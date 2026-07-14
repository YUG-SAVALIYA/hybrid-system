"""
Tests for TechnicalDateAlignmentService.

Uses mocked SQLAlchemy sessions – no real DB round-trips except for the
source-DB isolation check (which only inspects, never writes).
"""
from __future__ import annotations

import uuid
import datetime
from unittest.mock import MagicMock, patch

import pytest

from services.technical.technical_date_alignment import (
    TechnicalDateAlignmentService,
    CompanyAlignment,
    AlignmentResult,
)


# ------------------------------------------------------------------ #
#  Helpers                                                             #
# ------------------------------------------------------------------ #

def _date(s: str) -> datetime.date:
    return datetime.date.fromisoformat(s)


def _company(symbol: str, cid: str = None):
    row = MagicMock()
    row.id = cid or str(uuid.uuid4())
    row.share_symbol = symbol
    return row


def _build_service(
    benchmark_dates: list[datetime.date] | None,
    company_rows: list,
    candle_by_symbol: dict[str, datetime.date],
    max_staleness: int = 3,
    benchmark_code: str = "NIFTY_500",
    duplicate_bench_dates: bool = False,
):
    """
    Returns a TechnicalDateAlignmentService with fully mocked sessions.
    benchmark_dates=None → no rows returned (unavailable).
    """
    disc_session = MagicMock()
    src_session = MagicMock()

    # --- Discovery session: benchmark_candles query ---
    if benchmark_dates is None:
        bench_rows = []
    else:
        bench_rows = [MagicMock(trade_date=d) for d in benchmark_dates]
        if duplicate_bench_dates and benchmark_dates:
            bench_rows.append(MagicMock(trade_date=benchmark_dates[0]))

    disc_session.execute.return_value.fetchall.return_value = bench_rows

    # --- Source session: companies + candle queries ---
    # We need to discriminate which query is being called.
    def src_execute(query_text, params=None):
        result = MagicMock()
        sql = str(query_text).lower()
        if "companies" in sql:
            result.fetchall.return_value = company_rows
        else:
            # candle bulk query
            candle_rows = [
                MagicMock(symbol=sym, latest_date=dt)
                for sym, dt in candle_by_symbol.items()
            ]
            result.fetchall.return_value = candle_rows
        return result

    src_session.execute.side_effect = src_execute

    service = TechnicalDateAlignmentService(
        source_session=src_session,
        discovery_session=disc_session,
        benchmark_code=benchmark_code,
        max_staleness=max_staleness,
    )
    return service


def _make_dates(n: int, start: str = "2024-01-02") -> list[datetime.date]:
    """Generate n consecutive dates starting from start."""
    base = _date(start)
    return [datetime.date.fromordinal(base.toordinal() + i) for i in range(n)]


# ------------------------------------------------------------------ #
#  1. Benchmark unavailable                                            #
# ------------------------------------------------------------------ #

def test_benchmark_unavailable():
    svc = _build_service(benchmark_dates=None, company_rows=[], candle_by_symbol={})
    result = svc.align("SHORT")
    assert result.status == "BENCHMARK_DATA_UNAVAILABLE"
    assert result.companies == []


# ------------------------------------------------------------------ #
#  2. Insufficient benchmark history                                   #
# ------------------------------------------------------------------ #

def test_insufficient_history_short():
    """SHORT requires 21 sessions; give only 10."""
    dates = _make_dates(10)
    svc = _build_service(benchmark_dates=dates, company_rows=[], candle_by_symbol={})
    result = svc.align("SHORT")
    assert result.status == "INSUFFICIENT_HISTORY"


def test_insufficient_history_long():
    """LONG requires 253 sessions; give only 100."""
    dates = _make_dates(100)
    svc = _build_service(benchmark_dates=dates, company_rows=[], candle_by_symbol={})
    result = svc.align("LONG")
    assert result.status == "INSUFFICIENT_HISTORY"


# ------------------------------------------------------------------ #
#  3. Latest benchmark date selection                                  #
# ------------------------------------------------------------------ #

