"""
Tests for TechnicalConsistencyService.
"""
from __future__ import annotations

import uuid
import datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text, inspect

from database import DiscoverySessionLocal, source_engine
from models.discovery import BenchmarkCandle, CompanyTechnicalMetric
from services.technical.technical_consistency import TechnicalConsistencyService
import config


@pytest.fixture
def disc_session():
    session = DiscoverySessionLocal()
    session.execute(text("DELETE FROM company_technical_metrics"))
    session.execute(text("DELETE FROM benchmark_candles"))
    session.commit()
    yield session
    session.execute(text("DELETE FROM company_technical_metrics"))
    session.execute(text("DELETE FROM benchmark_candles"))
    session.commit()
    session.close()


def _populate_benchmark(session, n=1000):
    base_date = datetime.date(2020, 1, 1)
    candles = []
    current_date = base_date
    for _ in range(n):
        while current_date.weekday() > 4:
            current_date += datetime.timedelta(days=1)
        candles.append(
            BenchmarkCandle(
                id=str(uuid.uuid4()),
                benchmark_code="NIFTY500",
                benchmark_name="Nifty 500",
                trade_date=current_date,
                open=100.0, high=100.0, low=100.0, close=100.0,
                source_name="TEST", import_batch_id="test"
            )
        )
        current_date += datetime.timedelta(days=1)
    session.add_all(candles)
    session.commit()
    return sorted([c.trade_date for c in candles])


def _populate_metric(session, run_id, symbol, horizon, as_of_date, bench_date):
    rec = CompanyTechnicalMetric(
        id=str(uuid.uuid4()),
        run_id=run_id,
        source_company_id=f"comp_{symbol}",
        symbol=symbol,
        horizon=horizon,
        as_of_date=as_of_date,
        company_candle_date=bench_date,
        benchmark_candle_date=bench_date,
        current_close=150.0,
        start_close=100.0,
        company_return=50.0,
        return_available=True,
        calculation_details={"existing_key": "exists"}
    )
    session.add(rec)
    session.commit()
    return rec


def _mock_src_session(candles=None):
    if candles is None:
        candles = []
    src = MagicMock()
    call_count = {"candles": 0}
    def _execute(query_text, params=None):
        sql = str(query_text).lower()
        res = MagicMock()
        if "market_candles" in sql:
            call_count["candles"] += 1
            res.fetchall.return_value = candles
        return res
    src.execute.side_effect = _execute
    src.call_count = call_count
    return src


# ------------------------------------------------------------------ #
#  1-4. Exact Horizon Blocks and Exact Date Matching                   #
# ------------------------------------------------------------------ #

@pytest.mark.parametrize("horizon, n_blocks, s_block", [
    ("SHORT", 4, 5),
    ("MID", 3, 21),
    ("LONG", 4, 63),
])
def test_exact_horizon_blocks_and_dates(disc_session, horizon, n_blocks, s_block):
    bench_dates = _populate_benchmark(disc_session, 600)
    as_of = bench_dates[-1]
    _populate_metric(disc_session, "test", "ABC", horizon, as_of, as_of)
    
    mock_candles = []
    # Build a consistent price series: price grows backwards so return is always positive
    # E.g. start=100, end=150.
    price_map = {}
    for i in range(n_blocks):
        end_idx = 599 - i * s_block
        start_idx = 599 - (i + 1) * s_block
        price_map[bench_dates[end_idx]] = float(end_idx + 1)
        price_map[bench_dates[start_idx]] = float(start_idx + 1)
        
    for dt, p in price_map.items():
        mock_candles.append(MagicMock(symbol="ABC", dt=dt, close=p))
        
    src = _mock_src_session(candles=mock_candles)
    svc = TechnicalConsistencyService(src, disc_session)
    svc.calculate_and_save_consistency("test", horizon)
    
    rec = disc_session.query(CompanyTechnicalMetric).first()
    assert rec.consistency_available is True
    
    # 50% return > benchmark 0% return -> all positive and outperforming
    assert rec.positive_period_ratio == 100.0
    assert rec.benchmark_outperformance_ratio == 100.0
    assert rec.company_consistency_score == 100.0
    
    details = rec.calculation_details["consistency"]
    assert details["valid_periods"] == n_blocks
    assert len(details["periods"]) == n_blocks
    
    # Verify exact structure
    assert details["expected_periods"] == n_blocks


