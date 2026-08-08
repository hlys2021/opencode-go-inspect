import sqlite3
import hashlib
import os
import re
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "opencode_monitor.db")

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def parse_to_standard_time(rec_time: str, default_year: Optional[int] = None) -> str:
    """
    将中文时间字符串（如 '8月8日 上午12:08'）转换为可精准排序的标准 ISO 时间（如 '2026-08-08 00:08:00'）
    规则：
    - '上午12:xx' -> 00:xx (凌晨)
    - '上午01:xx'~'上午11:xx' -> 01:xx~11:xx
    - '下午12:xx' -> 12:xx (中午)
    - '下午01:xx'~'下午11:xx' -> 13:xx~23:xx
    """
    if not rec_time:
        return ""

    match = re.search(r"(\d+)月(\d+)日(?:\s+(上午|下午)(\d+):(\d+))?", rec_time)
    if not match:
        return rec_time

    month = int(match.group(1))
    day = int(match.group(2))
    year = default_year if default_year is not None else datetime.now().year
    ampm = match.group(3)
    hour_str = match.group(4)
    minute_str = match.group(5)

    hour = 0
    minute = 0
    if hour_str and minute_str:
        h = int(hour_str)
        minute = int(minute_str)
        if ampm == "下午":
            hour = h if h == 12 else h + 12
        elif ampm == "上午":
            hour = 0 if h == 12 else h

    return f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:00"

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    # 1. 创建使用量日志表
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS usage_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        row_hash TEXT UNIQUE NOT NULL,
        record_time TEXT NOT NULL,
        parsed_time TEXT,
        model TEXT NOT NULL,
        input_tokens INTEGER DEFAULT 0,
        output_tokens INTEGER DEFAULT 0,
        cost_str TEXT,
        cost_usd REAL DEFAULT 0.0,
        session_id TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # 兼容老表补全 parsed_time 字段
    cursor.execute("PRAGMA table_info(usage_logs)")
    columns = [row["name"] for row in cursor.fetchall()]
    if "parsed_time" not in columns:
        cursor.execute("ALTER TABLE usage_logs ADD COLUMN parsed_time TEXT")

    # 补全旧记录中 parsed_time 为 NULL 的数据
    cursor.execute("SELECT id, record_time FROM usage_logs WHERE parsed_time IS NULL OR parsed_time = ''")
    unparsed_rows = cursor.fetchall()
    if unparsed_rows:
        for r in unparsed_rows:
            p_time = parse_to_standard_time(r["record_time"])
            cursor.execute("UPDATE usage_logs SET parsed_time = ? WHERE id = ?", (p_time, r["id"]))
        conn.commit()

    # 2. 创建预算告警历史表
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS alert_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        alert_type TEXT NOT NULL,  -- 'daily_warning', 'daily_critical', 'monthly_warning', 'monthly_critical'
        message TEXT NOT NULL,
        current_amount REAL NOT NULL,
        budget_amount REAL NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.commit()
    conn.close()

def get_existing_hashes() -> set:
    """获取数据库中已存在的所有记录 hash"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT row_hash FROM usage_logs")
    rows = cursor.fetchall()
    conn.close()
    return {r["row_hash"] for r in rows}

def generate_hash(record_time: str, model: str, input_tokens: int, output_tokens: int, cost_str: str) -> str:
    raw = f"{record_time}_{model}_{input_tokens}_{output_tokens}_{cost_str}"
    return hashlib.md5(raw.encode('utf-8')).hexdigest()

def insert_usage_records(records: List[Dict[str, Any]]) -> int:
    """批量插入使用记录，自动去重，返回新插入的行数"""
    conn = get_db_connection()
    cursor = conn.cursor()
    inserted_count = 0

    for r in records:
        h = generate_hash(
            r.get("record_time", ""),
            r.get("model", ""),
            r.get("input_tokens", 0),
            r.get("output_tokens", 0),
            r.get("cost_str", "")
        )
        rec_time = r.get("record_time", "")
        p_time = parse_to_standard_time(rec_time)

        try:
            cursor.execute("""
                INSERT INTO usage_logs (row_hash, record_time, parsed_time, model, input_tokens, output_tokens, cost_str, cost_usd, session_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                h,
                rec_time,
                p_time,
                r.get("model", ""),
                r.get("input_tokens", 0),
                r.get("output_tokens", 0),
                r.get("cost_str", ""),
                r.get("cost_usd", 0.0),
                r.get("session_id", "")
            ))
            inserted_count += 1
        except sqlite3.IntegrityError:
            # Hash 已存在，忽略重复数据
            pass

    conn.commit()
    conn.close()
    return inserted_count

def get_recent_usage(limit: int = 10000, offset: int = 0) -> List[Dict[str, Any]]:
    """按标准化真实时间倒序查询最新的使用记录"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM usage_logs ORDER BY parsed_time DESC, id DESC LIMIT ? OFFSET ?",
        (limit, offset),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_usage_count() -> int:
    """获取使用记录总数"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM usage_logs")
    row = cursor.fetchone()
    conn.close()
    return row[0] or 0

def get_all_usage() -> List[Dict[str, Any]]:
    """导出全部使用记录"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM usage_logs ORDER BY parsed_time DESC, id DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_cost_between(start_time: str, end_time: str) -> float:
    """统计 [start_time, end_time) 区间内的成本合计"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COALESCE(SUM(cost_usd), 0.0) as cost FROM usage_logs WHERE parsed_time >= ? AND parsed_time < ?",
        (start_time, end_time),
    )
    row = cursor.fetchone()
    conn.close()
    return row["cost"] or 0.0

