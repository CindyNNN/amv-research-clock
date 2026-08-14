# AI 金融研究助手

本地 A 股 / 港股研究辅助工具。所有输出都是**研究支持，不是投资建议**。不会下单，不索取券商账号。

公开研究站（TooColdCC 风格的净值页）在 [`site/`](site/README.md)。

## 公开站点（GitHub Pages）

- 页面：创业板 **0AMV 满仓时钟**（`amv_emo70_ma60`，159915）以及 **五份离场委员会**（情绪离场 50/55/60/65/70 等权，观察用）。
- 每个交易日北京时间 **20:00**（12:00 UTC）由 GitHub Actions 重建；也可手动 Run workflow。
- **0AMV 无法在云端计算**（指南针/通达信不能上 GitHub）。云端真相源是 `data/amv/0amv_daily.csv`。出门在外请用手机 App 跑 Actions 并粘贴今日收盘价。

公开地址：https://cindynnn.github.io/amv-research-clock/

仓库：https://github.com/CindyNNN/amv-research-clock

Pages 已设为 GitHub Actions 构建。每个交易日北京时间 20:00 自动更新；也可在 Actions 里手动 Run workflow。

### 本地预览

```powershell
$env:PYTHONPATH='src'
python scripts/build_strategy_site.py --offline
python -m http.server 8080 --directory site
```

打开 http://127.0.0.1:8080/

### 手机补录 0AMV

1. GitHub App → 本仓库 → **Actions** → **Build strategy site** → **Run workflow**
2. `amv_close`：今日 0AMV 收盘价
3. `amv_date`：可选，`YYYY-MM-DD`；不填即北京今天
4. 不填收盘价时仍会刷新上证/深成/创业板指/沪深300/159915 对比层，横幅提示信号沿用上一有效日

若某日收盘价与上一有效日完全相同，不作为新的 AMV 信号。

## 本地技术看板

Streamlit 看板仍是本机科技板块终端，与公开静态站分开。
