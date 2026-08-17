"""隐藏启动 OpenCode 监控后端的辅助入口。"""

import os
import subprocess
import sys
import time
from pathlib import Path


def _python_console_executable() -> str:
    current = Path(sys.executable)
    if os.name == "nt" and current.name.lower() == "pythonw.exe":
        candidate = current.with_name("python.exe")
        if candidate.exists():
            return str(candidate)
    return sys.executable


def _hidden_options() -> dict:
    if os.name != "nt":
        return {}
    flags = (
        getattr(subprocess, "CREATE_NO_WINDOW", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
    )
    return {"creationflags": flags}


def main() -> None:
    delay = float(os.environ.get("OPENCODE_MONITOR_RESTART_DELAY", "1.5"))
    time.sleep(max(0.2, delay))

    root = Path(__file__).resolve().parent
    run_path = root / "run.py"
    options = _hidden_options()
    options.update({
        "cwd": str(root),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    })
    subprocess.Popen(
        [_python_console_executable(), "-X", "utf8", str(run_path)],
        **options,
    )


if __name__ == "__main__":
    main()
