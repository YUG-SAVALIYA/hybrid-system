"""
FundamentalPeriodSelectionService

Selects and aligns annual financial periods for P&L, balance sheet, and cash flow records.
"""
from __future__ import annotations

import datetime
from sqlalchemy import text
from sqlalchemy.orm import Session

from services.common.period_parser import PeriodParser

def _parse_date(date_str: str) -> datetime.date:
    y, m, d = map(int, date_str.split('-'))
    return datetime.date(y, m, d)

def _days_between(d1: str, d2: str) -> int:
    return abs((_parse_date(d1) - _parse_date(d2)).days)

def _classify_and_filter_periods(period_strings: list[str] | None) -> list[dict]:
    if not period_strings:
        return []
        
    parsed_list = []
    for p in set(period_strings):
        if not p: continue
        parsed = PeriodParser.parse(p)
        if parsed["parse_status"] != "VALID":
            orig = parsed["original_period"].lower()
            if "q1" in orig or "q2" in orig or "q3" in orig or "q4" in orig or "quarter" in orig:
                parsed["classification"] = "QUARTERLY"
            else:
                parsed["classification"] = "UNKNOWN"
        else:
            parsed["classification"] = "ANNUAL_CONFIRMED"
        parsed_list.append(parsed)
        
    valid_months = set()
    for p in parsed_list:
        if p["parse_status"] == "VALID":
            parts = p["original_period"].strip().split()
            if len(parts) > 0:
                valid_months.add(parts[0])
                
    if len(valid_months) > 1:
        for p in parsed_list:
            if p["classification"] == "ANNUAL_CONFIRMED":
                p["classification"] = "AMBIGUOUS_PERIOD_CADENCE"
                
    years_seen = {}
    for p in parsed_list:
        if p["classification"] == "ANNUAL_CONFIRMED":
            fy = p["financial_year"]
            if fy not in years_seen:
                years_seen[fy] = []
            years_seen[fy].append(p)
            
    for fy, items in years_seen.items():
        if len(items) > 1:
            for item in items:
                item["classification"] = "DUPLICATE_CALENDAR_YEAR"
                
    confirmed = [p for p in parsed_list if p["classification"] == "ANNUAL_CONFIRMED"]
    confirmed.sort(key=lambda x: x["period_end"], reverse=True)
    return confirmed


class FundamentalPeriodSelectionService:
    def __init__(self, source_session: Session):
        self._src = source_session

    def select_periods(self) -> list[dict]:
        query_companies = text("""
            SELECT 
                c.id as source_company_id,
                c.share_symbol as symbol,
                co.id as overview_id
            FROM companies c
            LEFT JOIN company_overviews co ON c.share_symbol = co.share_symbol
        """)
        companies = self._src.execute(query_companies).fetchall()
        source_company_ids = [c.source_company_id for c in companies if c.source_company_id]

        pl_periods_map = {}
        bs_periods_map = {}
        cf_periods_map = {}

        if source_company_ids:
            pl_query = text("SELECT company_id, array_agg(DISTINCT period) as periods FROM company_profit_losses WHERE company_id = ANY(:cids) GROUP BY company_id")
            for r in self._src.execute(pl_query, {"cids": source_company_ids}).fetchall():
                pl_periods_map[r.company_id] = r.periods

            bs_query = text("SELECT company_id, array_agg(DISTINCT period) as periods FROM company_balance_sheets WHERE company_id = ANY(:cids) GROUP BY company_id")
            for r in self._src.execute(bs_query, {"cids": source_company_ids}).fetchall():
                bs_periods_map[r.company_id] = r.periods

            cf_query = text("SELECT company_id, array_agg(DISTINCT period) as periods FROM company_cash_flows WHERE company_id = ANY(:cids) GROUP BY company_id")
            for r in self._src.execute(cf_query, {"cids": source_company_ids}).fetchall():
                cf_periods_map[r.company_id] = r.periods

        results = []
        for r in companies:
            warnings = set()
            cid = r.source_company_id
            
            pl_strings = pl_periods_map.get(cid, []) if cid else []
            bs_strings = bs_periods_map.get(cid, []) if cid else []
            cf_strings = cf_periods_map.get(cid, []) if cid else []
            
            if not r.overview_id:
                warnings.add("MISSING_COMPANY_OVERVIEW")
                
            pl_periods = _classify_and_filter_periods(pl_strings)
            bs_periods = _classify_and_filter_periods(bs_strings)
            cf_periods = _classify_and_filter_periods(cf_strings)
            
            def _eval_pair(periods_list):
                latest = periods_list[0]["original_period"] if len(periods_list) > 0 else None
                prev = periods_list[1]["original_period"] if len(periods_list) > 1 else None
                comp = False
                if latest and prev:
                    d1 = periods_list[0]["period_end"]
                    d2 = periods_list[1]["period_end"]
                    if 300 <= _days_between(d1, d2) <= 430:
                        comp = True
                    else:
                        warnings.add("NON_CONSECUTIVE_ANNUAL_PERIODS")
                return latest, prev, comp

            pl_latest, pl_prev, pl_comp = _eval_pair(pl_periods)
            if not pl_comp: warnings.add("INSUFFICIENT_PROFIT_LOSS_PERIODS")
                
            bs_latest, bs_prev, bs_comp = _eval_pair(bs_periods)
            if not bs_comp: warnings.add("INSUFFICIENT_BALANCE_SHEET_PERIODS")
                
            cf_latest, cf_prev, cf_comp = _eval_pair(cf_periods)
            if not cf_comp: warnings.add("INSUFFICIENT_CASH_FLOW_PERIODS")
                
            pl_set = {p["period_end"]: p for p in pl_periods}
            cf_set = {p["period_end"]: p for p in cf_periods}
            
            common_dates = sorted(list(set(pl_set.keys()).intersection(set(cf_set.keys()))), reverse=True)
            
            pl_cf_latest = pl_set[common_dates[0]]["original_period"] if len(common_dates) > 0 else None
            pl_cf_prev = pl_set[common_dates[1]]["original_period"] if len(common_dates) > 1 else None
            pl_cf_comp = False
            
            if pl_cf_latest and pl_cf_prev:
                d1 = common_dates[0]
                d2 = common_dates[1]
                if 300 <= _days_between(d1, d2) <= 430:
                    pl_cf_comp = True
                else:
                    warnings.add("NON_CONSECUTIVE_ANNUAL_PERIODS")
                    
            if not pl_cf_comp:
                warnings.add("NO_COMMON_PL_CF_PERIOD")
                
            results.append({
                "source_company_id": r.source_company_id,
                "symbol": r.symbol,
                "overview_id": r.overview_id,
                "profit_loss": {
                    "latest_period": pl_latest,
                    "previous_period": pl_prev,
                    "comparable": pl_comp
                },
                "balance_sheet": {
                    "latest_period": bs_latest,
                    "previous_period": bs_prev,
                    "comparable": bs_comp
                },
                "cash_flow": {
                    "latest_period": cf_latest,
                    "previous_period": cf_prev,
                    "comparable": cf_comp
                },
                "profit_loss_cash_flow_common": {
                    "latest_period": pl_cf_latest,
                    "previous_period": pl_cf_prev,
                    "comparable": pl_cf_comp
                },
                "warnings": sorted(list(warnings))
            })
            
        return results
