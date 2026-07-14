"""
Tests for TechnicalReturnService.
Verifies return formulas, alignment fetching, persistence, and DB isolation.
"""
from __future__ import annotations

import uuid
import datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text, inspect

from database import DiscoverySessionLocal, source_engine, discovery_engine
from models.discovery import BenchmarkCandle, CompanyTechnicalMetric
from services.technical.technical_date_alignment import AlignmentResult, CompanyAlignment
from services.technical.technical_return import TechnicalReturnService
import config


# ------------------------------------------------------------------ #
#  Helpers                                                             #
# ------------------------------------------------------------------ #

def _date(s: str) -> datetime.date:
    return datetime.date.fromisoformat(s)

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

def _populate_benchmark(session, n=300):
    base_date = datetime.date(2023, 1, 1)
    candles = []
    # Simple linear price: day 0 = 100.0, day 1 = 101.0, ...
    for i in range(n):
        candles.append(
            BenchmarkCandle(
                id=str(uuid.uuid4()),
                benchmark_code="NIFTY_500",
                benchmark_name="Nifty 500",
                trade_date=datetime.date.fromordinal(base_date.toordinal() + i),
                open=100.0 + i,
                high=100.0 + i,
                low=100.0 + i,
                close=100.0 + i,
                source_name="TEST",
                import_batch_id="test"
            )
        )
    session.add_all(candles)
    session.commit()
    return sorted([c.trade_date for c in candles])

def _mock_src_session(company_info=None, candles=None):
    if company_info is None:
        company_info = [MagicMock(share_symbol="ABC", sectore="Tech", industry="Software", categorized_industry="SaaS")]
    if candles is None:
        candles = []
        
    src = MagicMock()
    call_count = {"companies": 0, "candles": 0}
    
    def _execute(query_text, params=None):
        sql = str(query_text).lower()
        res = MagicMock()
        if "companies" in sql:
            call_count["companies"] += 1
            res.fetchall.return_value = company_info
        elif "market_candles" in sql:
            call_count["candles"] += 1
            res.fetchall.return_value = candles
        return res
        
    src.execute.side_effect = _execute
    src.call_count = call_count
    return src


# ------------------------------------------------------------------ #
#  1-3. Exact Horizon Returns (SHORT, MID, LONG)                       #
# ------------------------------------------------------------------ #

@pytest.mark.parametrize("horizon, h_days", [
    ("SHORT", config.HORIZON_SHORT_DAYS),
    ("MID", config.HORIZON_MID_DAYS),
    ("LONG", config.HORIZON_LONG_DAYS),
])
def test_exact_horizon_return(disc_session, horizon, h_days):
    bench_dates = _populate_benchmark(disc_session, 300)
    as_of = bench_dates[-1]  # index 299
    
    # Benchmark prices:
    b_curr = 399.0
    b_start = 399.0 - h_days
    b_expected = ((b_curr / b_start) - 1.0) * 100.0
    
    # Company prices:
    c_curr = 150.0
    c_start = 100.0
    c_expected = ((150.0 / 100.0) - 1.0) * 100.0
    
    src = _mock_src_session(
        candles=[
            MagicMock(symbol="ABC", dt=as_of, close=c_curr),
            MagicMock(symbol="ABC", dt=bench_dates[-(1+h_days)], close=c_start)
        ]
    )
    
    align = AlignmentResult(
        status="READY", horizon=horizon, as_of_date=as_of, benchmark_candle_date=as_of, total_benchmark_sessions=300,
        companies=[CompanyAlignment(source_company_id="123", symbol="ABC", as_of_date=as_of, benchmark_candle_date=as_of, company_candle_date=as_of, staleness_sessions=0, available=True)]
    )
    
    svc = TechnicalReturnService(src, disc_session)
    run_id = "test_run"
    svc.calculate_and_save_returns(run_id, align)
    
    rec = disc_session.query(CompanyTechnicalMetric).filter_by(run_id=run_id, symbol="ABC").first()
    assert rec is not None
    assert rec.return_available is True
    assert rec.current_close == c_curr
    assert rec.start_close == c_start
    assert rec.benchmark_current_close == b_curr
    assert rec.benchmark_start_close == b_start
    assert abs(rec.company_return - c_expected) < 1e-5
    assert abs(rec.benchmark_return - b_expected) < 1e-5


# ------------------------------------------------------------------ #
#  4-5. Relative Return (Positive / Negative)                          #
# ------------------------------------------------------------------ #