def test_latest_benchmark_date_is_as_of_date():
    dates = _make_dates(30)
    svc = _build_service(benchmark_dates=dates, company_rows=[], candle_by_symbol={})
    result = svc.align("SHORT")
    assert result.status == "READY"
    assert result.as_of_date == dates[-1]
    assert result.benchmark_candle_date == dates[-1]


# ------------------------------------------------------------------ #
#  4. Exact company-date match (0 sessions behind)                     #
# ------------------------------------------------------------------ #

def test_exact_date_match_available():
    dates = _make_dates(30)
    as_of = dates[-1]
    company = _company("ABC")
    svc = _build_service(
        benchmark_dates=dates,
        company_rows=[company],
        candle_by_symbol={"ABC": as_of},
    )
    result = svc.align("SHORT")
    assert result.status == "READY"
    alignment = result.companies[0]
    assert alignment.symbol == "ABC"
    assert alignment.company_candle_date == as_of
    assert alignment.staleness_sessions == 0
    assert alignment.available is True
    assert alignment.warnings == []


# ------------------------------------------------------------------ #
#  5. Company one session behind                                        #
# ------------------------------------------------------------------ #

def test_one_session_behind():
    dates = _make_dates(30)
    as_of = dates[-1]
    company = _company("DEF")
    svc = _build_service(
        benchmark_dates=dates,
        company_rows=[company],
        candle_by_symbol={"DEF": dates[-2]},   # 1 session behind
    )
    result = svc.align("SHORT")
    alignment = result.companies[0]
    assert alignment.staleness_sessions == 1
    assert alignment.available is True
    assert any("STALE_COMPANY_CANDLE_SESSIONS_1" in w for w in alignment.warnings)


# ------------------------------------------------------------------ #
#  6. Company three sessions behind (boundary – still available)       #
# ------------------------------------------------------------------ #

def test_three_sessions_behind_still_available():
    dates = _make_dates(30)
    company = _company("GHI")
    svc = _build_service(
        benchmark_dates=dates,
        company_rows=[company],
        candle_by_symbol={"GHI": dates[-4]},   # 3 sessions behind
    )
    result = svc.align("SHORT")
    alignment = result.companies[0]
    assert alignment.staleness_sessions == 3
    assert alignment.available is True
    assert any("STALE_COMPANY_CANDLE_SESSIONS_3" in w for w in alignment.warnings)


# ------------------------------------------------------------------ #
#  7. Company more than three sessions behind (unavailable)            #
# ------------------------------------------------------------------ #

def test_four_sessions_behind_unavailable():
    dates = _make_dates(30)
    company = _company("JKL")
    svc = _build_service(
        benchmark_dates=dates,
        company_rows=[company],
        candle_by_symbol={"JKL": dates[-5]},   # 4 sessions behind
        max_staleness=3,
    )
    result = svc.align("SHORT")
    alignment = result.companies[0]
    assert alignment.staleness_sessions == 4
    assert alignment.available is False
    assert "STALE_COMPANY_CANDLE" in alignment.warnings


# ------------------------------------------------------------------ #
#  8. Company candle after benchmark date is ignored                   #
# ------------------------------------------------------------------ #

def test_company_candle_after_benchmark_date_ignored():
    """
    If a company has a candle dated after the benchmark as_of_date,
    the service must clamp it to as_of_date and compute staleness from there.
    """
    dates = _make_dates(30)
    as_of = dates[-1]
    future_date = datetime.date.fromordinal(as_of.toordinal() + 5)
    company = _company("MNO")
    svc = _build_service(
        benchmark_dates=dates,
        company_rows=[company],
        candle_by_symbol={"MNO": future_date},
    )
    result = svc.align("SHORT")
    alignment = result.companies[0]
    # Clamped to as_of → staleness = 0
    assert alignment.company_candle_date == as_of
    assert alignment.staleness_sessions == 0
    assert alignment.available is True


# ------------------------------------------------------------------ #
#  9. Company without candle data                                       #
# ------------------------------------------------------------------ #

