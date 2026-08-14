---
name: investment-advice-analysis
description: Project-local workflow for A-share/H-share sector and stock trade-advice analysis. Use when the user asks whether a sector, theme, board, stock, or watchlist is worth buying, adding, holding, reducing, avoiding, or "上车", including questions like "液冷板块如何", "现在能买吗", "建议买吗", "是不是低位", or "帮我看交易机会".
---

# Investment Advice Analysis

## Purpose

Use this skill to turn market data, project dashboard signals, recent prices, and fundamentals into cautious, evidence-backed trade advice. The output is research support only: never place orders, request broker credentials, or promise returns.

## Core Rule

Do not treat "low position" as automatically safe. Always distinguish:

- Healthy low position: drawdown has stabilized, price reclaims key moving averages, volume/funds improve, and the theme has a live catalyst.
- Weak low position: price is below key moving averages, recent rebound lacks fund inflow, and stronger themes are attracting capital.

## Workflow

### 1. Clarify the Target

Infer the likely target from the user request:

- Sector/theme: map to local board names in `data/dashboard/latest/tech_board_scores.csv`.
- A-share stock: use `.SZ` or `.SS`/`.SH` style public quote symbols when fetching prices.
- H-share stock: use `.HK`.

If a Chinese name has multiple listed entities, state the mapping assumption clearly.

### 2. Read Local Project Signals First

For technology and advanced manufacturing themes, inspect local dashboard cache before external data:

```powershell
$env:PYTHONPATH='src'; python -c "import pandas as pd; df=pd.read_csv('data/dashboard/latest/tech_board_scores.csv'); print(df[df.astype(str).apply(lambda col: col.str.contains('关键词', na=False)).any(axis=1)].to_string(index=False))"
```

Use these fields:

- `score`: board strength. Above 70 is strong; 60-70 is watch/pullback; below 50 is weak.
- `advice_label`: existing project bucket.
- `ret5`, `ret20`: short-term and swing performance.
- `drawdown20`: whether the board is still in a 20-day drawdown.
- `net_amount`: fund flow. Positive confirms; negative warns.
- `risk_flags`: overheating, deep drawdown, volatility, or no obvious abnormality.

Also read:

- `data/dashboard/latest/data_status.json`
- `data/dashboard/latest/sentiment_history.csv`

If cache is stale, say so and either refresh with `refresh-dashboard` if requested, or lower confidence.

### 3. Fetch Current Market Data When Needed

For current questions, verify prices because this information changes daily. Prefer official disclosures for fundamentals and public quote APIs for prices.

Use Yahoo chart API when AKShare endpoints fail:

```powershell
python -c "import requests, datetime, pandas as pd; t='002837.SZ'; data=requests.get(f'https://query1.finance.yahoo.com/v8/finance/chart/{t}?range=1y&interval=1d',timeout=20,headers={'User-Agent':'Mozilla/5.0'}).json(); res=data['chart']['result'][0]; q=res['indicators']['quote'][0]; rows=[]; [rows.append((datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d'),c,v)) for ts,c,v in zip(res['timestamp'],q['close'],q['volume']) if c is not None]; df=pd.DataFrame(rows,columns=['date','close','volume']); close=df['close']; print(df.tail(10).to_string(index=False)); print({'last':float(close.iloc[-1]),'ret5':float(close.iloc[-1]/close.iloc[-6]-1),'ret20':float(close.iloc[-1]/close.iloc[-21]-1),'ret60':float(close.iloc[-1]/close.iloc[-61]-1),'above20':float(close.iloc[-1]/close.tail(20).mean()-1),'above60':float(close.iloc[-1]/close.tail(60).mean()-1),'dd_high_1y':float(close.iloc[-1]/close.max()-1),'vol20_vs60':float(df['volume'].tail(20).mean()/df['volume'].tail(60).mean()-1)})"
```

Request network escalation when local sandbox blocks public data fetching.

### 4. Add K-Line Shape Analysis

Use K-line morphology as confirmation, not as a standalone buy reason. Inspect at least the most recent 20-60 daily candles when price data is available.

Calculate or describe:

