# 创业板研究观察

静态页，由 `scripts/build_strategy_site.py` 写入 `site/data/`。GitHub Pages 从本目录发布。

顶部三个页面：创业板指数策略、板块轮动对照、科技主题资金流入。

**研究辅助，不是投资建议。** 不会帮你下单，也不会要券商账号。

## 网上地址

https://cindynnn.github.io/amv-research-clock/

仓库：https://github.com/CindyNNN/amv-research-clock

## 本地预览

在仓库根目录：

```powershell
$env:PYTHONPATH='src'
python scripts/build_strategy_site.py --offline
python -m http.server 8080 --directory site
```

浏览器打开 http://127.0.0.1:8080/ 。不要用 `file://`，否则页面读不到数据。

## 录入 0AMV

打开站点，进「创业板指数策略」这一页，点右上角 **录入 0AMV**，填指南针收盘价。用仓库所有者的 GitHub 账号提交后，大约一两分钟刷新网页即可。

收盘价若和上一有效日完全相同，不当新信号，仓位不会变。

## 每天怎么更新

工作日晚上八点左右自动构建。资金流如果当天拉不到，网站照样发布，只是资金流页会写明数据停在哪一天。
