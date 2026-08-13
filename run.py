import os
import sys
import time
import threading
import subprocess
from pathlib import Path
import uvicorn
import database
import crawler
import alert_engine
from app import DEFAULT_SERVER_HOST, app, get_server_port, set_server_shutdown_callback

def ensure_stdio() -> None:
    """pythonw 没有标准输出时，避免启动日志触发异常。"""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

def console_python_executable() -> str:
    """抓取子进程使用 python.exe，避免 pythonw 下 sys.stdout 为 None。"""
    current = Path(sys.executable)
    if os.name == "nt" and current.name.lower() == "pythonw.exe":
        candidate = current.with_name("python.exe")
        if candidate.exists():
            return str(candidate)
    return sys.executable

def hidden_subprocess_options():
    if os.name != "nt":
        return {}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return {"creationflags": flags}

def responsive_sleep(interval_min: float) -> None:
    """分块等待并监听配置变化，让新的抓取间隔尽快生效。"""
    deadline = time.monotonic() + interval_min * 60
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        try:
            new_interval = crawler.load_config().get("fetch_interval_minutes", interval_min)
        except Exception:
            new_interval = interval_min
        if new_interval != interval_min:
            interval_min = new_interval
            deadline = time.monotonic() + interval_min * 60
        time.sleep(min(2.0, remaining))

def scheduler_thread():
    print("[Scheduler] 启动后台定时抓取线程...")
    database.init_db()
    # 先等待主服务和端口启动完成，再延迟 5 秒开始首次抓取
    time.sleep(5)
    while True:
        try:
            config = crawler.load_config()
            interval_min = config.get("fetch_interval_minutes", 30)

            print("[Scheduler] 正在通过独立子进程执行周期性数据抓取...")
            crawler_script = os.path.join(os.path.dirname(__file__), "crawler.py")
            crawler_options = {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            }
            crawler_options.update(hidden_subprocess_options())
            subprocess.run([console_python_executable(), crawler_script], **crawler_options)
            alert_engine.check_budget_alerts()

            print(f"[Scheduler] 完成抓取，等待 {interval_min} 分钟后进行下一次检查...")
            responsive_sleep(interval_min)
        except Exception as e:
            print(f"[Scheduler] 出现异常: {e}")
            time.sleep(60)

if __name__ == "__main__":
    ensure_stdio()
    # 启动后台定时抓取线程
    t = threading.Thread(target=scheduler_thread, daemon=True)
    t.start()

    print("==================================================")
    print("🚀 OpenCode 使用量监控面板已启动！")
    server_port = get_server_port()
    print(f"👉 请在浏览器中打开: http://{DEFAULT_SERVER_HOST}:{server_port}")
    print("==================================================")

    # 使用 Server 对象，使网页上的停止/重启按钮可以优雅控制当前进程。
    server = uvicorn.Server(uvicorn.Config(app, host=DEFAULT_SERVER_HOST, port=server_port))
    set_server_shutdown_callback(lambda: setattr(server, "should_exit", True))
    server.run()
