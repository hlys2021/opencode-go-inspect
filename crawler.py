import os
import json
import re
import time
from typing import List, Dict, Any
from playwright.sync_api import sync_playwright
import database

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

def load_config() -> Dict[str, Any]:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

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
    headless = config.get("headless", False)

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
            print("⚠️ 检测到需要登录！请在弹出的浏览器窗口中完成 OpenCode 账号登录。")
            print("登录完成后脚本将自动继续抓取...")
            print("="*50 + "\n")
            try:
                page.wait_for_url(lambda u: "usage" in u or "workspace" in u, timeout=120000)
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

        # 循环翻页与全量解析
        max_pages = 100
        current_page_num = 1

        while current_page_num <= max_pages:
            print(f"[Crawler] 正在解析第 {current_page_num} 页 DOM 数据...")
            dom_records = parse_current_page_dom()

            # 将当前页的记录去重合并到全局列表
            added_this_page = 0
            for r in dom_records:
                if not any(existing.get("record_time") == r.get("record_time") and existing.get("input_tokens") == r.get("input_tokens") and existing.get("cost_str") == r.get("cost_str") for existing in records):
                    records.append(r)
                    added_this_page += 1

            print(f"[Crawler] 第 {current_page_num} 页解析完成，新增 {added_this_page} 条使用记录")

            # 查找分页器“下一页”按钮
            btns = page.query_selector_all("button")
            next_btn = None
            if len(btns) >= 2:
                # 倒数第 2 个通常为 > 按钮，倒数第 1 个是语言切换框
                candidate = btns[-2]
                is_disabled = candidate.evaluate("el => el.disabled")
                if not is_disabled:
                    next_btn = candidate

            if next_btn:
                try:
                    # 先将按钮滚动到可视区域内
                    next_btn.scroll_into_view_if_needed()
                    time.sleep(0.5)

                    first_cell = page.query_selector("td")
                    time_before = first_cell.inner_text().strip() if first_cell else ""

                    print(f"[Crawler] 正在点击跳转到第 {current_page_num + 1} 页...")
                    next_btn.click()
                    time.sleep(2.5)

                    first_cell_after = page.query_selector("td")
                    time_after = first_cell_after.inner_text().strip() if first_cell_after else ""

                    # 如果点击后第一行的单元格内容完全未发生改变，说明已到末页
                    if time_before and time_before == time_after:
                        print("[Crawler] 点击后第一行数据没有发生变化，已到达最后一页。")
                        break

                    current_page_num += 1
                except Exception as e:
                    print(f"[Crawler] 翻页点击过程结束或出现阻碍: {e}")
                    break
            else:
                print("[Crawler] “下一页”按钮已被禁用，已成功抓取完全部历史页面。")
                break

        browser_context.close()

    print(f"[Crawler Summary] 全量抓取解析完成，共获取 {len(records)} 条历史记录！")
    return records

def run_crawler_job():
    database.init_db()
    print("[Crawler Job] 开始全面全量抓取 OpenCode 使用量...")
    try:
        records = fetch_opencode_usage()
        print(f"[Crawler Job] 共解析出 {len(records)} 条全量历史使用记录")
        inserted = database.insert_usage_records(records)
        print(f"[Crawler Job] 成功新增/增量更新 {inserted} 条记录到 SQLite 数据库")
    except Exception as e:
        print(f"[Crawler Job] 抓取出现错误: {e}")

if __name__ == "__main__":
    run_crawler_job()
