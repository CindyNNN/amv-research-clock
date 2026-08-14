# Wiki

## Technology Board Themes

- PCB
- CPO
- Robotics
- Liquid cooling
- Commercial aerospace
- Rare earths
- Minor metals
- Semiconductors
- Semiconductor materials
- Semiconductor equipment
- Chips

## Recommendation Labels

- `watch`: worth tracking, but not a buy instruction.
- `avoid_for_now`: risk or trend is unfavorable.
- `strong_sector_watchlist`: sector is strong and deserves deeper review.
- `pullback_wait`: trend is extended and a better entry may require patience.
- `risk_control_required`: volatility, drawdown, or data risk is elevated.

## 冷西西韭菜50 / Bagholder 50

- Public A-share crowding / reverse-behavior index by toocoldcc.
- Repo: https://github.com/toocoldcc/bagholder50
- Site: https://indices-toocoldcc.pages.dev/
- Four frozen factors: price chase, turnover spike, dragon-tiger list count, extra-large-order net flow. Higher score = more crowded = more caution.
- Equal-weight Top 50, next-open in / following-open out. Theoretical gross returns, not a live NAV.
- Relative heat (Bagholder50 20d return minus ChiNext 20d return, expanding percentile) is a usable *exit/stay-flat* skeleton for 159915, not a buy clock. Research version remains hysteresis 30/70. Walk-forward 2022–2025 was positive, but adding peak-drawdown / tighter bands did not earn complexity; 2026 holdout was weak. Do not replace 0AMV+emotion as the primary ChiNext timer.


## Theme industry ETF universe (0AMV research)

Mapped in `src/ai_invest_advisor/sector_etf_universe.py` and `data/sector_etfs/universe.csv`. Daily bars: Tencent fqkline qfq. Primary timer remains `amv_emo70_ma60` on 159915; sector ETFs are the same gate applied one-by-one, plus an experimental monthly top-1 overlay. Proxies: 515880 for CPO/comms, 512660 for aerospace, 512400 for minor metals, 159819 for AI/liquid-cooling. No dedicated large PCB ETF.

## Public 0AMV research site

- Static pages in `site/`, JSON under `site/data/`. Builder: `scripts/build_strategy_site.py`.
- Three pages: ChiNext timing (full-lot + five-sleeve inner tabs); monthly 120d equal-weight top-2 sector ETF rotation (overlay only, `m120_k2_raw_gate`); tech-theme Eastmoney fund flow.
- Cloud 0AMV: `data/amv/0amv_daily.csv`. GitHub Actions weekdays 12:00 UTC. Phone path: page button「录入 0AMV」or Actions `amv_close`.
- Fund-flow snapshot committed at `data/site/theme_fund_flow.csv`. Windows: 当日 / 3日 / 5日 / 10日 / 20日 (Tonghuashun `即时` and `N日排行`). If today's fetch fails, the flow page keeps the last snapshot and says so.
- ChiNext and rotation pages can inspect holdings on a chosen date (date picker or click the NAV curve). Rotation series stores `held` / `traded` per day.
- Overlays on ChiNext page: 000001 / 399001 / 399006 / 000300 / 159915. Rotation page overlays: ChiNext full-lot, 上证, 深成, 159915.

## Disclaimer

This is research support, not financial advice.