def test_relative_return_positive_and_negative(disc_session):
    bench_dates = _populate_benchmark(disc_session, 50)
    as_of = bench_dates[-1]
    # h = 20 for SHORT
    b_curr = 149.0
    b_start = 129.0
    b_ret = ((b_curr / b_start) - 1) * 100

    # Company 1: beats benchmark (Pos)
    c1_curr = 200.0; c1_start = 100.0  # +100%
    # Company 2: loses to benchmark (Neg)
    c2_curr = 100.0; c2_start = 100.0  # +0%
    
    src = _mock_src_session(
        company_info=[
            MagicMock(share_symbol="POS", sectore="", industry="", categorized_industry=""),
            MagicMock(share_symbol="NEG", sectore="", industry="", categorized_industry="")
        ],
        candles=[
            MagicMock(symbol="POS", dt=as_of, close=c1_curr),
            MagicMock(symbol="POS", dt=bench_dates[-21], close=c1_start),
            MagicMock(symbol="NEG", dt=as_of, close=c2_curr),
            MagicMock(symbol="NEG", dt=bench_dates[-21], close=c2_start),
        ]
    )
    
    align = AlignmentResult(
        status="READY", horizon="SHORT", as_of_date=as_of, benchmark_candle_date=as_of, total_benchmark_sessions=50,
        companies=[
            CompanyAlignment(source_company_id="1", symbol="POS", as_of_date=as_of, benchmark_candle_date=as_of, company_candle_date=as_of, staleness_sessions=0, available=True),
            CompanyAlignment(source_company_id="2", symbol="NEG", as_of_date=as_of, benchmark_candle_date=as_of, company_candle_date=as_of, staleness_sessions=0, available=True)
        ]
    )
    
    svc = TechnicalReturnService(src, disc_session)
    svc.calculate_and_save_returns("test_run", align)
    
    pos = disc_session.query(CompanyTechnicalMetric).filter_by(symbol="POS").first()
    neg = disc_session.query(CompanyTechnicalMetric).filter_by(symbol="NEG").first()
    
    assert pos.relative_return > 0
    assert abs(pos.relative_return - (100.0 - b_ret)) < 1e-5
    
    assert neg.relative_return < 0
    assert abs(neg.relative_return - (0.0 - b_ret)) < 1e-5


# ------------------------------------------------------------------ #
#  6-7. Aligned Benchmark session matching                             #
# ------------------------------------------------------------------ #

def test_stale_company_uses_matching_benchmark_session(disc_session):
    bench_dates = _populate_benchmark(disc_session, 50)
    as_of = bench_dates[-1]
    
    # 3 sessions behind
    comp_date = bench_dates[-4]
    start_date = bench_dates[-24]
    
    src = _mock_src_session(
        candles=[
            MagicMock(symbol="ABC", dt=comp_date, close=150.0),
            MagicMock(symbol="ABC", dt=start_date, close=100.0)
        ]
    )
    
    align = AlignmentResult(
        status="READY", horizon="SHORT", as_of_date=as_of, benchmark_candle_date=as_of, total_benchmark_sessions=50,
        companies=[
            CompanyAlignment(source_company_id="123", symbol="ABC", as_of_date=as_of, benchmark_candle_date=as_of, company_candle_date=comp_date, staleness_sessions=3, available=True)
        ]
    )
    
    svc = TechnicalReturnService(src, disc_session)
    svc.calculate_and_save_returns("test_run", align)
    
    rec = disc_session.query(CompanyTechnicalMetric).filter_by(symbol="ABC").first()
    assert rec.benchmark_candle_date == str(comp_date)
    assert rec.current_close == 150.0
    # benchmark current close should match bench_dates[-4] -> 100 + 46 = 146.0
    assert rec.benchmark_current_close == 146.0
    # start close should match bench_dates[-24] -> 100 + 26 = 126.0
    assert rec.benchmark_start_close == 126.0


# ------------------------------------------------------------------ #
#  8-9. Missing Exact History Constraints                              #
# ------------------------------------------------------------------ #

