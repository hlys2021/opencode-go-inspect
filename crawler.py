import os
import json
import re
import time
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright
import database

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
STATUS_PATH = os.path.join(os.path.dirname(__file__), "crawler_status.json")
STALE_LOCK_TIMEOUT_SECONDS = 90 * 60

def load_config() -> Dict[str, Any]:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def get_crawler_status() -> Dict[str, Any]:
    default_status = {
        "is_crawling": False,
        "last_sync_time": "",
        "last_inserted_count": 0,
        "last_error": "",
        "last_go_sync_time": "",
        "last_go_error": "",
        "started_at": ""
    }
    if not os.path.exists(STATUS_PATH):
        return default_status
    try:
        with open(STATUS_PATH, "r", encoding="utf-8") as f:
            status = json.load(f)
    except Exception:
        return default_status
    # 状态锁超时未复位视为陈旧锁，按未在抓取处理
    if status.get("is_crawling") and not _lock_is_fresh(status.get("started_at", "")):
        status["is_crawling"] = False
        status["is_stale_lock"] = True
    return status

def _parse_status_time(time_str: str) -> Optional[datetime]:
    if not time_str:
        return None
    try:
        return datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None

def _lock_is_fresh(started_at: str) -> bool:
    started = _parse_status_time(started_at)
    if started is None:
        return False
    return (datetime.now() - started).total_seconds() < STALE_LOCK_TIMEOUT_SECONDS

def clear_stale_lock() -> None:
    """复位被异常中断（进程被杀/断电）遗留的抓取状态锁"""
    if not os.path.exists(STATUS_PATH):
        return
    try:
        with open(STATUS_PATH, "r", encoding="utf-8") as f:
            status = json.load(f)
    except Exception:
        return
    if status.get("is_crawling") and not _lock_is_fresh(status.get("started_at", "")):
        update_crawler_status({
            "is_crawling": False,
            "started_at": "",
            "last_error": "检测到上次抓取进程异常退出，已自动复位陈旧状态锁"
        })
        print("[Crawler] 已自动复位陈旧抓取状态锁")

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

def get_go_url(config: Optional[Dict[str, Any]] = None) -> str:
    """读取 Go 套餐页面地址，未配置时从使用量页面自动推导。"""
    config = config or load_config()
    configured_url = str(config.get("go_url") or "").strip()
    if configured_url:
        return configured_url

    target_url = str(config.get("target_url") or "").rstrip("/")
    if target_url.endswith("/usage"):
        return target_url[:-len("/usage")] + "/go"
    return target_url + "/go" if target_url else ""

def _parse_percentage(value_text: str) -> Optional[float]:
    match = re.search(r"(-?\d+(?:\.\d+)?)\s*%", value_text or "")
    if not match:
        return None
    try:
        return max(0.0, min(100.0, float(match.group(1))))
    except ValueError:
        return None

def _parse_reset_delta(reset_text: str) -> Optional[timedelta]:
    """解析页面的相对重置时间，如 '3 days 19 hours'。"""
    text = (reset_text or "").lower()
    units = {
        "second": "seconds", "seconds": "seconds", "秒": "seconds",
        "minute": "minutes", "minutes": "minutes", "分钟": "minutes",
        "hour": "hours", "hours": "hours", "小时": "hours",
        "day": "days", "days": "days", "天": "days",
        "week": "weeks", "weeks": "weeks", "周": "weeks",
    }
    total_seconds = 0
    pattern = r"(\d+(?:\.\d+)?)\s*(seconds?|minutes?|hours?|days?|weeks?|秒|分钟|小时|天|周)"
    for match in re.finditer(pattern, text):
        unit = units.get(match.group(2))
        if unit:
            total_seconds += int(float(match.group(1)) * {
                "seconds": 1,
                "minutes": 60,
                "hours": 3600,
                "days": 86400,
                "weeks": 604800,
            }[unit])
    return timedelta(seconds=total_seconds) if total_seconds > 0 else None

