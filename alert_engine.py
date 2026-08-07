import os
import json
from typing import Dict, Any, List
import database

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

def load_config() -> Dict[str, Any]:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def check_budget_alerts() -> List[Dict[str, Any]]:
    config = load_config()
    daily_budget = config.get("daily_budget_usd", 5.0)
    monthly_budget = config.get("monthly_budget_usd", 50.0)
    warn_ratio = config.get("alert_warning_ratio", 0.8)
    crit_ratio = config.get("alert_critical_ratio", 1.0)

    summary = database.get_usage_summary()
    total_cost = summary.get("total_cost", 0.0)

    alerts_triggered = []

    # 检查月度预算
    if total_cost >= monthly_budget * crit_ratio:
        msg = f"🚨 【严重告警】当前总使用额 (${total_cost:.4f}) 已达到/超过设定月度预算 (${monthly_budget:.2f})！"
        database.record_alert("monthly_critical", msg, total_cost, monthly_budget)
        alerts_triggered.append({"type": "monthly_critical", "message": msg})
    elif total_cost >= monthly_budget * warn_ratio:
        msg = f"⚠️ 【预警】当前总使用额 (${total_cost:.4f}) 已达到月度预算 (${monthly_budget:.2f}) 的 {int(warn_ratio*100)}%！"
        database.record_alert("monthly_warning", msg, total_cost, monthly_budget)
        alerts_triggered.append({"type": "monthly_warning", "message": msg})

    return alerts_triggered

if __name__ == "__main__":
    database.init_db()
    alerts = check_budget_alerts()
    print("预算告警检测结果:", alerts)