def test_missing_middle_candle_is_ignored(disc_session):
    """
    If a company is missing a candle in the middle of the period, but HAS the exact
    start and end date candles, it should calculate the return seamlessly.
    """
    bench_dates = _populate_benchmark(disc_session, 50)
    as_of = bench_dates[-1]
    
    src = _mock_src_session(
        candles=[
            MagicMock(symbol="ABC", dt=as_of, close=150.0),
            MagicMock(symbol="ABC", dt=bench_dates[-21], close=100.0)
            # Notice we do NOT provide any middle candles, but the query explicitly only asked for these two!
        ]
    )
    
    align = AlignmentResult(
        status="READY", horizon="SHORT", as_of_date=as_of, benchmark_candle_date=as_of, total_benchmark_sessions=50,
        companies=[CompanyAlignment(source_company_id="1", symbol="ABC", as_of_date=as_of, benchmark_candle_date=as_of, company_candle_date=as_of, staleness_sessions=0, available=True)]
    )
    
    svc = TechnicalReturnService(src, disc_session)
    svc.calculate_and_save_returns("test", align)
    
    rec = disc_session.query(CompanyTechnicalMetric).first()
    assert rec.return_available is True
    assert rec.company_return == 50.0


def test_missing_start_date_candle(disc_session):
    bench_dates = _populate_benchmark(disc_session, 50)
    as_of = bench_dates[-1]
    
    src = _mock_src_session(
        candles=[
            MagicMock(symbol="ABC", dt=as_of, close=150.0)
            # Missing exact start date
        ]
    )
    
    align = AlignmentResult(
        status="READY", horizon="SHORT", as_of_date=as_of, benchmark_candle_date=as_of, total_benchmark_sessions=50,
        companies=[CompanyAlignment(source_company_id="1", symbol="ABC", as_of_date=as_of, benchmark_candle_date=as_of, company_candle_date=as_of, staleness_sessions=0, available=True)]
    )
    
    svc = TechnicalReturnService(src, disc_session)
    svc.calculate_and_save_returns("test", align)
    
    rec = disc_session.query(CompanyTechnicalMetric).first()
    assert rec.return_available is False
    assert "MISSING_COMPANY_START_DATE_CANDLE" in rec.warnings


def test_missing_end_date_candle(disc_session):
    bench_dates = _populate_benchmark(disc_session, 50)
    as_of = bench_dates[-1]
    
    src = _mock_src_session(
        candles=[
            MagicMock(symbol="ABC", dt=bench_dates[-21], close=100.0)
            # Missing exact end date
        ]
    )
    
    align = AlignmentResult(
        status="READY", horizon="SHORT", as_of_date=as_of, benchmark_candle_date=as_of, total_benchmark_sessions=50,
        companies=[CompanyAlignment(source_company_id="1", symbol="ABC", as_of_date=as_of, benchmark_candle_date=as_of, company_candle_date=as_of, staleness_sessions=0, available=True)]
    )
    
    svc = TechnicalReturnService(src, disc_session)
    svc.calculate_and_save_returns("test", align)
    
    rec = disc_session.query(CompanyTechnicalMetric).first()
    assert rec.return_available is False
    assert "MISSING_COMPANY_END_DATE_CANDLE" in rec.warnings


def test_insufficient_aligned_benchmark_history(disc_session):
    bench_dates = _populate_benchmark(disc_session, 15) # Only 15 days of benchmark history
    as_of = bench_dates[-1]
    
    src = _mock_src_session(
        candles=[
            MagicMock(symbol="ABC", dt=as_of, close=150.0),
            MagicMock(symbol="ABC", dt=as_of, close=100.0) # Doesn't matter, bench fails first
        ]
    )
    
    align = AlignmentResult(
        status="READY", horizon="SHORT", as_of_date=as_of, benchmark_candle_date=as_of, total_benchmark_sessions=15,
        companies=[CompanyAlignment(source_company_id="1", symbol="ABC", as_of_date=as_of, benchmark_candle_date=as_of, company_candle_date=as_of, staleness_sessions=0, available=True)]
    )
    
    svc = TechnicalReturnService(src, disc_session)
    svc.calculate_and_save_returns("test", align)
    
    rec = disc_session.query(CompanyTechnicalMetric).first()
    assert rec.return_available is False
    assert "INSUFFICIENT_ALIGNED_BENCHMARK_HISTORY" in rec.warnings


# ------------------------------------------------------------------ #
#  10. Zero or negative start close                                    #
# ------------------------------------------------------------------ #

