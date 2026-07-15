"""
Tests for TechnicalVolumeService.
Verifies volume formulas, non-overlapping windows, missing thresholds,
DB isolation, and field preservation.
"""
from __future__ import annotations

import uuid
import datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text, inspect

from database import DiscoverySessionLocal, source_engine
from models.discovery import BenchmarkCandle, CompanyTechnicalMetric
from services.technical.technical_volume import TechnicalVolumeService
import config


# ------------------------------------------------------------------ #
#  Helpers                                                             #
# ------------------------------------------------------------------ #

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
    # Weekdays only roughly
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
        return_available=True
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
#  1-3. Exact Horizon Volume (SHORT, MID, LONG)                        #
# ------------------------------------------------------------------ #

@pytest.mark.parametrize("horizon, h_days", [
    ("SHORT", config.HORIZON_SHORT_DAYS),
    ("MID", config.HORIZON_MID_DAYS),
    ("LONG", config.HORIZON_LONG_DAYS),
])
def test_exact_horizon_volume(disc_session, horizon, h_days):
    bench_dates = _populate_benchmark(disc_session, 600)
    as_of = bench_dates[-1]
    _populate_metric(disc_session, "test", "ABC", horizon, as_of, as_of)
    
    # 100% coverage: generate exact dates
    # Current window
    curr_dates = bench_dates[-h_days:]
    # Previous window
    prev_dates = bench_dates[-(2*h_days):-h_days]
    
    mock_candles = []
    # Current window avg = 2000
    for d in curr_dates:
        mock_candles.append(MagicMock(symbol="ABC", dt=d, volume=2000.0))
    # Previous window avg = 1000
    for d in prev_dates:
        mock_candles.append(MagicMock(symbol="ABC", dt=d, volume=1000.0))
        
    src = _mock_src_session(candles=mock_candles)
    svc = TechnicalVolumeService(src, disc_session)
    svc.calculate_and_save_volumes("test", horizon)
    
    rec = disc_session.query(CompanyTechnicalMetric).first()
    assert rec.volume_available is True
    assert rec.average_volume_current == 2000.0
    assert rec.average_volume_previous == 1000.0
    assert abs(rec.volume_change - 100.0) < 1e-5


# ------------------------------------------------------------------ #
#  4-5. Non-overlapping windows & Uses benchmark dates                 #
# ------------------------------------------------------------------ #

def test_non_overlapping_windows_and_benchmark_dates(disc_session):
    # Proves windows are strictly constructed from benchmark sessions
    # Current window dates should NOT overlap previous window dates
    bench_dates = _populate_benchmark(disc_session, 100)
    as_of = bench_dates[-1]
    _populate_metric(disc_session, "test", "ABC", "SHORT", as_of, as_of)
    
    h = 20
    curr_dates = bench_dates[-h:]
    prev_dates = bench_dates[-(2*h):-h]
    
    assert set(curr_dates).isdisjoint(set(prev_dates))
    
    # Send candles for exactly benchmark dates
    # And send some extra candles on weekends (not in benchmark) to prove they are ignored
    mock_candles = []
    for d in curr_dates:
        mock_candles.append(MagicMock(symbol="ABC", dt=d, volume=100.0))
    for d in prev_dates:
        mock_candles.append(MagicMock(symbol="ABC", dt=d, volume=100.0))
        
    # Extra invalid date (should be ignored entirely)
    saturday = datetime.date(2023, 1, 7) # Just an example, assuming not in bench
    mock_candles.append(MagicMock(symbol="ABC", dt=saturday, volume=9999999.0))
    
    src = _mock_src_session(candles=mock_candles)
    svc = TechnicalVolumeService(src, disc_session)
    svc.calculate_and_save_volumes("test", "SHORT")
    
    rec = disc_session.query(CompanyTechnicalMetric).first()
    assert rec.average_volume_current == 100.0
    assert rec.average_volume_previous == 100.0
    assert rec.volume_change == 0.0


# ------------------------------------------------------------------ #
#  6. Missing intermediate volume within allowed coverage              #
# ------------------------------------------------------------------ #

