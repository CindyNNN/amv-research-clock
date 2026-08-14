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

## 页面录入 0AMV

打开站点，点右上角 **录入 0AMV**，填指南针收盘价。用仓库所有者 GitHub 账号提交 Issue 后，Actions 会写入并更新网页。

收盘价若与上一有效日完全相同，不作为新的 AMV 信号。

## 定时任务

工作日 **12:00 UTC = 北京时间 20:00** 自动构建。也可随时 `workflow_dispatch`。
