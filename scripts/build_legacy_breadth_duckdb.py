from __future__ import annotations

from pathlib import Path

import duckdb


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "backtests" / "cyb_emotion_kdj" / "all_a_breadth_legacy_2020_2022.csv"

CN_URL = (
    "https://huggingface.co/datasets/cedwyh/jinjing-shared-data/"
    "resolve/main/cn_and_us_unified.parquet"
)
DELISTED_URL = (
    "https://huggingface.co/datasets/cedwyh/jinjing-shared-data/"
    "resolve/main/delisted_unified.parquet"
)

query = f"""
WITH raw AS (
    SELECT parsed_date AS date, symbol, CAST(close AS DOUBLE) AS close
    FROM (
        SELECT
            COALESCE(
                TRY_STRPTIME(CAST(date AS VARCHAR), '%Y-%m-%d'),
                TRY_STRPTIME(CAST(date AS VARCHAR), '%Y%m%d')
            )::DATE AS parsed_date,
            symbol,
            close,
            market
        FROM read_parquet('{CN_URL}')
    )
    WHERE market = 'CN'
      AND parsed_date BETWEEN DATE '2019-12-01' AND DATE '2022-12-31'

    UNION ALL

    SELECT parsed_date AS date, symbol, CAST(close AS DOUBLE) AS close
    FROM (
        SELECT
            COALESCE(
                TRY_STRPTIME(CAST(date AS VARCHAR), '%Y-%m-%d'),
                TRY_STRPTIME(CAST(date AS VARCHAR), '%Y%m%d')
            )::DATE AS parsed_date,
            symbol,
            close
        FROM read_parquet('{DELISTED_URL}')
    )
    WHERE parsed_date BETWEEN DATE '2019-12-01' AND DATE '2022-12-31'
),
deduplicated AS (
    SELECT date, symbol, MAX(close) AS close
    FROM raw
    WHERE close > 0
    GROUP BY date, symbol
),
with_previous AS (
    SELECT
        date,
        symbol,
        close,
        LAG(close) OVER (PARTITION BY symbol ORDER BY date) AS previous_close
    FROM deduplicated
)
SELECT
    date,
    COUNT(*) FILTER (WHERE close > previous_close) AS advancers,
    COUNT(*) FILTER (WHERE close = previous_close) AS unchanged,
    COUNT(*) FILTER (WHERE close < previous_close) AS decliners,
    COUNT(previous_close) AS quoted_total,
    100.0 * COUNT(*) FILTER (WHERE close > previous_close)
        / NULLIF(COUNT(previous_close), 0) AS emotion,
    'HuggingFace:cedwyh/jinjing-shared-data' AS source
FROM with_previous
WHERE date BETWEEN DATE '2020-01-01' AND DATE '2022-12-31'
GROUP BY date
ORDER BY date
"""

OUTPUT.parent.mkdir(parents=True, exist_ok=True)
connection = duckdb.connect()
frame = connection.execute(query).fetchdf()
frame.to_csv(OUTPUT, index=False, encoding="utf-8-sig")
print(frame.head().to_string(index=False))
print(frame.tail().to_string(index=False))
print(f"rows={len(frame)} output={OUTPUT}")