def fetch_go_usage_snapshot() -> Dict[str, Any]:
    """抓取 Go 页面上的滚动、每周、每月额度和相对/预计重置时间。"""
    config = load_config()
    source_url = get_go_url(config)
    if not source_url:
        raise RuntimeError("未配置 OpenCode Go 页面地址")

    headless = config.get("headless", True)
    fetched_at_dt = datetime.now()
    fetched_at = fetched_at_dt.strftime("%Y-%m-%d %H:%M:%S")
    result: Dict[str, Any] = {
        "fetched_at": fetched_at,
        "source_url": source_url,
        "rolling": {},
        "weekly": {},
        "monthly": {},
    }

    label_map = {
        "rolling usage": "rolling",
        "weekly usage": "weekly",
        "monthly usage": "monthly",
        "滚动用量": "rolling",
        "每周用量": "weekly",
        "每月用量": "monthly",
    }

    with sync_playwright() as p:
        user_data_dir = os.path.join(os.path.dirname(__file__), "user_data")
        browser_context = p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=headless,
            viewport={"width": 1400, "height": 1000},
            args=["--disable-blink-features=AutomationControlled"]
        )
        try:
            page = browser_context.pages[0] if browser_context.pages else browser_context.new_page()
            print(f"[Crawler] 正在打开 Go 页面: {source_url}")
            page.goto(source_url, wait_until="domcontentloaded", timeout=60000)
            if "login" in page.url.lower() or "auth" in page.url.lower():
                raise RuntimeError("Go 页面需要登录，请先以 headless=false 完成登录")

            try:
                page.wait_for_selector('[data-slot="usage-item"]', timeout=30000)
            except Exception as exc:
                body_text = page.locator("body").inner_text(timeout=10000)
                if "OpenCode Go" not in body_text and "Rolling Usage" not in body_text:
                    raise RuntimeError("未找到 Go 套餐用量区域，页面可能未登录或结构已变化") from exc

            items = page.locator('[data-slot="usage-item"]')
            for index in range(items.count()):
                item = items.nth(index)
                label = item.locator('[data-slot="usage-label"]').inner_text().strip()
                metric_name = label_map.get(label.lower())
                if not metric_name:
                    continue
                value_text = item.locator('[data-slot="usage-value"]').inner_text().strip()
                reset_text = item.locator('[data-slot="reset-time"]').inner_text().strip()
                percentage = _parse_percentage(value_text)
                reset_text = re.sub(r"^resets\s+in\s*", "", reset_text, flags=re.IGNORECASE).strip()
                reset_delta = _parse_reset_delta(reset_text)
                result[metric_name] = {
                    "percentage": percentage,
                    "reset_text": reset_text,
                    "reset_at": (fetched_at_dt + reset_delta).strftime("%Y-%m-%d %H:%M:%S") if reset_delta else "",
                }
        finally:
            browser_context.close()

    missing = [name for name in ("rolling", "weekly", "monthly") if result[name].get("percentage") is None]
    if missing:
        raise RuntimeError(f"Go 页面缺少用量字段: {', '.join(missing)}")
    print(
        "[Crawler] Go 用量抓取完成: "
        + ", ".join(f"{name}={result[name]['percentage']}%" for name in ("rolling", "weekly", "monthly"))
    )
    return result

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
                print(f"[Crawler] 第 {current_page_num} 页所有记录均已存在于数据库中，增量同步完成，提前停止后续翻页！")
                break

            next_btn = _find_next_button(page)

            if next_btn:
                try:
                    first_row_before = _first_row_signature(page)
                    next_btn.scroll_into_view_if_needed()
                    time.sleep(0.5)

                    print(f"[Crawler] 正在点击跳转到第 {current_page_num + 1} 页...")
                    next_btn.click()
                    time.sleep(2.5)

                    first_row_after = _first_row_signature(page)
                    if first_row_before and first_row_before == first_row_after:
                        print("[Crawler] 点击后首行数据未变化，已到达最后一页。")
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

def _first_row_signature(page) -> str:
    """取当前页面第一条有效数据行的签名，用于判断翻页后内容是否变化"""
    rows = page.query_selector_all("tr")
    for tr in rows:
        cells = tr.query_selector_all("td")
        if len(cells) < 4:
            continue
        texts = [c.inner_text().strip() for c in cells[:5]]
        if any(texts):
            return "|".join(texts)
    return ""

def _find_next_button(page):
    """优先按文案/aria-label 定位“下一页”，兼容原页面倒数第二个按钮的结构"""
    btns = page.query_selector_all("button")
    for b in btns:
        try:
            text = (b.inner_text() or "").strip()
            label = (b.get_attribute("aria-label") or "").strip()
        except Exception:
            continue
        if any(k in text for k in ("下一页", "下页", "Next", "next")) or "next" in label.lower():
            try:
                if not b.evaluate("el => el.disabled"):
                    return b
            except Exception:
                pass
    if len(btns) >= 2:
        candidate = btns[-2]
        try:
            if not candidate.evaluate("el => el.disabled"):
                return candidate
        except Exception:
            pass
    return None

def run_crawler_job():
    clear_stale_lock()
    status = get_crawler_status()
    if status.get("is_crawling"):
        print("[Crawler Job] 上一次抓取任务仍在运行中，本次触发跳过。")
        return

    start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    update_crawler_status({
        "is_crawling": True,
        "started_at": start_time,
        "last_sync_time": start_time,
        "last_error": ""
    })

    database.init_db()
    print(f"[Crawler Job] [{start_time}] 开始抓取 OpenCode 使用量...")
    inserted = 0
    usage_error = ""
    go_error = ""
    try:
        records = fetch_opencode_usage()
        print(f"[Crawler Job] 共解析出 {len(records)} 条历史使用记录")
        inserted = database.insert_usage_records(records)
        print(f"[Crawler Job] 成功新增/增量更新 {inserted} 条记录到 SQLite 数据库")
    except Exception as e:
        usage_error = str(e)
        print(f"[Crawler Job] 抓取出现错误: {usage_error}")
        update_crawler_status({"last_error": usage_error})

    try:
        go_snapshot = fetch_go_usage_snapshot()
        database.insert_go_usage_snapshot(go_snapshot)
        go_sync_time = go_snapshot.get("fetched_at", "")
        print(f"[Crawler Job] Go 用量快照已保存: {go_sync_time}")
    except Exception as e:
        go_error = str(e)
        print(f"[Crawler Job] Go 用量抓取出现错误: {go_error}")

    update_crawler_status({
        "is_crawling": False,
        "started_at": "",
        "last_inserted_count": inserted,
        "last_go_sync_time": go_sync_time if not go_error else get_crawler_status().get("last_go_sync_time", ""),
        "last_go_error": go_error,
        "last_error": "；".join(error for error in (usage_error, go_error) if error)
    })

if __name__ == "__main__":
    run_crawler_job()
