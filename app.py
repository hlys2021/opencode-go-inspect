import os
import io
import json
import pandas as pd
import sys
import subprocess
from contextlib import asynccontextmanager
from typing import Dict, Any
from fastapi import FastAPI, BackgroundTasks, Response
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request

import database
import crawler
import alert_engine

DEFAULT_SERVER_HOST = "127.0.0.1"
DEFAULT_SERVER_PORT = 18088

def get_server_port() -> int:
    """读取 Web 服务端口，避开 Windows 常见系统排除端口范围。"""
    raw_port = os.environ.get("OPENCODE_MONITOR_PORT", str(DEFAULT_SERVER_PORT)).strip()
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ValueError("OPENCODE_MONITOR_PORT 必须是 1-65535 之间的整数") from exc
    if not 1 <= port <= 65535:
        raise ValueError("OPENCODE_MONITOR_PORT 必须是 1-65535 之间的整数")
    return port

@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_db()
    yield

app = FastAPI(title="OpenCode Usage Monitor & Budget Alert", lifespan=lifespan)

@app.middleware("http")
async def add_no_cache_header(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.get("/api/usage")
def get_usage_data(page: int = 1, page_size: int = 20):
    page = max(1, page)
    page_size = min(max(1, page_size), 200)
    summary = database.get_usage_summary()
    recent_logs = database.get_recent_usage(limit=page_size, offset=(page - 1) * page_size)
    total_count = database.get_usage_count()
    config = alert_engine.load_config()
    alerts = database.get_recent_alerts(limit=5)
    return {
        "summary": summary,
        "recent_logs": recent_logs,
        "total_count": total_count,
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

    try:
        if "monthly_budget_usd" in data:
            value = float(data["monthly_budget_usd"])
            if value <= 0:
                raise ValueError("monthly budget must be positive")
            current_config["monthly_budget_usd"] = value
        if "daily_budget_usd" in data:
            value = float(data["daily_budget_usd"])
            if value <= 0:
                raise ValueError("daily budget must be positive")
            current_config["daily_budget_usd"] = value
    except (TypeError, ValueError):
        return JSONResponse(
            {"status": "error", "message": "预算金额必须是大于 0 的数字"},
            status_code=400,
        )

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(current_config, f, indent=2, ensure_ascii=False)

    # 重新触发一次告警检测
    alert_engine.check_budget_alerts()
    return {"status": "ok", "config": current_config}

@app.get("/api/sync_status")
def get_sync_status():
    """获取抓取同步运行状态"""
    return crawler.get_crawler_status()

@app.post("/api/sync")
def trigger_sync(background_tasks: BackgroundTasks):
    """手动触发后台抓取"""
    crawler.clear_stale_lock()
    status = crawler.get_crawler_status()
    if status.get("is_crawling"):
        return {"status": "busy", "message": "已有一个抓取任务在后台运行中..."}

    def do_sync():
        crawler_script = os.path.join(os.path.dirname(__file__), "crawler.py")
        subprocess.run([sys.executable, crawler_script])
        alert_engine.check_budget_alerts()

    background_tasks.add_task(do_sync)
    return {"status": "ok", "message": "已在后台启动抓取同步"}

@app.get("/api/export")
def export_csv():
    """生成带有 UTF-8 BOM 的 CSV 导出文件"""
    records = database.get_all_usage()
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
    uvicorn.run(app, host=DEFAULT_SERVER_HOST, port=get_server_port())
