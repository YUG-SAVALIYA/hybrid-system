from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Optional
import uuid
import datetime
import config
from services.common.symbol_matcher import SymbolMatcher
from services.common.period_parser import PeriodParser, PeriodComparator
from services.common.cadence_auditor import CadenceAuditor

HORIZON_MAP = {
    "SHORT": (config.HORIZON_SHORT_DAYS, config.UNIVERSE_MIN_CANDLES_SHORT),
    "MID":   (config.HORIZON_MID_DAYS,   config.UNIVERSE_MIN_CANDLES_MID),
    "LONG":  (config.HORIZON_LONG_DAYS,  config.UNIVERSE_MIN_CANDLES_LONG),
}


class UniverseBuilder:
    """
    Builds the eligible company universe for a given horizon.
    Evaluates candle coverage, financial data availability, and group eligibility.
    Does NOT compute technical or fundamental scores.
    """

    def __init__(
        self,
        source_session: Session,
        suffixes: list[str] = None
    ):
        self.source = source_session
        self.matcher = SymbolMatcher(suffixes or config.SYMBOL_SUFFIXES_TO_STRIP)
        self.cadence_auditor = CadenceAuditor(source_session)
        self._cached_companies = None
        self._cached_candle_counts = None
        self._cached_financials = None

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def build(self, horizon: str, as_of_date: Optional[datetime.date] = None) -> list[dict]:
        """
        Returns a list of eligibility dicts – one per company.
        """
        if horizon not in HORIZON_MAP:
            raise ValueError(f"Unknown horizon: {horizon}")

        horizon_days, min_candles = HORIZON_MAP[horizon]
        as_of = as_of_date or datetime.date.today()

        if self._cached_companies is None:
            self._cached_companies = self._fetch_companies()
        companies = self._cached_companies

        if self._cached_candle_counts is None:
            self._cached_candle_counts = self._fetch_candle_counts(as_of)
        candle_counts = self._cached_candle_counts

        if self._cached_financials is None:
            self._cached_financials = self._fetch_financial_availability()
        fin_availability = self._cached_financials

        results = []
        for company in companies:
            entry = self._evaluate_company(
                company, horizon, horizon_days, min_candles, candle_counts, fin_availability
            )
            if not (entry.get("return_available") and entry.get("financial_data_available")):
                continue
            entry["as_of_date"] = as_of
            results.append(entry)

        return results

    def generate_coverage_report(self, horizon: str) -> dict:
        entries = self.build(horizon)

        by_reason: dict = {}
        for e in entries:
            for reason in e.get("exclusion_reasons", []):
                by_reason[reason] = by_reason.get(reason, 0) + 1

        return {
            "horizon": horizon,
            "active_companies": len(entries),
            "symbol_matches": sum(1 for e in entries if not any("NO_CANDLE" in r for r in e.get("exclusion_reasons", []))),
            "return_eligible":        sum(1 for e in entries if e["return_available"]),
            "volume_eligible":        sum(1 for e in entries if e["volume_available"]),
            "fundamental_eligible":   sum(1 for e in entries if e["financial_data_available"]),
            "sector_eligible":        sum(1 for e in entries if e["eligible_for_sector"]),
            "industry_eligible":      sum(1 for e in entries if e["eligible_for_industry"]),
            "basic_industry_eligible": sum(1 for e in entries if e["eligible_for_basic_industry"]),
            "excluded_counts_by_reason": by_reason,
        }

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _fetch_companies(self) -> list:
        return self.source.execute(text("""
            SELECT id, share_symbol, sectore, industry, categorized_industry
            FROM companies
            WHERE share_symbol IS NOT NULL AND share_symbol != ''
        """)).fetchall()

    def _fetch_candle_counts(self, as_of: datetime.date) -> dict[str, int]:
        """Returns {normalized_symbol: candle_count}"""
        as_of_str = as_of.isoformat() + "T23:59:59"
        rows = self.source.execute(text("""
            SELECT symbol, COUNT(*) as cnt
            FROM market_candles_cleaned
            WHERE datetime <= :as_of
            GROUP BY symbol
        """), {"as_of": as_of_str}).fetchall()
        return {r.symbol: r.cnt for r in rows}

    def _fetch_financial_availability(self) -> dict[str, dict]:
        """
        For each companies.id returns the latest and prior-comparable periods
        across all three financial tables.

        Join path (discovered from actual schema):
          companies.share_symbol = company_overviews.share_symbol
          company_overviews.id   = company_profit_losses.company_id
                                 = company_balance_sheets.company_id
                                 = company_cash_flows.company_id
        """
        availability: dict[str, dict] = {}

        # Build symbol → companies.id map (share_symbol is unique in companies)
        sym_to_company_id: dict[str, str] = {}
        for r in self.source.execute(text("SELECT id, share_symbol FROM companies WHERE share_symbol IS NOT NULL")).fetchall():
            sym_to_company_id[r.share_symbol.strip().upper()] = r.id

        # Build overview_id → companies.id bridge via share_symbol
        overview_to_company: dict[str, str] = {}
        for r in self.source.execute(text("SELECT id, share_symbol FROM company_overviews WHERE share_symbol IS NOT NULL")).fetchall():
            sym_upper = r.share_symbol.strip().upper()
            cid = sym_to_company_id.get(sym_upper)
            if cid:
                overview_to_company[r.id] = cid

        for table, key in [
            ("company_profit_losses",  "pnl"),
            ("company_balance_sheets", "bs"),
            ("company_cash_flows",     "cf"),
        ]:
            rows = self.source.execute(text(f"""
                SELECT company_id, period FROM {table}
            """)).fetchall()

            # Group periods by overview_id
            by_overview: dict[str, list] = {}
            for r in rows:
                by_overview.setdefault(r.company_id, []).append(r.period)

            # Cache period parsing
            parsed_cache = {}
            def _parse(p):
                if p not in parsed_cache:
                    parsed_cache[p] = PeriodParser.parse(p)
                return parsed_cache[p]

            for ov_id, periods in by_overview.items():
                cid = overview_to_company.get(ov_id)
                if not cid:
                    continue

                parsed_valid = sorted(
                    [p for p in periods if _parse(p)["parse_status"] == "VALID"],
                    key=lambda p: _parse(p)["period_end"],
                    reverse=True
                )
                entry = availability.setdefault(cid, {})
                latest = parsed_valid[0] if parsed_valid else None
                prior = None
                if latest:
                    prior_candidate = PeriodComparator.get_previous_comparable_period(latest)
                    if prior_candidate and prior_candidate in periods:
                        prior = prior_candidate
                entry[f"{key}_latest"] = latest
                entry[f"{key}_prior"]  = prior

        return availability


    def _evaluate_company(
        self,
        company,
        horizon: str,
        horizon_days: int,
        min_candles: int,
        candle_counts: dict[str, int],
        fin_availability: dict[str, dict]
    ) -> dict:
        company_id = company.id
        raw_symbol = company.share_symbol or ""
        sector     = company.sectore or ""
        industry   = company.industry or ""
        basic_ind  = company.categorized_industry  # may be None

        exclusion_reasons = []

        # --- Sector / industry checks ---
        if not sector:
            exclusion_reasons.append("NO_SECTOR")
        if not industry:
            exclusion_reasons.append("NO_INDUSTRY")

        # --- Symbol match ---
        norm_symbol = self.matcher.normalize(raw_symbol)
        candle_count = candle_counts.get(norm_symbol) or candle_counts.get(raw_symbol, 0)
        if candle_count == 0:
            exclusion_reasons.append("NO_CANDLE_DATA")

        # --- Technical component availability ---
        #  full coverage = 2 * horizon_days candles
        #  return/consistency only = horizon_days + 1 candles
        return_min     = horizon_days + 1
        full_min       = min_candles        # 2 * horizon_days

        return_available      = candle_count >= return_min
        volume_available      = candle_count >= full_min
        consistency_available = candle_count >= return_min

        if not return_available:
            exclusion_reasons.append(f"INSUFFICIENT_CANDLES_FOR_{horizon}")

        # --- Technical data coverage (fraction of full 2x requirement) ---
        technical_data_coverage = round(
            min(candle_count, full_min) / full_min if full_min > 0 else 0.0,
            4
        )

        # --- Financial data availability ---
        fa = fin_availability.get(company_id, {})
        pnl_available = bool(fa.get("pnl_latest") and fa.get("pnl_prior"))
        bs_available  = bool(fa.get("bs_latest")  and fa.get("bs_prior"))
        cf_available  = bool(fa.get("cf_latest")  and fa.get("cf_prior"))
        financial_data_available = pnl_available and bs_available and cf_available

        if not financial_data_available:
            exclusion_reasons.append("INSUFFICIENT_FINANCIAL_DATA")

        # --- Fundamental data coverage ---
        avail_count = sum([pnl_available, bs_available, cf_available])
        fundamental_data_coverage = round(avail_count / 3.0, 4)

        # --- Group eligibility ---
        is_sector_eligible = (
            return_available
            and bool(sector)
            and bool(industry)
        )
        is_industry_eligible = is_sector_eligible
        is_basic_industry_eligible = is_sector_eligible and bool(basic_ind)

        if not is_basic_industry_eligible and bool(basic_ind) is False:
            exclusion_reasons.append("NO_BASIC_INDUSTRY")

        return {
            "source_company_id":       company_id,
            "symbol":                  raw_symbol,
            "sector":                  sector,
            "industry":                industry,
            "basic_industry":          basic_ind,
            "horizon":                 horizon,
            "return_available":        return_available,
            "volume_available":        volume_available,
            "consistency_available":   consistency_available,
            "financial_data_available": financial_data_available,
            "technical_data_coverage": technical_data_coverage,
            "fundamental_data_coverage": fundamental_data_coverage,
            "eligible_for_sector":         is_sector_eligible,
            "eligible_for_industry":       is_industry_eligible,
            "eligible_for_basic_industry": is_basic_industry_eligible,
            "exclusion_reasons":       exclusion_reasons,
        }
