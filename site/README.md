# 创业板 0AMV 研究站点

静态页，由 `scripts/build_strategy_site.py` 写入 `site/data/`。GitHub Pages 从本目录发布。

**研究辅助，不是投资建议。** 不代客下单，不索取券商账号。

## Pages 地址

https://cindynnn.github.io/amv-research-clock/

仓库：https://github.com/CindyNNN/amv-research-clock

## 本地预览

在仓库根目录：

```powershell
$env:PYTHONPATH='src'
python scripts/build_strategy_site.py --offline
python -m http.server 8080 --directory site
```

浏览器打开 http://127.0.0.1:8080/ 。不要用 `file://`，否则 JSON 无法加载。

## 手机补录 0AMV（电脑不在身边）

指南针/通达信不能在 GitHub 上跑。云端真相源是仓库里的 `data/amv/0amv_daily.csv`。

1. 打开 GitHub 手机 App → 本仓库 → **Actions**
2. 选 **Build strategy site** → **Run workflow**
3. 在 `amv_close` 粘贴今日 0AMV **收盘价**
4. 如需指定日期，填 `amv_date`（`YYYY-MM-DD`）；不填则用北京今天
5. 跑完后 Pages 会更新；若未填收盘价，指数/ETF 对比层仍会刷新，页面横幅写「今日 0AMV 未更新，信号沿用上一有效日」

收盘价若与上一有效日完全相同（例如 2026-08-14 曾误录成与 08-13 相同的 207502.5），不会当作新的 AMV 信号。

## 定时任务

工作日 **12:00 UTC = 北京时间 20:00** 自动构建。也可随时 `workflow_dispatch`。