# ------------------------------------------------------------------ #
#  5-7. Ratio calculations and 50/50 score                             #
# ------------------------------------------------------------------ #

def test_ratio_calculations_and_5050_score(disc_session):
    bench_dates = _populate_benchmark(disc_session, 100)
    as_of = bench_dates[-1]
    _populate_metric(disc_session, "test", "ABC", "SHORT", as_of, as_of)
    
    # SHORT: 4 blocks of 5
    # To test ratios, we want:
    # 4 valid blocks total.
    # 1: Negative, Underperformed  (c_ret: -10%, b_ret: 0%)
    mock_candles = []
    price_map = {}
    # Block 0: pos (110/100 = 10%)
    price_map[bench_dates[99 - 0*5]] = 110.0
    price_map[bench_dates[99 - 1*5]] = 100.0
    # Block 1: pos (110/100 = 10%), overlaps start of 0
    # but wait, if end of block 1 is start of block 0, the date is the same!
    # If the date is 94, it must have the same price!
    # So we must construct an unbroken valid chain if they share endpoints.
    # Block 0: 99=110, 94=100 (+10%)
    # Block 1: 94=100, 89=110 (-9%)   Wait, if start=110, end=100 -> negative
    # Block 2: 89=110, 84=100 (+10%)
    # Block 3: 84=100, 79=110 (-9%)
    
    price_map[bench_dates[99 - 0*5]] = 110.0
    price_map[bench_dates[99 - 1*5]] = 100.0
    price_map[bench_dates[99 - 2*5]] = 110.0
    price_map[bench_dates[99 - 3*5]] = 100.0
    price_map[bench_dates[99 - 4*5]] = 110.0
    
    for dt, p in price_map.items():
        mock_candles.append(MagicMock(symbol="ABC", dt=dt, close=p))
        
    src = _mock_src_session(candles=mock_candles)
    svc = TechnicalConsistencyService(src, disc_session)
    svc.calculate_and_save_consistency("test", "SHORT")
    
    rec = disc_session.query(CompanyTechnicalMetric).first()
    # 2 positive, 2 negative. Total valid = 4. Ratio = 50%.
    assert rec.positive_period_ratio == 50.0
    assert rec.benchmark_outperformance_ratio == 50.0
    assert rec.company_consistency_score == 50.0
    

# ------------------------------------------------------------------ #
#  8-10. Missing candles & Invalidations                               #
# ------------------------------------------------------------------ #

def test_missing_candles_and_block_invalidations(disc_session):
    bench_dates = _populate_benchmark(disc_session, 100)
    as_of = bench_dates[-1]
    _populate_metric(disc_session, "test", "ABC", "SHORT", as_of, as_of)
    
    # 4 Blocks of 5.
    # Block 0: Missing intermediate candle (should be valid)
    # Block 1: Missing start candle (should invalidate ONLY Block 1)
    # Block 2: Missing end candle (should invalidate ONLY Block 2)
    # Block 3: Valid
    
    mock_candles = []
    price_map = {}
    
    # Block 0: Start/End present
    # end=99, start=94
    price_map[bench_dates[99 - 0*5]] = 150.0
    price_map[bench_dates[99 - 1*5]] = 100.0
    
    # Block 1: Missing start (99-2*5)
    # end=94 (already present from block 0)
    # start=89 (we intentionally DO NOT ADD IT)
    
    # Block 2: Missing end (99-2*5)
    # end=89 (already missing from block 1)
    # start=84
    price_map[bench_dates[99 - 3*5]] = 100.0
    
    # Block 3: Valid
    # end=84 (already present from block 2)
    # start=79
    price_map[bench_dates[99 - 4*5]] = 50.0
    
    for dt, p in price_map.items():
        mock_candles.append(MagicMock(symbol="ABC", dt=dt, close=p))
    
    src = _mock_src_session(candles=mock_candles)
    svc = TechnicalConsistencyService(src, disc_session)
    svc.calculate_and_save_consistency("test", "SHORT")
    
    rec = disc_session.query(CompanyTechnicalMetric).first()
    assert rec.consistency_available is True # 2 valid blocks
    
    details = rec.calculation_details["consistency"]
    assert details["valid_periods"] == 2
    # The two valid blocks have positive return -> 100% ratios
    # Block 0: 150/100 -> positive
    # Block 3: 100/50 -> positive
    assert rec.positive_period_ratio == 100.0


