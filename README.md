# OpenCode 使用量监控与预算告警系统 (OpenCode Usage Monitor & Alert System)

本工具专为 OpenCode（Go 套餐）设计，通过 Playwright 自动化抓取 OpenCode Web 端的详细使用量历史，并提供图形化 Web 看板、预算阈值预警及数据 CSV 导出功能。

---

## 📁 项目结构说明

位于目录 `D:\models\opencode-inspect` 下：

```
D:\models\opencode-inspect\
├── config.json           # 系统配置文件 (抓取目标 URL、轮询间隔、预算阈值)
├── requirements.txt     # Python 依赖包清单
├── database.py           # SQLite 数据库模型与聚合查询接口
├── crawler.py            # Playwright 网页端使用量数据自动化抓取与解析引擎
├── alert_engine.py       # 预算消耗计算与预警/超额告警触发引擎
├── app.py                # FastAPI 后端 REST API 与 CSV 导出服务
├── run.py                # 一键启动主服务脚本 (同时运行后台抓取与 Web 看板)
├── templates/
│   └── index.html        # Chart.js + TailwindCSS 响应式监控看板界面
└── user_data/            # Playwright 浏览器登录状态与 Session 持久化目录
```

---

## 🚀 快速使用指南

### 第一步：安装依赖包
打开命令行终端 (Git Bash / PowerShell)，进入项目目录并安装依赖：

```bash
cd /d/models/opencode-inspect
pip install -r requirements.txt
playwright install chromium
```

### 第二步：修改配置文件 `config.json`
在 `config.json` 中配置你的 OpenCode 使用量页面 URL 及预算告警阈值：

```json
{
  "target_url": "https://opencode.ai/workspace/YOUR_WORKSPACE_ID/usage",
  "fetch_interval_minutes": 30,
  "daily_budget_usd": 5.0,
  "monthly_budget_usd": 50.0,
  "alert_warning_ratio": 0.8,
  "alert_critical_ratio": 1.0,
  "headless": false
}
```

### 第三步：启动监控系统
运行启动脚本：

```bash
python run.py
```

* **首次运行登录**：系统会弹出一个 Chromium 浏览器窗口打开 OpenCode 使用量页面。如果是首次使用，请在弹出的窗口中手动完成账号登录。登录凭证会自动保存至 `user_data/` 目录，后续运行无需再次登录。
* **访问 Web 看板**：打开浏览器访问 [http://127.0.0.1:18088](http://127.0.0.1:18088) 即可查看可视化图表与详细面板。若需自定义端口，可先设置环境变量 `OPENCODE_MONITOR_PORT`。

---

## ✨ 核心功能亮点

1. **可视化仪表盘 (Web Dashboard)**：
   * **概览卡片**：累计成本（美元/Go点数）、输入/输出 Token 总数、月度预算消耗百分比进度条。
   * **趋势图表**：折线图展示使用成本随时间变化趋势，环形图展示不同模型（如 `deepseek-v4-flash`）的分布与占比。
2. **自动化去重与历史记录**：
   * 基于每行记录的时间戳、模型与 Token 数量生成联合签名，保证增量抓取时不重复插入。
3. **预算告警 (Budget Warning)**：
   * 自动监控**当月**与**当日**消耗。当达到预算 80% 时触发**预警（Warning）**；达到或超过 100% 时触发**严重告警（Critical）**，并在 Web 看板顶端以显著颜色预警。同一类型告警 24 小时内自动去重，避免重复刷屏。
4. **一键 CSV 数据导出**：
   * 顶部提供【一键导出 CSV】按钮，导出文件内置 UTF-8 BOM，完美兼容 Excel / WPS 直接打开，不出现乱码。