def test_zero_or_negative_close(disc_session):
    bench_dates = _populate_benchmark(disc_session, 50)
    as_of = bench_dates[-1]
    
    src = _mock_src_session(
        candles=[
            MagicMock(symbol="ABC", dt=as_of, close=150.0),
            MagicMock(symbol="ABC", dt=bench_dates[-21], close=0.0) # Zero close
        ]
    )
    
    align = AlignmentResult(
        status="READY", horizon="SHORT", as_of_date=as_of, benchmark_candle_date=as_of, total_benchmark_sessions=50,
        companies=[CompanyAlignment(source_company_id="1", symbol="ABC", as_of_date=as_of, benchmark_candle_date=as_of, company_candle_date=as_of, staleness_sessions=0, available=True)]
    )
    
    svc = TechnicalReturnService(src, disc_session)
    svc.calculate_and_save_returns("test", align)
    
    rec = disc_session.query(CompanyTechnicalMetric).first()
    assert rec.return_available is False
    assert "INVALID_COMPANY_CLOSE" in rec.warnings



# ------------------------------------------------------------------ #
#  11-12. Idempotent persistence & volume/consistency preservation     #
# ------------------------------------------------------------------ #

def test_idempotent_persistence_preserves_other_fields(disc_session):
    bench_dates = _populate_benchmark(disc_session, 50)
    as_of = bench_dates[-1]
    
    # First write a record with some volume data
    rec = CompanyTechnicalMetric(
        id=str(uuid.uuid4()), run_id="test", source_company_id="1", symbol="ABC",
        horizon="SHORT", volume_available=True, average_volume_current=500,
        company_consistency_score=0.9
    )
    disc_session.add(rec)
    disc_session.commit()
    
    src = _mock_src_session(
        candles=[
            MagicMock(symbol="ABC", dt=as_of, close=150.0),
            MagicMock(symbol="ABC", dt=bench_dates[-21], close=100.0)
        ]
    )
    align = AlignmentResult(
        status="READY", horizon="SHORT", as_of_date=as_of, benchmark_candle_date=as_of, total_benchmark_sessions=50,
        companies=[CompanyAlignment(source_company_id="1", symbol="ABC", as_of_date=as_of, benchmark_candle_date=as_of, company_candle_date=as_of, staleness_sessions=0, available=True)]
    )
    
    svc = TechnicalReturnService(src, disc_session)
    svc.calculate_and_save_returns("test", align)
    
    updated = disc_session.query(CompanyTechnicalMetric).filter_by(symbol="ABC").first()
    
    # Return fields updated
    assert updated.return_available is True
    assert updated.company_return == 50.0
    
    # Volume/Consistency preserved
    assert updated.volume_available is True
    assert updated.average_volume_current == 500
    assert updated.company_consistency_score == 0.9


# ------------------------------------------------------------------ #
#  13. Bulk-query behavior                                             #
# ------------------------------------------------------------------ #

def test_bulk_query_behavior(disc_session):
    bench_dates = _populate_benchmark(disc_session, 50)
    as_of = bench_dates[-1]
    
    src = _mock_src_session(
        candles=[
            MagicMock(symbol="ABC", dt=as_of, close=150.0),
            MagicMock(symbol="ABC", dt=bench_dates[-21], close=100.0),
            MagicMock(symbol="XYZ", dt=as_of, close=200.0),
            MagicMock(symbol="XYZ", dt=bench_dates[-21], close=100.0)
        ]
    )
    
    align = AlignmentResult(
        status="READY", horizon="SHORT", as_of_date=as_of, benchmark_candle_date=as_of, total_benchmark_sessions=50,
        companies=[
            CompanyAlignment(source_company_id="1", symbol="ABC", as_of_date=as_of, benchmark_candle_date=as_of, company_candle_date=as_of, staleness_sessions=0, available=True),
            CompanyAlignment(source_company_id="2", symbol="XYZ", as_of_date=as_of, benchmark_candle_date=as_of, company_candle_date=as_of, staleness_sessions=0, available=True)
        ]
    )
    
    svc = TechnicalReturnService(src, disc_session)
    svc.calculate_and_save_returns("test", align)
    
    # Should only query companies and candles exactly once each!
    assert src.call_count["companies"] == 1
    assert src.call_count["candles"] == 1


# ------------------------------------------------------------------ #
#  14. Source database remains untouched                               #
# ------------------------------------------------------------------ #

def test_source_db_not_written(disc_session):
    inspector = inspect(source_engine)
    tables_before = set(inspector.get_table_names())
    
    test_exact_horizon_return(disc_session, "SHORT", 20)
    
    inspector = inspect(source_engine)
    tables_after = set(inspector.get_table_names())
    
    # Verify no tables were added to source (like company_technical_metrics)
    assert tables_before == tables_after
    assert "company_technical_metrics" not in tables_after
