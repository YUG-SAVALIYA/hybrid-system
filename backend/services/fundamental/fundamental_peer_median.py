"""
FundamentalPeerMedianService

Resolves deterministic peer-median baselines for fundamental metrics.
"""
from __future__ import annotations

import logging
import copy
from typing import Callable, Any
from sqlalchemy.orm import Session

import config
from models.discovery import CompanyFundamentalMetric

logger = logging.getLogger(__name__)

MIN_VALID_PEER_OBSERVATIONS = getattr(config, 'MIN_VALID_PEER_OBSERVATIONS', 3)

METRIC_EXTRACTORS = {
    "sales_growth_pct": lambda d: (
        d.get("growth", {}).get("sales_growth_pct"),
        d.get("growth", {}).get("sales_growth_available", False)
    ),
    "net_profit_growth_pct": lambda d: (
        d.get("growth", {}).get("net_profit_growth_pct"),
        d.get("growth", {}).get("net_profit_growth_available", False)
    ),
    "latest_operating_margin_pct": lambda d: (
        d.get("profitability", {}).get("latest_operating_margin_pct"),
        d.get("profitability", {}).get("latest_operating_margin_available", False)
    ),
    "operating_margin_change_pp": lambda d: (
        d.get("profitability", {}).get("operating_margin_change_pp"),
        d.get("profitability", {}).get("operating_margin_trend_available", False)
    ),
    "debt_to_equity": lambda d: (
        d.get("financial_strength", {}).get("debt_to_equity"),
        d.get("financial_strength", {}).get("debt_to_equity_available", False)
    ),
    "borrowing_change_pct": lambda d: (
        d.get("financial_strength", {}).get("borrowing_change_pct"),
        d.get("financial_strength", {}).get("borrowing_trend_available", False)
    ),
    "latest_ocf_to_pat": lambda d: (
        d.get("earnings_quality", {}).get("cash_conversion", {}).get("latest", {}).get("ocf_to_pat"),
        d.get("earnings_quality", {}).get("cash_conversion", {}).get("latest_cash_conversion_available", False)
    ),
    "ocf_to_pat_change": lambda d: (
        d.get("earnings_quality", {}).get("cash_conversion", {}).get("ocf_to_pat_change"),
        d.get("earnings_quality", {}).get("cash_conversion", {}).get("cash_conversion_trend_available", False)
    ),
    "positive_pat_period_ratio": lambda d: (
        d.get("earnings_quality", {}).get("profit_stability", {}).get("positive_pat_period_ratio"),
        d.get("earnings_quality", {}).get("profit_stability", {}).get("profit_stability_available", False)
    ),
    "pat_growth_volatility_pct": lambda d: (
        d.get("earnings_quality", {}).get("profit_stability", {}).get("pat_growth_volatility_pct"),
        d.get("earnings_quality", {}).get("profit_stability", {}).get("pat_growth_volatility_available", False)
    )
}

def _is_finite(val: Any) -> bool:
    if val is None: return False
    if isinstance(val, (int, float)):
        return val == val and val != float('inf') and val != float('-inf')
    return False

def _median(values: list[float]) -> float:
    n = len(values)
    if n == 0: return 0.0
    s = sorted(values)
    mid = n // 2
    if n % 2 == 0:
        return (s[mid - 1] + s[mid]) / 2.0
    else:
        return s[mid]


