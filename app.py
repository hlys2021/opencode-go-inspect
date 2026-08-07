import os
import io
import json
import pandas as pd
from typing import Dict, Any
from fastapi import FastAPI, BackgroundTasks, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request

import database
import crawler
import alert_engine

app = FastAPI(title="OpenCode Usage Monitor & Budget Alert")

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

@app.on_event("startup")
def startup_event():
    database.init_db()

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.get("/api/usage")
def get_usage_data():
    summary = database.get_usage_summary()
    recent_logs = database.get_recent_usage(limit=10000)
    config = alert_engine.load_config()
    alerts = database.get_recent_alerts(limit=5)
    return {
        "summary": summary,
        "recent_logs": recent_logs,
        "config": config,
        "alerts": alerts
    }

@app.get("/api/chart_data")
def get_chart_data(granularity: str = "daily"):
    """多粒度 (hourly, daily, weekly) 聚合图表数据"""
    return database.get_aggregated_tokens(granularity=granularity)

@app.post("/api/config")
def update_config(data: Dict[str, Any]):
    """手动动态修改配置（如修改月度预算数额）"""
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    current_config = alert_engine.load_config()

    if "monthly_budget_usd" in data:
        current_config["monthly_budget_usd"] = float(data["monthly_budget_usd"])
    if "daily_budget_usd" in data:
        current_config["daily_budget_usd"] = float(data["daily_budget_usd"])

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(current_config, f, indent=2, ensure_ascii=False)

    # 重新触发一次告警检测
    alert_engine.check_budget_alerts()
    return {"status": "ok", "config": current_config}

@app.post("/api/sync")
def trigger_sync(background_tasks: BackgroundTasks):
    """手动触发后台抓取"""
    def do_sync():
        crawler.run_crawler_job()
        alert_engine.check_budget_alerts()

    background_tasks.add_task(do_sync)
    return {"status": "ok", "message": "已在后台启动抓取同步"}

@app.get("/api/export")
def export_csv():
    """生成带有 UTF-8 BOM 的 CSV 导出文件"""
    records = database.get_recent_usage(limit=10000)
    if not records:
        return Response("记录为空", media_type="text/plain")

    df = pd.DataFrame(records)

    # 规范列名
    rename_dict = {
        "record_time": "时间",
        "model": "模型",
        "input_tokens": "输入Token",
        "output_tokens": "输出Token",
        "cost_str": "成本标识",
        "cost_usd": "成本(USD)",
        "session_id": "会话ID"
    }
    df = df.rename(columns=rename_dict)

    # 只选择展示需要的字段
    columns_to_keep = ["时间", "模型", "输入Token", "输出Token", "成本标识", "成本(USD)"]
    existing_cols = [c for c in columns_to_keep if c in df.columns]
    df = df[existing_cols]

    # 输出到 CSV 内存流
    stream = io.StringIO()
    # 写入 UTF-8 BOM
    stream.write('﻿')
    df.to_csv(stream, index=False)

    response = StreamingResponse(
        iter([stream.getvalue()]),
        media_type="text/csv"
    )
    response.headers["Content-Disposition"] = "attachment; filename=opencode_usage_export.csv"
    return response

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8088)
