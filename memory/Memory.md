# Memory

## User Preferences

- Markets: A shares and Hong Kong shares.
- First data preference: public data, with TongDaXin local data as an optional adapter.
- Output preference: explainable research views with risk notes.
- Sector scope: focus on technology and advanced manufacturing themes, especially PCB, CPO, robotics, liquid cooling, commercial aerospace, rare earths, minor metals, semiconductors, semiconductor materials, semiconductor equipment, and chips.
- 0AMV daily workflow (from 2026-08-10): do **not** auto-launch Compass; one weekday task at **15:30** prompts for today's 0AMV **close** then sends close email (`run_cyb_signal_monitor_close.bat`).

## Watchlists

- Technology board universe saved under `data/tech_boards/tech_boards_20260616_141058`.
- Current priority themes: PCB, CPO, robotics, liquid cooling, commercial aerospace, rare earths, minor metals, semiconductor chain, and chips.
- External crowding monitor: TooColdCC 韭菜50 (`toocoldcc/bagholder50`) can be used as an avoid-list cross-check, not as a buy list.
- Sector-ETF research (2026-08-14): keep 0AMV+emotion as the ChiNext clock. Daily momentum rotation is rejected. Monthly 20d top-1 failed walk-forward vs that clock; 120d equal-weight top-2 only barely passed stability and is an observation, not a replacement.
- Position sizing (2026-08-14): do not slow-ramp the binary 0AMV switch. If splitting into 5 lots, equal sleeves with emotion exits 50/55/60/65/70 is the only design that is not clearly worse; live monitor stays full-lot e70 until a later holdout.
- Public research site (2026-08-14): static pages in `site/`, three top-level views — ChiNext timing, 120d top-2 sector rotation as overlay only, tech-theme fund flow. Rebuilt weekdays 20:00 CST. Enter 0AMV from the ChiNext page button (GitHub issue). Cloud series: `data/amv/0amv_daily.csv`. Fund-flow snapshot: `data/site/theme_fund_flow.csv` (do not use gitignored `data/dashboard/`). Local Streamlit dashboard stays the tech-board terminal. Reader-facing copy should be natural Chinese, not translated jargon.