- Trend structure: higher highs/higher lows, lower highs/lower lows, box range, or sharp V rebound.
- Moving-average relationship: price versus 5/10/20/60-day averages; whether the 20-day average is flattening, rising, or falling.
- Breakout quality: breakout above a range or prior high should have volume expansion and should not close with a large upper shadow.
- Pullback quality: healthy pullback holds above the 20-day average or previous platform top with shrinking volume.
- Reversal hints: long lower shadow near support, bullish engulfing after a decline, or reclaiming the 20-day average after a false breakdown.
- Failure hints: long upper shadow after a fast rebound, gap-up fade, high-volume bearish candle, failed breakout, or repeated rejection at the 20/60-day average.
- Volume confirmation: compare recent volume with 20-day and 60-day average volume. Price up with shrinking volume is weaker than price up with controlled volume expansion.

Use this K-line decision table:

| K-line condition | Meaning | Advice impact |
| --- | --- | --- |
| Reclaims 20-day average, 20-day average flattens/rises, volume improves | Repair is underway | Allow trial position |
| Breaks platform high with volume, closes near high | Right-side confirmation | Allow add-on after pullback |
| Fast 5-day rebound > 12%-15% with long upper shadow | Chasing risk | Wait for pullback |
| Below 20-day and 60-day averages, rebound volume weak | Weak low position | Avoid new position |
| Pullback holds 20-day average with shrinking volume | Healthy digestion | Watch for second entry |
| Falls below recent platform support with volume | Setup invalidated | Reduce/avoid |

When discussing K-line shape, state both the constructive sign and the invalidation sign. Example: "The stock has reclaimed the 20-day average, but a close back below the platform support would invalidate the repair."

### 5. Score the Trade Setup

Judge the setup across five dimensions:

1. Theme logic: is the industry catalyst still active?
2. Relative position: is it low versus its own 1-year range and versus hot themes?
3. Trend repair: has it reclaimed 20-day and 60-day averages?
4. Fund confirmation: is `net_amount` positive or improving?
5. Risk/reward: is short-term rebound already too sharp?

Use this decision table:

| Condition | Interpretation | Suggested action |
| --- | --- | --- |
| score >= 70, net_amount > 0, ret5 <= 12%, above 20/60-day averages | Confirmed strength | Watch/buy on pullback |
| score 50-70, trend repairing, net_amount improving | Early repair | Small trial position |
| score < 50, net_amount < 0, ret20 weak | Low but weak | Wait |
| ret5 > 12%-15% after a weak base | Rebound too fast | Do not chase |
| price below 20-day average after failed rebound | Weak continuation risk | Avoid/reduce |

### 6. Position Sizing Language

Use ranges instead of absolute instructions:

- No confirmation: no new position, or only watch.
- Early repair: 20%-30% of planned position.
- Confirmed but not overheated: 40%-60% of planned position.
- Strong market plus strong board: up to 50%-70% observation allocation for the theme, diversified across names.
- Overheated or fund outflow: reduce, wait, or keep only base exposure.

Never say "all in", "guaranteed", "risk-free", or "safe to buy". Prefer "试仓", "等待确认", "分批", "触发条件", and "撤退条件".

## Output Format

Answer in concise Chinese with:

1. Direct verdict: buy / trial position / wait / reduce / avoid.
2. Evidence table or bullets: local board score, returns, moving-average position, fund flow, key stock confirmation.
3. K-line shape: trend structure, support/resistance, volume, constructive signs, and invalidation signs.
4. Why "low" is or is not safe.
5. Trading plan:
   - Entry zone or condition.
   - Add condition.
   - Stop-loss or invalidation condition.
   - Position size range.
6. Data sources and date.
7. Disclaimer: `This is research support, not financial advice.`

## Example Reasoning Pattern

For "液冷板块如何，我觉得目前液冷相对低位，上车安全吗":

- Confirm local cache rows for `液冷服务器`, `数据中心(AIDC)`, `东数西算(算力)`, and `算力租赁`.
- Compare them with current hot themes such as PCB, CPO, semiconductor equipment, and semiconductor materials.
- Add K-line checks for representative stocks: whether they reclaim the 20-day average, break a platform, close near highs, show long upper shadows, or fail at resistance.
- Pull representative stocks such as 英维克, 高澜股份, 申菱环境, 飞荣达, 佳力图, 同飞股份, 科创新源, 依米康.
- If board score is below 50 and net amount is negative, say: "relative low, but not yet safe; use trial position only."
- If some leaders reclaim 20-day averages while the board is still weak, say: "left-side trial, wait for fund confirmation before adding."

## Hard Boundaries

- Do not place trades.
- Do not ask for broker credentials.
- Do not present delayed public data as live broker data.
- Do not make personalized suitability claims unless the user has provided risk tolerance, time horizon, and position context.
- For high-risk or ambiguous cases, ask for current holdings, cost basis, and intended holding period before giving position-specific advice.