def get_current_period_costs() -> Dict[str, float]:
    """按 parsed_time 统计当月与当日成本，作为月度/日度预算告警基准"""
    now = datetime.now()
    month_start = f"{now.year:04d}-{now.month:02d}-01 00:00:00"
    if now.month == 12:
        month_end = f"{now.year + 1:04d}-01-01 00:00:00"
    else:
        month_end = f"{now.year:04d}-{now.month + 1:02d}-01 00:00:00"
    day_start = now.strftime("%Y-%m-%d 00:00:00")
    day_end = (now + timedelta(days=1)).strftime("%Y-%m-%d 00:00:00")
    return {
        "monthly_cost": get_cost_between(month_start, month_end),
        "daily_cost": get_cost_between(day_start, day_end),
    }

def get_aggregated_tokens(granularity: str = "daily") -> Dict[str, Any]:
    """
    根据给定的粒度 (hourly, daily, weekly) 聚合查询累计 Token 使用量与成本
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT record_time, parsed_time, input_tokens, output_tokens, cost_usd FROM usage_logs ORDER BY parsed_time ASC, id ASC")
    rows = cursor.fetchall()
    conn.close()

    aggregated = {}

    for r in rows:
        p_time = r["parsed_time"] or parse_to_standard_time(r["record_time"])
        total_tok = (r["input_tokens"] or 0) + (r["output_tokens"] or 0)
        cost = r["cost_usd"] or 0.0

        # 从标准时间 YYYY-MM-DD HH:MM:SS 中提取 key
        key = "未知时间"
        if p_time and len(p_time) >= 16:
            month_day = p_time[5:10] # MM-DD
            hour = p_time[11:13]     # HH
            day_num = int(p_time[8:10])

            if granularity == "hourly":
                key = f"{month_day} {hour}:00"
            elif granularity == "weekly":
                key = _weekly_key(p_time)
            else:  # daily
                key = month_day
        else:
            key = r["record_time"][:10]

        if key not in aggregated:
            aggregated[key] = {"tokens": 0, "cost": 0.0, "count": 0}
        aggregated[key]["tokens"] += total_tok
        aggregated[key]["cost"] += cost
        aggregated[key]["count"] += 1

    labels = list(aggregated.keys())
    tokens_data = [aggregated[k]["tokens"] for k in labels]
    cost_data = [round(aggregated[k]["cost"], 4) for k in labels]

    return {
        "labels": labels,
        "tokens_data": tokens_data,
        "cost_data": cost_data
    }

def _weekly_key(p_time: str) -> str:
    """按 ISO 自然周聚合，如 '2026年第32周'；无法解析时回退为近似周"""
    try:
        dt = datetime.strptime(p_time, "%Y-%m-%d %H:%M:%S")
        iso_year, iso_week, _ = dt.isocalendar()
        return f"{iso_year}年第{iso_week}周"
    except ValueError:
        day_num = int(p_time[8:10])
        week_num = (day_num - 1) // 7 + 1
        return f"{p_time[5:7]}月第{week_num}周"

def get_usage_summary() -> Dict[str, Any]:
    """获取使用量汇总信息"""
    conn = get_db_connection()
    cursor = conn.cursor()

    # 统计总量
    cursor.execute("""
        SELECT
            COUNT(*) as total_records,
            SUM(input_tokens) as total_input,
            SUM(output_tokens) as total_output,
            SUM(input_tokens + output_tokens) as total_tokens,
            SUM(cost_usd) as total_cost
        FROM usage_logs
    """)
    total_row = cursor.fetchone()

    # 按模型汇总
    cursor.execute("""
        SELECT
            model,
            SUM(input_tokens) as input_tokens,
            SUM(output_tokens) as output_tokens,
            SUM(input_tokens + output_tokens) as total_tokens,
            SUM(cost_usd) as cost_usd,
            COUNT(*) as count
        FROM usage_logs
        GROUP BY model
    """)
    model_rows = cursor.fetchall()

    conn.close()

    period_costs = get_current_period_costs()

    return {
        "total_records": total_row["total_records"] or 0,
        "total_input": total_row["total_input"] or 0,
        "total_output": total_row["total_output"] or 0,
        "total_tokens": total_row["total_tokens"] or 0,
        "total_cost": round(total_row["total_cost"] or 0.0, 4),
        "monthly_cost": round(period_costs["monthly_cost"], 4),
        "daily_cost": round(period_costs["daily_cost"], 4),
        "models": [dict(r) for r in model_rows]
    }

def record_alert(alert_type: str, message: str, current_amount: float, budget_amount: float):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO alert_logs (alert_type, message, current_amount, budget_amount, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (alert_type, message, current_amount, budget_amount, now_str))
    conn.commit()
    conn.close()

def get_last_alert(alert_type: str) -> Optional[Dict[str, Any]]:
    """获取指定类型最近一次告警记录"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM alert_logs WHERE alert_type = ? ORDER BY id DESC LIMIT 1",
        (alert_type,),
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_recent_alerts(limit: int = 20) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM alert_logs ORDER BY id DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

if __name__ == "__main__":
    init_db()
    print("SQLite 数据库初始化与字段修复完成:", DB_PATH)
