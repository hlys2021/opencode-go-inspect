import os
import sys
import time
import threading
import subprocess
import uvicorn
import database
import crawler
import alert_engine
from app import app

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
            subprocess.run([sys.executable, crawler_script])
            alert_engine.check_budget_alerts()

            print(f"[Scheduler] 完成抓取，等待 {interval_min} 分钟后进行下一次检查...")
            time.sleep(interval_min * 60)
        except Exception as e:
            print(f"[Scheduler] 出现异常: {e}")
            time.sleep(60)

if __name__ == "__main__":
    # 启动后台定时抓取线程
    t = threading.Thread(target=scheduler_thread, daemon=True)
    t.start()

    print("==================================================")
    print("🚀 OpenCode 使用量监控面板已启动！")
    print("👉 请在浏览器中打开: http://127.0.0.1:8088")
    print("==================================================")

    # 启动 FastAPI Web 界面（使用 8088 端口）
    uvicorn.run(app, host="127.0.0.1", port=8088)
