# 创业板 0AMV 公开研究站

- 日期：2026-08-14
- 标的：创业板 ETF 159915；对比层 上证指数 / 深证成指 / 创业板指 / 沪深300 / 159915 持有
- 产物：`site/` 静态页 + `scripts/build_strategy_site.py` + GitHub Actions 工作日 20:00（北京）
- 当前：满仓时钟与五份委员会均为空仓观察；2026-08-14 的 0AMV 收盘与 08-13 相同，不作为有效信号
- 结论属性：研究辅助，非投资建议

## 结论

把现用策略做成可在手机打开的公开净值页，风格接近 TooColdCC：满仓时钟一页，五份离场委员会一页（只观察、不替换时钟）。日频/月频行业轮动不在此站推广。

0AMV 不能在 GitHub 上算。出门把电脑留在家时，用 GitHub 手机 App 跑 Actions，把今日 0AMV 收盘价贴进 `amv_close`。不贴也能刷新指数对比层，页面会提示信号沿用上一有效日。

云端序列：`data/amv/0amv_daily.csv`。

## 本地打开

```
python scripts/build_strategy_site.py --offline
python -m http.server 8080 --directory site
```

http://127.0.0.1:8080/

#创业板 #0AMV #研究站 #GitHubPages
