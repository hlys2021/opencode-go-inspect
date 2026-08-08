import os
import json
import re
import time
from typing import List, Dict, Any
from datetime import datetime
from playwright.sync_api import sync_playwright
import database

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
STATUS_PATH = os.path.join(os.path.dirname(__file__), "crawler_status.json")

def load_config() -> Dict[str, Any]:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def get_crawler_status() -> Dict[str, Any]:
    default_status = {
        "is_crawling": False,
        "last_sync_time": "",
        "last_inserted_count": 0,
        "last_error": ""
    }
    if not os.path.exists(STATUS_PATH):
        return default_status
    try:
        with open(STATUS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default_status

def update_crawler_status(status_dict: Dict[str, Any]):
    current = get_crawler_status()
    current.update(status_dict)
    try:
        with open(STATUS_PATH, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[Crawler Status] 保存状态文件失败: {e}")

def parse_cost_to_usd(cost_str: str) -> float:
    """提取成本字符串中的数值，如 Go ($0.0016) -> 0.0016"""
    match = re.search(r"\$([\d\.]+)", cost_str)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return 0.0
    return 0.0

def fetch_opencode_usage() -> List[Dict[str, Any]]:
    config = load_config()
    target_url = config.get("target_url")
    headless = config.get("headless", True)

    existing_hashes = database.get_existing_hashes()
    print(f"[Crawler] 数据库中已有 {len(existing_hashes)} 条 Hash 记录，启动增量抓取模式...")

    records = []

    with sync_playwright() as p:
        user_data_dir = os.path.join(os.path.dirname(__file__), "user_data")
        browser_context = p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=headless,
            viewport={"width": 1400, "height": 1000},
            args=["--disable-blink-features=AutomationControlled"]
        )

        page = browser_context.pages[0] if browser_context.pages else browser_context.new_page()
        print(f"[Crawler] 正在打开页面: {target_url}")
        page.goto(target_url, wait_until="load", timeout=60000)

        # 检查是否需要用户手工登录
        if "login" in page.url or "auth" in page.url or page.locator("input[type='password']").count() > 0:
            print("\n" + "="*50)
            print("⚠️ 检测到需要登录！请在 config.json 中调整 headless: false 并在窗口中完成登录。")
            print("="*50 + "\n")
            try:
                page.wait_for_url(lambda u: "usage" in u or "workspace" in u, timeout=60000)
                print("✅ 登录成功！")
            except Exception as login_err:
                print(f"⚠️ 等待登录超时或跳过: {login_err}")

        time.sleep(3)

        def parse_current_page_dom() -> List[Dict[str, Any]]:
            page_records = []
            rows = page.query_selector_all("tr")
            for tr in rows:
                cells = tr.query_selector_all("td")
                if len(cells) < 4:
                    continue

                cell_texts = [c.inner_text().strip().replace("\n", " ") for c in cells]
                record_time = cell_texts[0]
                model = cell_texts[1]

                if "日期" in record_time or "DATE" in record_time or "模型" in model or "MODEL" in model:
                    continue

                def clean_int(val_str: str) -> int:
                    cleaned = re.sub(r"[^\d]", "", val_str)
                    return int(cleaned) if cleaned else 0

                input_tokens = clean_int(cell_texts[2])
                output_tokens = clean_int(cell_texts[3])
                cost_str = cell_texts[4] if len(cells) >= 5 else ""
                cost_usd = parse_cost_to_usd(cost_str)
                session_id = cell_texts[5] if len(cells) >= 6 else ""

                if record_time and model:
                    page_records.append({
                        "record_time": record_time,
                        "model": model,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "cost_str": cost_str,
                        "cost_usd": cost_usd,
                        "session_id": session_id
                    })
            return page_records

        # 循环翻页与解析
        max_pages = 100
        current_page_num = 1
        consecutive_duplicate_pages = 0

        while current_page_num <= max_pages:
            print(f"[Crawler] 正在解析第 {current_page_num} 页 DOM 数据...")
            dom_records = parse_current_page_dom()

            if not dom_records:
                print(f"[Crawler] 第 {current_page_num} 页数据为空，停止继续翻页。")
                break

            page_hashes = [
                database.generate_hash(
                    r["record_time"], r["model"], r["input_tokens"], r["output_tokens"], r["cost_str"]
                ) for r in dom_records
            ]

            all_exist_in_db = len(page_hashes) > 0 and all(h in existing_hashes for h in page_hashes)

            added_this_page = 0
            for r in dom_records:
                h = database.generate_hash(
                    r["record_time"], r["model"], r["input_tokens"], r["output_tokens"], r["cost_str"]
                )
                if h not in existing_hashes:
                    if not any(existing.get("record_time") == r.get("record_time") and existing.get("input_tokens") == r.get("input_tokens") and existing.get("cost_str") == r.get("cost_str") for existing in records):
                        records.append(r)
                        added_this_page += 1

            print(f"[Crawler] 第 {current_page_num} 页解析完成，新增 {added_this_page} 条使用记录")

            if all_exist_in_db:
                consecutive_duplicate_pages += 1
                if consecutive_duplicate_pages >= 1:
                    print(f"[Crawler] 第 {current_page_num} 页所有记录均已存在于数据库中，增量同步完成，提前停止后续翻页！")
                    break
            else:
                consecutive_duplicate_pages = 0

            btns = page.query_selector_all("button")
            next_btn = None
            if len(btns) >= 2:
                candidate = btns[-2]
                is_disabled = candidate.evaluate("el => el.disabled")
                if not is_disabled:
                    next_btn = candidate

            if next_btn:
                try:
                    next_btn.scroll_into_view_if_needed()
                    time.sleep(0.5)

                    first_cell = page.query_selector("td")
                    time_before = first_cell.inner_text().strip() if first_cell else ""

                    print(f"[Crawler] 正在点击跳转到第 {current_page_num + 1} 页...")
                    next_btn.click()
                    time.sleep(2.5)

                    first_cell_after = page.query_selector("td")
                    time_after = first_cell_after.inner_text().strip() if first_cell_after else ""

                    if time_before and time_before == time_after:
                        print("[Crawler] 点击后第一行数据没有发生变化，已到达最后一页。")
                        break

                    current_page_num += 1
                except Exception as e:
                    print(f"[Crawler] 翻页点击过程结束或出现阻碍: {e}")
                    break
            else:
                print("[Crawler] “下一页”按钮已被禁用或未找到，停止翻页。")
                break

        browser_context.close()

    print(f"[Crawler Summary] 全量抓取解析完成，共获取 {len(records)} 条历史记录！")
    return records

def run_crawler_job():
    status = get_crawler_status()
    if status.get("is_crawling"):
        print("[Crawler Job] 上一次抓取任务仍在运行中，本次触发跳过。")
        return

    start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    update_crawler_status({
        "is_crawling": True,
        "last_sync_time": start_time,
        "last_error": ""
    })

    database.init_db()
    print(f"[Crawler Job] [{start_time}] 开始抓取 OpenCode 使用量...")
    try:
        records = fetch_opencode_usage()
        print(f"[Crawler Job] 共解析出 {len(records)} 条历史使用记录")
        inserted = database.insert_usage_records(records)
        print(f"[Crawler Job] 成功新增/增量更新 {inserted} 条记录到 SQLite 数据库")
        update_crawler_status({
            "is_crawling": False,
            "last_inserted_count": inserted,
            "last_error": ""
        })
    except Exception as e:
        err_msg = str(e)
        print(f"[Crawler Job] 抓取出现错误: {err_msg}")
        update_crawler_status({
            "is_crawling": False,
            "last_error": err_msg
        })

if __name__ == "__main__":
    run_crawler_job()
