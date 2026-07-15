"""
TechnicalDateAlignmentService

Aligns company candles in the source database with the imported
NIFTY500 benchmark candles in the discovery database.

Does NOT calculate any returns, volumes, or scores.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

import config

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
#  Data structures                                                     #
# ------------------------------------------------------------------ #

@dataclass
class CompanyAlignment:
    source_company_id: str
    symbol: str
    as_of_date: Optional[date]
    benchmark_candle_date: Optional[date]
    company_candle_date: Optional[date]
    staleness_sessions: int        # how many benchmark sessions behind
    available: bool
    warnings: list[str] = field(default_factory=list)


@dataclass
class AlignmentResult:
    status: str                    # READY | BENCHMARK_DATA_UNAVAILABLE |
                                   # INSUFFICIENT_HISTORY | INVALID_BENCHMARK_DATA
    horizon: str
    as_of_date: Optional[date]
    benchmark_candle_date: Optional[date]
    total_benchmark_sessions: int
    companies: list[CompanyAlignment] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ #
#  Service                                                             #
# ------------------------------------------------------------------ #

class TechnicalDateAlignmentService:
    """
    Aligns company candles to the NIFTY500 benchmark trading dates.

    Parameters
    ----------
    source_session  : read-only SQLAlchemy session connected to source DB.
    discovery_session : read-write SQLAlchemy session connected to discovery DB.
    benchmark_code  : benchmark identifier stored in benchmark_candles table.
                      Defaults to config.PRIMARY_TECHNICAL_BENCHMARK ("NIFTY500").
    max_staleness   : maximum allowed session lag before a company is marked unavailable.
                      Defaults to config.MAX_COMPANY_CANDLE_STALENESS_SESSIONS.
    """

    def __init__(
        self,
        source_session: Session,
        discovery_session: Session,
        benchmark_code: str = None,
        max_staleness: int = None,
    ):
        self._src = source_session
        self._disc = discovery_session
        self._benchmark_code = benchmark_code or config.PRIMARY_TECHNICAL_BENCHMARK
        self._max_staleness = max_staleness if max_staleness is not None \
            else config.MAX_COMPANY_CANDLE_STALENESS_SESSIONS

    # ---------------------------------------------------------------- #
    #  Public API                                                        #
    # ---------------------------------------------------------------- #

    def align(self, horizon: str) -> AlignmentResult:
        """
        Main entry point.  Returns an AlignmentResult with a status and,
        when READY, a CompanyAlignment record per eligible company.
        """
        # 1. Confirm benchmark data exists and is sufficient.
        benchmark_dates, status_error = self._load_benchmark_dates(horizon)
        if status_error:
            return AlignmentResult(
                status=status_error,
                horizon=horizon,
                as_of_date=None,
                benchmark_candle_date=None,
                total_benchmark_sessions=0,
                errors=[status_error],
            )

        # benchmark_dates is an ordered list (ascending) of date objects.
        as_of = benchmark_dates[-1]          # latest trading date
        total_sessions = len(benchmark_dates)

        # Build a fast lookup: date → 0-based position in the sorted list.
        # Position of as_of_date = total_sessions - 1.
        date_to_idx: dict[date, int] = {d: i for i, d in enumerate(benchmark_dates)}

        # 2. Fetch companies and their latest candle dates in bulk (one query).
        company_rows = self._fetch_companies()
        company_symbols = [r.share_symbol.strip() for r in company_rows if r.share_symbol]

        # 3. Bulk-fetch the latest candle date on or before as_of_date per symbol.
        latest_candle_by_symbol = self._fetch_latest_candles_bulk(company_symbols, as_of)

        # 4. Compute alignment for every company.
        alignments: list[CompanyAlignment] = []
        for row in company_rows:
            sym = (row.share_symbol or "").strip()
            alignment = self._align_company(
                source_company_id=row.id,
                symbol=sym,
                as_of=as_of,
                latest_candle=latest_candle_by_symbol.get(sym),
                date_to_idx=date_to_idx,
                as_of_idx=total_sessions - 1,
            )
            alignments.append(alignment)

        return AlignmentResult(
            status="READY",
            horizon=horizon,
            as_of_date=as_of,
            benchmark_candle_date=as_of,
            total_benchmark_sessions=total_sessions,
            companies=alignments,
        )

    # ---------------------------------------------------------------- #
    #  Internal helpers                                                  #
    # ---------------------------------------------------------------- #

    def _load_benchmark_dates(self, horizon: str) -> tuple[list[date] | None, str | None]:
        """
        Fetches all benchmark trading dates from the discovery DB.
        Returns (sorted_date_list, None) on success, or (None, status_string) on failure.
        """
        rows = self._disc.execute(
            text("""
                SELECT trade_date
                FROM benchmark_candles
                WHERE benchmark_code = :code
                  AND close > 0
                ORDER BY trade_date ASC
            """),
            {"code": self._benchmark_code},
        ).fetchall()

        if not rows:
            return None, "BENCHMARK_DATA_UNAVAILABLE"

        # Reject if any non-positive closes snuck through (belt-and-suspenders).
        dates: list[date] = [r.trade_date for r in rows]

        # Check for duplicate dates (data integrity).
        if len(dates) != len(set(dates)):
            return None, "INVALID_BENCHMARK_DATA"

        # Horizon minimum requirements (ordered trading rows, not calendar days).
        min_required = {
            "SHORT": config.BENCHMARK_MIN_SHORT,
            "MID":   config.BENCHMARK_MIN_MID,
            "LONG":  config.BENCHMARK_MIN_LONG,
        }.get(horizon, config.BENCHMARK_MIN_SHORT)

        if len(dates) < min_required:
            return None, "INSUFFICIENT_HISTORY"

        return dates, None

    def _fetch_companies(self):
        """Return all companies that have a non-empty share_symbol."""
        return self._src.execute(
            text("""
                SELECT id, share_symbol
                FROM companies
                WHERE share_symbol IS NOT NULL
                  AND share_symbol != ''
            """)
        ).fetchall()

    def _fetch_latest_candles_bulk(
        self, symbols: list[str], as_of: date
    ) -> dict[str, date]:
        """
        Single query: for each symbol in market_candles_cleaned, return the
        latest datetime on or before as_of_date.

        Returns {symbol: latest_candle_date}.
        Uses a VALUES list to pass symbols without risk of SQL injection.
        Falls back gracefully when the symbol list is empty.
        """
        if not symbols:
            return {}

        as_of_str = as_of.isoformat()[:10] + "T23:59:59"

        result = self._src.execute(
            text("""
                SELECT symbol,
                       MAX(SUBSTRING(datetime FROM 1 FOR 10)) AS latest_date
                FROM market_candles_cleaned
                WHERE symbol = ANY(:syms)
                  AND datetime <= :as_of
                GROUP BY symbol
            """),
            {"syms": symbols, "as_of": as_of_str},
        ).fetchall()

        return {r.symbol.strip(): date.fromisoformat(r.latest_date) for r in result if r.latest_date}

    def _align_company(
        self,
        source_company_id: str,
        symbol: str,
        as_of: date,
        latest_candle: Optional[date],
        date_to_idx: dict[date, int],
        as_of_idx: int,
    ) -> CompanyAlignment:
        warnings: list[str] = []

        if not symbol:
            return CompanyAlignment(
                source_company_id=source_company_id,
                symbol=symbol,
                as_of_date=as_of,
                benchmark_candle_date=as_of,
                company_candle_date=None,
                staleness_sessions=0,
                available=False,
                warnings=["NO_SYMBOL"],
            )

        if latest_candle is None:
            return CompanyAlignment(
                source_company_id=source_company_id,
                symbol=symbol,
                as_of_date=as_of,
                benchmark_candle_date=as_of,
                company_candle_date=None,
                staleness_sessions=0,
                available=False,
                warnings=["NO_CANDLE_DATA"],
            )

        # Candles after the benchmark as_of_date are never used.
        if latest_candle > as_of:
            latest_candle = as_of

        # Compute staleness as the number of benchmark trading sessions
        # between the company's latest candle and the as_of date.
        company_idx = date_to_idx.get(latest_candle)

        if company_idx is None:
            # Company's latest candle is not a benchmark trading day.
            # Walk backward through benchmark dates to find the closest
            # earlier benchmark session.
            earlier = [d for d in date_to_idx if d <= latest_candle]
            if earlier:
                closest = max(earlier)
                company_idx = date_to_idx[closest]
                staleness = as_of_idx - company_idx
            else:
                # Candle predates all benchmark history entirely.
                staleness = as_of_idx + 1   # treat as beyond threshold
        else:
            staleness = as_of_idx - company_idx

        available = staleness <= self._max_staleness
        if staleness > 0 and available:
            warnings.append(f"STALE_COMPANY_CANDLE_SESSIONS_{staleness}")
        elif not available:
            warnings.append("STALE_COMPANY_CANDLE")

        return CompanyAlignment(
            source_company_id=source_company_id,
            symbol=symbol,
            as_of_date=as_of,
            benchmark_candle_date=as_of,
            company_candle_date=latest_candle,
            staleness_sessions=staleness,
            available=available,
            warnings=warnings,
        )