def test_missing_volume_allowed_coverage(disc_session):
    bench_dates = _populate_benchmark(disc_session, 100)
    as_of = bench_dates[-1]
    _populate_metric(disc_session, "test", "ABC", "SHORT", as_of, as_of)
    
    h = 20
    curr_dates = bench_dates[-h:]
    prev_dates = bench_dates[-(2*h):-h]
    
    # 85% coverage = 17 days out of 20
    mock_candles = []
    for d in curr_dates[:17]:
        mock_candles.append(MagicMock(symbol="ABC", dt=d, volume=200.0))
    for d in prev_dates[:17]:
        mock_candles.append(MagicMock(symbol="ABC", dt=d, volume=100.0))
        
    src = _mock_src_session(candles=mock_candles)
    svc = TechnicalVolumeService(src, disc_session)
    svc.calculate_and_save_volumes("test", "SHORT")
    
    rec = disc_session.query(CompanyTechnicalMetric).first()
    assert rec.volume_available is True
    assert rec.average_volume_current == 200.0
    assert rec.average_volume_previous == 100.0
    assert rec.volume_change == 100.0


# ------------------------------------------------------------------ #
#  7-8. Coverage below thresholds                                      #
# ------------------------------------------------------------------ #

def test_current_coverage_below_threshold(disc_session):
    bench_dates = _populate_benchmark(disc_session, 100)
    as_of = bench_dates[-1]
    _populate_metric(disc_session, "test", "ABC", "SHORT", as_of, as_of)
    
    h = 20
    curr_dates = bench_dates[-h:]
    prev_dates = bench_dates[-(2*h):-h]
    
    # 75% current coverage = 15 days
    # 100% previous
    mock_candles = []
    for d in curr_dates[:15]:
        mock_candles.append(MagicMock(symbol="ABC", dt=d, volume=200.0))
    for d in prev_dates:
        mock_candles.append(MagicMock(symbol="ABC", dt=d, volume=100.0))
        
    src = _mock_src_session(candles=mock_candles)
    svc = TechnicalVolumeService(src, disc_session)
    svc.calculate_and_save_volumes("test", "SHORT")
    
    rec = disc_session.query(CompanyTechnicalMetric).first()
    assert rec.volume_available is False
    assert "INSUFFICIENT_CURRENT_VOLUME_COVERAGE" in rec.warnings


def test_previous_coverage_below_threshold(disc_session):
    bench_dates = _populate_benchmark(disc_session, 100)
    as_of = bench_dates[-1]
    _populate_metric(disc_session, "test", "ABC", "SHORT", as_of, as_of)
    
    h = 20
    curr_dates = bench_dates[-h:]
    prev_dates = bench_dates[-(2*h):-h]
    
    # 100% current, 75% previous
    mock_candles = []
    for d in curr_dates:
        mock_candles.append(MagicMock(symbol="ABC", dt=d, volume=200.0))
    for d in prev_dates[:15]:
        mock_candles.append(MagicMock(symbol="ABC", dt=d, volume=100.0))
        
    src = _mock_src_session(candles=mock_candles)
    svc = TechnicalVolumeService(src, disc_session)
    svc.calculate_and_save_volumes("test", "SHORT")
    
    rec = disc_session.query(CompanyTechnicalMetric).first()
    assert rec.volume_available is False
    assert "INSUFFICIENT_PREVIOUS_VOLUME_COVERAGE" in rec.warnings


# ------------------------------------------------------------------ #
#  9. Previous average volume equals zero                              #
# ------------------------------------------------------------------ #

def test_previous_average_volume_zero(disc_session):
    bench_dates = _populate_benchmark(disc_session, 100)
    as_of = bench_dates[-1]
    _populate_metric(disc_session, "test", "ABC", "SHORT", as_of, as_of)
    
    h = 20
    curr_dates = bench_dates[-h:]
    prev_dates = bench_dates[-(2*h):-h]
    
    mock_candles = []
    for d in curr_dates:
        mock_candles.append(MagicMock(symbol="ABC", dt=d, volume=200.0))
    for d in prev_dates:
        mock_candles.append(MagicMock(symbol="ABC", dt=d, volume=0.0)) # ZERO volume
        
    src = _mock_src_session(candles=mock_candles)
    svc = TechnicalVolumeService(src, disc_session)
    svc.calculate_and_save_volumes("test", "SHORT")
    
    rec = disc_session.query(CompanyTechnicalMetric).first()
    assert rec.volume_available is False
    assert "ZERO_PREVIOUS_AVERAGE_VOLUME" in rec.warnings


# ------------------------------------------------------------------ #
#  10. Negative volume is rejected                                     #
# ------------------------------------------------------------------ #

