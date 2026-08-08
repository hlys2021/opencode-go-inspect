import os
import json
from datetime import datetime
from typing import Dict, Any, List
import database

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
ALERT_COOLDOWN_SECONDS = 24 * 60 * 60

def load_config() -> Dict[str, Any]:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def _alert_on_cooldown(alert_type: str) -> bool:
    """同一类型告警 24 小时内不重复记录，避免每次同步都刷屏"""
    last = database.get_last_alert(alert_type)
    if not last:
        return False
    created = last.get("created_at") or ""
    try:
        created_ts = datetime.strptime(created, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return False
    return (datetime.now() - created_ts).total_seconds() < ALERT_COOLDOWN_SECONDS

def check_budget_alerts() -> List[Dict[str, Any]]:
    config = load_config()
    daily_budget = config.get("daily_budget_usd", 5.0)
    monthly_budget = config.get("monthly_budget_usd", 50.0)
    warn_ratio = config.get("alert_warning_ratio", 0.8)
    crit_ratio = config.get("alert_critical_ratio", 1.0)

    period_costs = database.get_current_period_costs()
    monthly_cost = period_costs.get("monthly_cost", 0.0)
    daily_cost = period_costs.get("daily_cost", 0.0)

    alerts_triggered = []

    # 检查当月预算
    if monthly_cost >= monthly_budget * crit_ratio:
        msg = f"🚨 【严重告警】当月已用 (${monthly_cost:.4f})，已达到/超过月度预算 (${monthly_budget:.2f})！"
        if not _alert_on_cooldown("monthly_critical"):
            database.record_alert("monthly_critical", msg, monthly_cost, monthly_budget)
            alerts_triggered.append({"type": "monthly_critical", "message": msg})
    elif monthly_cost >= monthly_budget * warn_ratio:
        msg = f"⚠️ 【预警】当月已用 (${monthly_cost:.4f})，已达到月度预算 (${monthly_budget:.2f}) 的 {int(warn_ratio*100)}%！"
        if not _alert_on_cooldown("monthly_warning"):
            database.record_alert("monthly_warning", msg, monthly_cost, monthly_budget)
            alerts_triggered.append({"type": "monthly_warning", "message": msg})

    # 检查当日预算
    if daily_cost >= daily_budget * crit_ratio:
        msg = f"🚨 【严重告警】今日已用 (${daily_cost:.4f})，已达到/超过日度预算 (${daily_budget:.2f})！"
        if not _alert_on_cooldown("daily_critical"):
            database.record_alert("daily_critical", msg, daily_cost, daily_budget)
            alerts_triggered.append({"type": "daily_critical", "message": msg})
    elif daily_cost >= daily_budget * warn_ratio:
        msg = f"⚠️ 【预警】今日已用 (${daily_cost:.4f})，已达到日度预算 (${daily_budget:.2f}) 的 {int(warn_ratio*100)}%！"
        if not _alert_on_cooldown("daily_warning"):
            database.record_alert("daily_warning", msg, daily_cost, daily_budget)
            alerts_triggered.append({"type": "daily_warning", "message": msg})

    return alerts_triggered

if __name__ == "__main__":
    database.init_db()
    alerts = check_budget_alerts()
    print("预算告警检测结果:", alerts)
