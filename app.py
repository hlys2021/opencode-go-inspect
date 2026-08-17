import os
import io
import json
import pandas as pd
import sys
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Callable, Dict, Any, Optional
from fastapi import FastAPI, BackgroundTasks, Response
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request

import database
import crawler
import alert_engine

DEFAULT_SERVER_HOST = "127.0.0.1"
DEFAULT_SERVER_PORT = 18088
SERVER_STARTED_AT = datetime.now()
_server_shutdown_callback: Optional[Callable[[], None]] = None

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

def set_server_shutdown_callback(callback: Callable[[], None]) -> None:
    """由运行入口注入 Uvicorn 的优雅停止回调。"""
    global _server_shutdown_callback
    _server_shutdown_callback = callback

def _request_server_shutdown() -> None:
    callback = _server_shutdown_callback
    if callback is not None:
        callback()
        return
    # 直接执行 app.py 时没有外层 Server 对象，延迟退出以便响应先返回。
    threading.Timer(0.2, lambda: os._exit(0)).start()

def _hidden_python_executable() -> str:
    """优先使用 pythonw，保证重启后的后端不弹出控制台窗口。"""
    current = Path(sys.executable)
    if os.name == "nt":
        pythonw = current.with_name("pythonw.exe")
        if pythonw.exists():
            return str(pythonw)
    return sys.executable

def _console_python_executable() -> str:
    """为爬虫选择带控制台的 Python 解释器，避免 pythonw 缺少标准流。"""
    current = Path(sys.executable)
    if os.name == "nt" and current.name.lower() == "pythonw.exe":
        candidate = current.with_name("python.exe")
        if candidate.exists():
            return str(candidate)
    return sys.executable

def _hidden_process_options() -> Dict[str, Any]:
    if os.name != "nt":
        return {}
    flags = (
        getattr(subprocess, "CREATE_NO_WINDOW", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
    )
    return {"creationflags": flags}

def _run_hidden_crawler(crawler_script: str) -> None:
    options = _hidden_process_options()
    options.update({
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    })
    subprocess.run([_console_python_executable(), crawler_script], **options)

def _spawn_delayed_backend_restart() -> None:
    """在旧进程释放端口后，通过独立隐藏辅助器启动新服务。"""
    launcher_path = Path(__file__).with_name("backend_launcher.py")
    options = _hidden_process_options()
    options.update({
        "cwd": str(launcher_path.parent),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    })
    subprocess.Popen([_hidden_python_executable(), "-X", "utf8", str(launcher_path)], **options)

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
    go_usage = database.get_latest_go_usage_snapshot()
    go_usage_status = crawler.get_crawler_status()
    return {
        "summary": summary,
        "recent_logs": recent_logs,
        "total_count": total_count,
        "config": config,
        "alerts": alerts,
        "go_usage": go_usage,
        "go_usage_status": go_usage_status,
    }

@app.get("/api/go_usage")
def get_go_usage_data():
    """返回最近一次成功抓取的 OpenCode Go 额度快照。"""
    snapshot = database.get_latest_go_usage_snapshot()
    return {
        "status": "ok" if snapshot else "empty",
        "snapshot": snapshot,
        "crawler": crawler.get_crawler_status(),
    }

@app.get("/api/chart_data")
def get_chart_data(granularity: str = "daily", model: Optional[str] = None):
    """按粒度和模型聚合图表数据。"""
    return database.get_aggregated_tokens(granularity=granularity, model=model)

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
        if "fetch_interval_minutes" in data:
            value = float(data["fetch_interval_minutes"])
            if value <= 0:
                raise ValueError("fetch interval must be positive")
            # 秒级选项（10s/30s）会换算成小数分钟，这里保留 3 位小数避免浮点噪音
            current_config["fetch_interval_minutes"] = round(value, 3)
    except (TypeError, ValueError):
        return JSONResponse(
            {"status": "error", "message": "预算金额与抓取间隔必须是大于 0 的数字"},
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

@app.get("/api/server/status")
def get_server_status():
    """返回后端自身健康状态和当前抓取状态。"""
    now = datetime.now()
    return {
        "status": "online",
        "healthy": True,
        "pid": os.getpid(),
        "host": DEFAULT_SERVER_HOST,
        "port": get_server_port(),
        "started_at": SERVER_STARTED_AT.strftime("%Y-%m-%d %H:%M:%S"),
        "uptime_seconds": max(0, int((now - SERVER_STARTED_AT).total_seconds())),
        "crawler": crawler.get_crawler_status(),
    }

@app.post("/api/server/stop")
def stop_server(background_tasks: BackgroundTasks):
    """停止当前后端进程，响应返回后执行。"""
    background_tasks.add_task(_request_server_shutdown)
    return {"status": "stopping", "message": "后端正在关闭"}

@app.post("/api/server/restart")
def restart_server(background_tasks: BackgroundTasks):
    """拉起隐藏的新后端进程后，优雅停止当前进程。"""
    try:
        _spawn_delayed_backend_restart()
    except Exception as exc:
        return JSONResponse(
            {"status": "error", "message": f"无法启动新的后端进程: {exc}"},
            status_code=500,
        )
    background_tasks.add_task(_request_server_shutdown)
    return {"status": "restarting", "message": "后端正在重启"}

@app.post("/api/sync")
def trigger_sync(background_tasks: BackgroundTasks):
    """手动触发后台抓取"""
    crawler.clear_stale_lock()
    status = crawler.get_crawler_status()
    if status.get("is_crawling"):
        return {"status": "busy", "message": "已有一个抓取任务在后台运行中..."}

    def do_sync():
        crawler_script = os.path.join(os.path.dirname(__file__), "crawler.py")
        _run_hidden_crawler(crawler_script)
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