# ------------------------------------------------------------------ #
#  11-12. Two valid blocks limit                                       #
# ------------------------------------------------------------------ #

def test_less_than_two_blocks_unavailable(disc_session):
    bench_dates = _populate_benchmark(disc_session, 100)
    as_of = bench_dates[-1]
    _populate_metric(disc_session, "test", "ABC", "SHORT", as_of, as_of)
    
    mock_candles = []
    # Provide ONLY Block 0
    mock_candles.append(MagicMock(symbol="ABC", dt=bench_dates[99 - 0*5], close=150.0))
    mock_candles.append(MagicMock(symbol="ABC", dt=bench_dates[99 - 1*5], close=100.0))
    
    src = _mock_src_session(candles=mock_candles)
    svc = TechnicalConsistencyService(src, disc_session)
    svc.calculate_and_save_consistency("test", "SHORT")
    
    rec = disc_session.query(CompanyTechnicalMetric).first()
    assert rec.consistency_available is False
    assert "INSUFFICIENT_VALID_CONSISTENCY_PERIODS" in rec.warnings
    assert rec.positive_period_ratio is None
    assert rec.benchmark_outperformance_ratio is None
    assert rec.company_consistency_score is None


# ------------------------------------------------------------------ #
#  13-15. Preservations, Merging, Idempotency                          #
# ------------------------------------------------------------------ #

def test_preservations_and_idempotency(disc_session):
    bench_dates = _populate_benchmark(disc_session, 100)
    as_of = bench_dates[-1]
    rec = _populate_metric(disc_session, "test", "ABC", "SHORT", as_of, as_of)
    assert rec.calculation_details["existing_key"] == "exists"
    assert rec.company_return == 50.0
    
    mock_candles = []
    # Block 0 and 1 valid
    mock_candles.append(MagicMock(symbol="ABC", dt=bench_dates[99], close=150.0))
    mock_candles.append(MagicMock(symbol="ABC", dt=bench_dates[94], close=100.0))
    mock_candles.append(MagicMock(symbol="ABC", dt=bench_dates[94], close=150.0))
    mock_candles.append(MagicMock(symbol="ABC", dt=bench_dates[89], close=100.0))
    
    src = _mock_src_session(candles=mock_candles)
    svc = TechnicalConsistencyService(src, disc_session)
    
    # Run 1
    svc.calculate_and_save_consistency("test", "SHORT")
    updated = disc_session.query(CompanyTechnicalMetric).first()
    assert updated.consistency_available is True
    assert updated.company_return == 50.0
    assert updated.calculation_details["existing_key"] == "exists"
    assert "consistency" in updated.calculation_details
    
    # Run 2
    svc.calculate_and_save_consistency("test", "SHORT")
    updated2 = disc_session.query(CompanyTechnicalMetric).first()
    assert updated2.consistency_available is True


# ------------------------------------------------------------------ #
#  16. Bulk-query behavior                                             #
# ------------------------------------------------------------------ #

def test_bulk_query_behavior(disc_session):
    bench_dates = _populate_benchmark(disc_session, 100)
    as_of = bench_dates[-1]
    _populate_metric(disc_session, "test", "ABC", "SHORT", as_of, as_of)
    _populate_metric(disc_session, "test", "XYZ", "SHORT", as_of, as_of)
    
    src = _mock_src_session(candles=[])
    svc = TechnicalConsistencyService(src, disc_session)
    svc.calculate_and_save_consistency("test", "SHORT")
    
    # Exactly one query for candles
    assert src.call_count["candles"] == 1


# ------------------------------------------------------------------ #
#  17. Source database remains untouched                               #
# ------------------------------------------------------------------ #

def test_source_db_not_written(disc_session):
    inspector = inspect(source_engine)
    tables_before = set(inspector.get_table_names())
    
    test_ratio_calculations_and_5050_score(disc_session)
    
    inspector = inspect(source_engine)
    tables_after = set(inspector.get_table_names())
    
    assert tables_before == tables_after
    assert "company_technical_metrics" not in tables_after