class FundamentalPeerMedianService:
    def __init__(self, discovery_session: Session):
        self._disc = discovery_session

    def resolve_peer_medians(self, run_id: str) -> None:
        records = self._disc.query(CompanyFundamentalMetric).filter_by(run_id=run_id).all()
        if not records:
            return

        # Pre-build index for fast lookups
        # To avoid comparing same industry name across different sectors,
        # keys will incorporate parent hierarchy.
        
        # sector_name -> list of records
        sector_idx = {}
        # sector|industry -> list of records
        industry_idx = {}
        # sector|industry|basic -> list of records
        basic_idx = {}

        for rec in records:
            sec = (rec.sector or "").strip()
            ind = (rec.industry or "").strip()
            bas = (rec.basic_industry or "").strip()
            
            if sec:
                sector_idx.setdefault(sec, []).append(rec)
            if sec and ind:
                k_ind = f"{sec}|{ind}"
                industry_idx.setdefault(k_ind, []).append(rec)
            if sec and ind and bas:
                k_bas = f"{sec}|{ind}|{bas}"
                basic_idx.setdefault(k_bas, []).append(rec)

        for rec in records:
            calc_details = rec.calculation_details or {}
            
            sec = (rec.sector or "").strip()
            ind = (rec.industry or "").strip()
            bas = (rec.basic_industry or "").strip()
            
            applicable_count = 0
            resolved_count = 0
            metrics_out = {}
            
            # Identify standard debt rule applicability
            target_std_debt = calc_details.get("financial_strength", {}).get("standard_debt_rule_applicable", True)
            
            for m_name, extractor in METRIC_EXTRACTORS.items():
                val, avail = extractor(calc_details)
                
                # Check Applicability
                is_applicable = False
                company_val = None
                reason = None
                
                if m_name == "debt_to_equity" and not target_std_debt:
                    is_applicable = False
                    reason = "N_A_STANDARD_DEBT_RULE"
                else:
                    if avail and _is_finite(val):
                        is_applicable = True
                        company_val = val
                    else:
                        is_applicable = False
                        reason = "COMPANY_METRIC_UNAVAILABLE"
                
                if is_applicable:
                    applicable_count += 1
                    
                if not is_applicable and reason == "N_A_STANDARD_DEBT_RULE":
                    # For debt excluded financials, it's not applicable
                    metrics_out[m_name] = {
                        "company_value": None,
                        "peer_median": None,
                        "comparison_level": None,
                        "peer_count": 0,
                        "available": False,
                        "reason": reason
                    }
                    continue
                    
                # Find peers
                peer_val_lists = {"BASIC_INDUSTRY": [], "INDUSTRY": [], "SECTOR": []}
                
                def collect_peers(idx_dict, key, level):
                    if key in idx_dict:
                        for p_rec in idx_dict[key]:
                            if p_rec.id == rec.id:
                                continue
                            p_calc = p_rec.calculation_details or {}
                            p_val, p_avail = extractor(p_calc)
                            if p_avail and _is_finite(p_val):
                                if m_name == "debt_to_equity":
                                    p_std_debt = p_calc.get("financial_strength", {}).get("standard_debt_rule_applicable", True)
                                    if not p_std_debt:
                                        continue
                                peer_val_lists[level].append(p_val)

                if sec and ind and bas:
                    collect_peers(basic_idx, f"{sec}|{ind}|{bas}", "BASIC_INDUSTRY")
                if sec and ind:
                    collect_peers(industry_idx, f"{sec}|{ind}", "INDUSTRY")
                if sec:
                    collect_peers(sector_idx, sec, "SECTOR")

                resolved_level = None
                resolved_median = None
                peer_count = 0
                
                if len(peer_val_lists["BASIC_INDUSTRY"]) >= MIN_VALID_PEER_OBSERVATIONS:
                    resolved_level = "BASIC_INDUSTRY"
                    resolved_median = _median(peer_val_lists["BASIC_INDUSTRY"])
                    peer_count = len(peer_val_lists["BASIC_INDUSTRY"])
                elif len(peer_val_lists["INDUSTRY"]) >= MIN_VALID_PEER_OBSERVATIONS:
                    resolved_level = "INDUSTRY"
                    resolved_median = _median(peer_val_lists["INDUSTRY"])
                    peer_count = len(peer_val_lists["INDUSTRY"])
                elif len(peer_val_lists["SECTOR"]) >= MIN_VALID_PEER_OBSERVATIONS:
                    resolved_level = "SECTOR"
                    resolved_median = _median(peer_val_lists["SECTOR"])
                    peer_count = len(peer_val_lists["SECTOR"])
                else:
                    # Determine reason
                    if not reason:
                        if sec and ind and bas:
                            reason = "INSUFFICIENT_SECTOR_PEERS"
                        elif sec and ind:
                            reason = "INSUFFICIENT_SECTOR_PEERS"
                        elif sec:
                            reason = "INSUFFICIENT_SECTOR_PEERS"
                        else:
                            reason = "NO_SECTOR_ASSIGNED"
                
                if resolved_level:
                    resolved_count += 1
                    
                metrics_out[m_name] = {
                    "company_value": company_val,
                    "peer_median": resolved_median,
                    "comparison_level": resolved_level,
                    "peer_count": peer_count,
                    "available": bool(resolved_level),
                    "reason": reason if not resolved_level else None
                }

            cov_pct = 0.0
            if applicable_count > 0:
                cov_pct = (resolved_count / applicable_count) * 100.0

            pb_data = {
                "minimum_peer_observations": MIN_VALID_PEER_OBSERVATIONS,
                "resolved_metric_count": resolved_count,
                "applicable_metric_count": applicable_count,
                "coverage_pct": cov_pct,
                "metrics": metrics_out
            }

            warnings = set(calc_details.get("warnings", []))
            warnings.discard("PEER_BASELINE_PARTIAL")
            warnings.discard("PEER_BASELINE_UNAVAILABLE")

            if applicable_count > 0:
                if resolved_count == 0:
                    warnings.add("PEER_BASELINE_UNAVAILABLE")
                elif resolved_count < applicable_count:
                    warnings.add("PEER_BASELINE_PARTIAL")

            new_calc = copy.deepcopy(calc_details)
            new_calc["peer_benchmarks"] = pb_data
            warn_list = list(warnings)
            warn_list.sort()
            new_calc["warnings"] = warn_list
            
            rec.calculation_details = new_calc
            
        self._disc.commit()
