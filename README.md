# AI 金融研究助手

本地 A 股 / 港股研究辅助工具。所有输出都是**研究支持，不是投资建议**。不会下单，不索取券商账号。

公开研究站（TooColdCC 风格的净值页）在 [`site/`](site/README.md)。

## 公开站点（GitHub Pages）

- 页面：创业板 **0AMV 满仓时钟**（`amv_emo70_ma60`，159915）以及 **五份离场委员会**（情绪离场 50/55/60/65/70 等权，观察用）。
- 每个交易日北京时间 **20:00**（12:00 UTC）由 GitHub Actions 重建；也可手动 Run workflow。
- 点页面右上角 **录入 0AMV**，填收盘价；用仓库所有者 GitHub 账号提交后，Actions 会写入并更新网页。

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

## 页面录入 0AMV

1. 打开站点，点右上角 **录入 0AMV**
2. 填日期和指南针收盘价
3. 用仓库所有者 GitHub 账号提交 Issue
4. 约 1–2 分钟后刷新网页

收盘价若与上一有效日完全相同，不作为新的 AMV 信号。

## 本地技术看板

Streamlit 看板仍是本机科技板块终端，与公开静态站分开。