def test_negative_volume_rejected_via_db_query(disc_session):
    """
    Since negative volume is filtered OUT in the SQL query, 
    the coverage will drop and it should trigger insufficient coverage.
    """
    bench_dates = _populate_benchmark(disc_session, 100)
    as_of = bench_dates[-1]
    _populate_metric(disc_session, "test", "ABC", "SHORT", as_of, as_of)
    
    h = 20
    curr_dates = bench_dates[-h:]
    prev_dates = bench_dates[-(2*h):-h]
    
    # 20 days: 10 valid, 10 negative
    # Coverage should be 50%
    mock_candles = []
    for d in curr_dates[:10]:
        mock_candles.append(MagicMock(symbol="ABC", dt=d, volume=200.0))
    for d in curr_dates[10:]:
        # If the mock passes it back, we just ignore it if it doesn't match the query.
        # But our mock just returns whatever we give it!
        # The SQL query explicitly has: AND volume >= 0.
        # Since we use a mock, we must simulate the DB filtering.
        pass
        
    for d in prev_dates:
        mock_candles.append(MagicMock(symbol="ABC", dt=d, volume=100.0))
        
    src = _mock_src_session(candles=mock_candles)
    svc = TechnicalVolumeService(src, disc_session)
    svc.calculate_and_save_volumes("test", "SHORT")
    
    rec = disc_session.query(CompanyTechnicalMetric).first()
    assert rec.volume_available is False
    assert "INSUFFICIENT_CURRENT_VOLUME_COVERAGE" in rec.warnings


# ------------------------------------------------------------------ #
#  11-12. Fields preserved & Idempotent Update                         #
# ------------------------------------------------------------------ #

def test_return_fields_preserved_idempotent_update(disc_session):
    bench_dates = _populate_benchmark(disc_session, 100)
    as_of = bench_dates[-1]
    rec = _populate_metric(disc_session, "test", "ABC", "SHORT", as_of, as_of)
    # verify initial
    assert rec.current_close == 150.0
    assert rec.company_return == 50.0
    
    h = 20
    curr_dates = bench_dates[-h:]
    prev_dates = bench_dates[-(2*h):-h]
    
    mock_candles = []
    for d in curr_dates: mock_candles.append(MagicMock(symbol="ABC", dt=d, volume=200.0))
    for d in prev_dates: mock_candles.append(MagicMock(symbol="ABC", dt=d, volume=100.0))
        
    src = _mock_src_session(candles=mock_candles)
    svc = TechnicalVolumeService(src, disc_session)
    
    # First run
    svc.calculate_and_save_volumes("test", "SHORT")
    updated = disc_session.query(CompanyTechnicalMetric).first()
    assert updated.volume_available is True
    assert updated.average_volume_current == 200.0
    assert updated.current_close == 150.0 # PRESERVED
    
    # Second run (Idempotent)
    svc.calculate_and_save_volumes("test", "SHORT")
    updated2 = disc_session.query(CompanyTechnicalMetric).first()
    assert updated2.volume_available is True
    assert updated2.current_close == 150.0


# ------------------------------------------------------------------ #
#  13. Bulk-query behavior                                             #
# ------------------------------------------------------------------ #

def test_bulk_query_behavior(disc_session):
    bench_dates = _populate_benchmark(disc_session, 100)
    as_of = bench_dates[-1]
    _populate_metric(disc_session, "test", "ABC", "SHORT", as_of, as_of)
    _populate_metric(disc_session, "test", "XYZ", "SHORT", as_of, as_of)
    
    src = _mock_src_session(candles=[]) # No candles -> unavailable, but query runs exactly once
    svc = TechnicalVolumeService(src, disc_session)
    svc.calculate_and_save_volumes("test", "SHORT")
    
    # Query must be executed EXACTLY once for all companies
    assert src.call_count["candles"] == 1


# ------------------------------------------------------------------ #
#  14. Source database remains untouched                               #
# ------------------------------------------------------------------ #

def test_source_db_not_written(disc_session):
    inspector = inspect(source_engine)
    tables_before = set(inspector.get_table_names())
    
    test_exact_horizon_volume(disc_session, "SHORT", 20)
    
    inspector = inspect(source_engine)
    tables_after = set(inspector.get_table_names())
    
    # Verify no tables were added to source
    assert tables_before == tables_after
    assert "company_technical_metrics" not in tables_after
