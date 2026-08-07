import sqlite3
import hashlib
import os
import re
from datetime import datetime
from typing import List, Dict, Any, Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "opencode_monitor.db")

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    # 1. 创建使用量日志表
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS usage_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        row_hash TEXT UNIQUE NOT NULL,
        record_time TEXT NOT NULL,
        model TEXT NOT NULL,
        input_tokens INTEGER DEFAULT 0,
        output_tokens INTEGER DEFAULT 0,
        cost_str TEXT,
        cost_usd REAL DEFAULT 0.0,
        session_id TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

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
        try:
            cursor.execute("""
                INSERT INTO usage_logs (row_hash, record_time, model, input_tokens, output_tokens, cost_str, cost_usd, session_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                h,
                r.get("record_time", ""),
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

def get_recent_usage(limit: int = 10000) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM usage_logs ORDER BY id DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_aggregated_tokens(granularity: str = "daily") -> Dict[str, Any]:
    """
    根据给定的粒度 (hourly, daily, weekly) 聚合查询累计 Token 使用量与成本
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT record_time, input_tokens, output_tokens, cost_usd FROM usage_logs ORDER BY id ASC")
    rows = cursor.fetchall()
    conn.close()

    aggregated = {}

    for r in rows:
        rec_time = r["record_time"] # 格式如 "8月7日 下午5:38" 或 ISO
        total_tok = (r["input_tokens"] or 0) + (r["output_tokens"] or 0)
        cost = r["cost_usd"] or 0.0

        # 解析时间分组键
        key = "未知时间"
        match = re.search(r"(\d+)月(\d+)日(?:\s+(上午|下午)(\d+):(\d+))?", rec_time)
        if match:
            month = int(match.group(1))
            day = int(match.group(2))
            ampm = match.group(3)
            hour_str = match.group(4)

            hour = 0
            if hour_str:
                hour = int(hour_str)
                if ampm == "下午" and hour < 12:
                    hour += 12
                elif ampm == "上午" and hour == 12:
                    hour = 0

            # 生成规范化标准 Key
            if granularity == "hourly":
                key = f"{month:02d}-{day:02d} {hour:02d}:00"
            elif granularity == "weekly":
                # 按月与周分组
                week_num = (day - 1) // 7 + 1
                key = f"{month:02d}月第{week_num}周"
            else:  # daily
                key = f"{month:02d}-{day:02d}"
        else:
            # 如果已有 ISO 类似前缀格式
            key = rec_time[:10]

        if key not in aggregated:
            aggregated[key] = {"tokens": 0, "cost": 0.0, "count": 0}
        aggregated[key]["tokens"] += total_tok
        aggregated[key]["cost"] += cost
        aggregated[key]["count"] += 1

    labels = list(aggregated.keys())
    tokens_data = [aggregated[k]["tokens"] for k in labels]
    cost_data = [round(aggregated[k]["cost"], 4) for k in labels]

    # 对标签进行正向排序，保证按时间从远到近展示
    combined = sorted(zip(labels, tokens_data, cost_data), key=lambda x: x[0])
    if combined:
        labels, tokens_data, cost_data = zip(*combined)
        labels = list(labels)
        tokens_data = list(tokens_data)
        cost_data = list(cost_data)
    else:
        labels, tokens_data, cost_data = [], [], []

    return {
        "labels": labels,
        "tokens_data": tokens_data,
        "cost_data": cost_data
    }

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

    return {
        "total_records": total_row["total_records"] or 0,
        "total_input": total_row["total_input"] or 0,
        "total_output": total_row["total_output"] or 0,
        "total_tokens": total_row["total_tokens"] or 0,
        "total_cost": round(total_row["total_cost"] or 0.0, 4),
        "models": [dict(r) for r in model_rows]
    }

def record_alert(alert_type: str, message: str, current_amount: float, budget_amount: float):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO alert_logs (alert_type, message, current_amount, budget_amount)
        VALUES (?, ?, ?, ?)
    """, (alert_type, message, current_amount, budget_amount))
    conn.commit()
    conn.close()

def get_recent_alerts(limit: int = 20) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM alert_logs ORDER BY id DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

if __name__ == "__main__":
    init_db()
    print("SQLite 数据库初始化完成:", DB_PATH)
