import sys
import os
import time
import httpx
from datetime import datetime, timezone
from datetime import date, timedelta
import uuid
import argparse

# Add backend to path so we can import models
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from database import DiscoverySessionLocal
from models.discovery import BenchmarkCandle

BENCHMARK_CODE = "NIFTY500"
BENCHMARK_NAME = "NIFTY 500"
SOURCE_NAME = "GROWW"
GROWW_INTERVAL_MINUTES = 1440
DEFAULT_LOOKBACK_DAYS = 3650

def parse_groww_candles(raw_candles: list) -> list:
    processed = []
    for c in raw_candles:
        if not isinstance(c, (list, tuple)) or len(c) < 5:
            continue
        if c[1] is None or c[2] is None or c[3] is None or c[4] is None:
            continue

        try:
            ts_sec = c[0]
            if ts_sec > 10_000_000_000:
                ts_sec = ts_sec // 1000

            # Groww gives IST timestamps generally, add 5.5h (19800s)
            # to land on the correct day in IST.
            dt_obj = datetime.fromtimestamp(ts_sec + 19800, tz=timezone.utc).date()

            processed.append({
                "trade_date": dt_obj,
                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4]),
                "volume": int(c[5]) if len(c) > 5 and c[5] is not None else 0,
            })
        except (ValueError, TypeError):
            continue
    return processed


def fetch_groww_candles(benchmark_code: str, start_ms: int, end_ms: int) -> list[dict]:
    url = (
        f"https://groww.in/v1/api/charting_service/v2/chart/"
        f"exchange/NSE/segment/CASH/{benchmark_code}"
        f"?intervalInMinutes={GROWW_INTERVAL_MINUTES}&minimal=false"
        f"&startTimeInMillis={start_ms}&endTimeInMillis={end_ms}"
    )
    headers = {"User-Agent": "Mozilla/5.0"}
    with httpx.Client(timeout=10.0, headers=headers) as client:
        res = client.get(url)
        res.raise_for_status()
        data = res.json()
    return parse_groww_candles(data.get("candles", []))


def _latest_existing_trade_date(db, benchmark_code: str) -> date | None:
    return (
        db.query(BenchmarkCandle.trade_date)
        .filter(BenchmarkCandle.benchmark_code == benchmark_code)
        .order_by(BenchmarkCandle.trade_date.desc())
        .limit(1)
        .scalar()
    )


def _trade_date_to_ms(trade_date: date) -> int:
    dt = datetime.combine(trade_date, datetime.min.time()).replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)

def run(full_refresh: bool = False, lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> None:
    db = DiscoverySessionLocal()
    try:
        end_ms = int(time.time() * 1000)
        if full_refresh:
            start_ms = end_ms - (lookback_days * 86400 * 1000)
        else:
            latest_existing = _latest_existing_trade_date(db, BENCHMARK_CODE)
            if latest_existing is None:
                start_ms = end_ms - (lookback_days * 86400 * 1000)
            else:
                overlap_start = latest_existing - timedelta(days=2)
                start_ms = max(_trade_date_to_ms(overlap_start), end_ms - (lookback_days * 86400 * 1000))

        print(f"Fetching {BENCHMARK_CODE} data from Groww...")
        candles = fetch_groww_candles(BENCHMARK_CODE, start_ms, end_ms)
        print(f"Fetched {len(candles)} valid candles.")

        if not candles:
            print("No candles to insert or update.")
            return

        existing_rows = (
            db.query(BenchmarkCandle)
            .filter(BenchmarkCandle.benchmark_code == BENCHMARK_CODE)
            .filter(BenchmarkCandle.trade_date >= min(c["trade_date"] for c in candles))
            .all()
        )
        existing_by_date = {row.trade_date: row for row in existing_rows}

        inserted = 0
        updated = 0
        now_dt = datetime.now(timezone.utc)
        batch_id = str(uuid.uuid4())

        for candle in candles:
            row = existing_by_date.get(candle["trade_date"])
            if row is None:
                row = BenchmarkCandle(
                    id=str(uuid.uuid4()),
                    benchmark_code=BENCHMARK_CODE,
                    benchmark_name=BENCHMARK_NAME,
                    trade_date=candle["trade_date"],
                    open=candle["open"],
                    high=candle["high"],
                    low=candle["low"],
                    close=candle["close"],
                    volume=candle["volume"],
                    source_name=SOURCE_NAME,
                    import_batch_id=batch_id,
                    created_at=now_dt,
                    updated_at=now_dt,
                )
                db.add(row)
                inserted += 1
            else:
                row.benchmark_name = BENCHMARK_NAME
                row.open = candle["open"]
                row.high = candle["high"]
                row.low = candle["low"]
                row.close = candle["close"]
                row.volume = candle["volume"]
                row.source_name = SOURCE_NAME
                row.import_batch_id = batch_id
                row.updated_at = now_dt
                updated += 1

        db.commit()
        print(f"Inserted {inserted} rows and updated {updated} rows for {BENCHMARK_CODE}.")
    finally:
        db.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import or refresh NIFTY500 benchmark candles from Groww.")
    parser.add_argument("--full-refresh", action="store_true", help="Backfill the full lookback window instead of doing an incremental refresh.")
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS, help="Calendar days to look back when backfilling.")
    args = parser.parse_args()
    run(full_refresh=args.full_refresh, lookback_days=args.lookback_days)