def test_company_without_candle_unavailable():
    dates = _make_dates(30)
    company = _company("PQR")
    svc = _build_service(
        benchmark_dates=dates,
        company_rows=[company],
        candle_by_symbol={},   # no entry for PQR
    )
    result = svc.align("SHORT")
    alignment = result.companies[0]
    assert alignment.available is False
    assert "NO_CANDLE_DATA" in alignment.warnings


# ------------------------------------------------------------------ #
#  10. No NIFTY 50 fallback                                            #
# ------------------------------------------------------------------ #

def test_no_nifty50_fallback_when_nifty500_missing():
    """
    Service must return BENCHMARK_DATA_UNAVAILABLE even when NIFTY 50
    data would theoretically exist; benchmark_code is never changed.
    """
    # Provide no NIFTY_500 data (empty list).
    svc = _build_service(
        benchmark_dates=None,
        company_rows=[],
        candle_by_symbol={},
        benchmark_code="NIFTY_500",
    )
    result = svc.align("SHORT")
    assert result.status == "BENCHMARK_DATA_UNAVAILABLE"
    # Verify the benchmark code was never mutated to NIFTY_50 or similar.
    assert svc._benchmark_code == "NIFTY_500"


# ------------------------------------------------------------------ #
#  11. Bulk-query behavior (only 2 source-DB queries per align call)   #
# ------------------------------------------------------------------ #

def test_bulk_query_single_candle_fetch():
    """
    Regardless of how many companies are present, the service must
    call the candle query exactly once (bulk, not one-per-company).
    """
    dates = _make_dates(30)
    as_of = dates[-1]
    companies = [_company(f"SYM{i}") for i in range(10)]
    candles = {f"SYM{i}": dates[-1] for i in range(10)}

    disc_session = MagicMock()
    src_session = MagicMock()

    bench_rows = [MagicMock(trade_date=d) for d in dates]
    disc_session.execute.return_value.fetchall.return_value = bench_rows

    call_count = {"candle": 0, "company": 0}

    def src_execute(query_text, params=None):
        result = MagicMock()
        sql = str(query_text).lower()
        if "companies" in sql:
            call_count["company"] += 1
            result.fetchall.return_value = companies
        else:
            call_count["candle"] += 1
            result.fetchall.return_value = [
                MagicMock(symbol=sym, latest_date=dt)
                for sym, dt in candles.items()
            ]
        return result

    src_session.execute.side_effect = src_execute

    svc = TechnicalDateAlignmentService(
        source_session=src_session,
        discovery_session=disc_session,
        benchmark_code="NIFTY_500",
    )
    result = svc.align("SHORT")

    assert result.status == "READY"
    assert call_count["company"] == 1, "Should fetch companies in one query"
    assert call_count["candle"] == 1, "Should fetch all candles in one bulk query"


# ------------------------------------------------------------------ #
#  12. Source database remains untouched                               #
# ------------------------------------------------------------------ #

def test_source_db_not_written():
    """
    The service must never call commit() or execute a write statement
    against the source session.
    """
    dates = _make_dates(30)
    src_session = MagicMock()
    disc_session = MagicMock()

    disc_session.execute.return_value.fetchall.return_value = [
        MagicMock(trade_date=d) for d in dates
    ]

    def src_execute(query_text, params=None):
        result = MagicMock()
        sql = str(query_text).lower()
        if "companies" in sql:
            result.fetchall.return_value = []
        else:
            result.fetchall.return_value = []
        return result

    src_session.execute.side_effect = src_execute

    svc = TechnicalDateAlignmentService(
        source_session=src_session,
        discovery_session=disc_session,
    )
    svc.align("SHORT")

    # commit and rollback must never be called on source
    src_session.commit.assert_not_called()
    src_session.rollback.assert_not_called()


# ------------------------------------------------------------------ #
#  13. Invalid benchmark data (duplicate dates)                        #
# ------------------------------------------------------------------ #

def test_invalid_benchmark_data_duplicate_dates():
    dates = _make_dates(30)
    svc = _build_service(
        benchmark_dates=dates,
        company_rows=[],
        candle_by_symbol={},
        duplicate_bench_dates=True,
    )
    result = svc.align("SHORT")
    assert result.status == "INVALID_BENCHMARK_DATA"
