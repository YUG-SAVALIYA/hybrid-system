"""
FundamentalBasicIndustryAggregationService

Calculates deterministic raw fundamental medians and structural distributions
for basic industries across all companies.
"""
from __future__ import annotations

import logging
import copy
from typing import Dict, List, Any
from sqlalchemy.orm import Session

import config
from models.discovery import CompanyFundamentalMetric, GroupScore

logger = logging.getLogger(__name__)

MIN_BASIC_INDUSTRY_FUNDAMENTAL_COMPANIES = getattr(config, 'MIN_BASIC_INDUSTRY_FUNDAMENTAL_COMPANIES', 2)

NORMAL_METRICS = [
    ("sales_growth_pct", "growth"),
    ("net_profit_growth_pct", "growth"),
    ("latest_operating_margin_pct", "profitability"),
    ("operating_margin_change_pp", "profitability"),
    ("latest_ocf_to_pat", "earnings_quality", "cash_conversion"),
    ("ocf_to_pat_change", "earnings_quality", "cash_conversion"),
    ("positive_pat_period_ratio", "earnings_quality", "profit_stability"),
    ("pat_growth_volatility_pct", "earnings_quality", "profit_stability")
]

DEBT_METRICS = [
    ("debt_to_equity", "financial_strength"),
    ("borrowing_change_pct", "financial_strength")
]

def _is_finite(val: float | None) -> bool:
    if val is None: return False
    if isinstance(val, (int, float)):
        return val == val and val != float('inf') and val != float('-inf')
    return False

def _median(sorted_values: List[float]) -> float | None:
    n = len(sorted_values)
    if n == 0:
        return None
    mid = n // 2
    if n % 2 != 0:
        return sorted_values[mid]
    else:
        return (sorted_values[mid - 1] + sorted_values[mid]) / 2.0

class FundamentalBasicIndustryAggregationService:
    def __init__(self, discovery_session: Session):
        self._disc = discovery_session

    def aggregate_basic_industries(self, run_id: str) -> None:
        companies = self._disc.query(CompanyFundamentalMetric).filter_by(run_id=run_id).all()
        
        basic_industries_map = {}
        
        for c in companies:
            sec = c.sector
            ind = c.industry
            bi = c.basic_industry
            if not sec or not ind or not bi:
                continue
                
            key = (sec, ind, bi)
            basic_industries_map.setdefault(key, []).append(c)
            
        if not basic_industries_map:
            return
            
        group_records = self._disc.query(GroupScore).filter_by(
            run_id=run_id,
            entity_type="BASIC_INDUSTRY"
        ).all()
        
        existing = {}
        for g in group_records:
            existing[(g.parent_sector, g.parent_industry, g.entity_name)] = g
            
        for (sec, ind, bi), comps in basic_industries_map.items():
            constituent_count = len(comps)
            score_avail_cnt = 0
            sel_elig_cnt = 0
            std_debt_app_cnt = 0
            std_debt_na_cnt = 0
            
            metric_lists = {m[0]: [] for m in NORMAL_METRICS + DEBT_METRICS}
            
            np_trans = {}
            bor_trans = {}
            cc_trans = {}
            
            for c in comps:
                if c.final_fundamental_score is not None:
                    score_avail_cnt += 1
                if c.fundamental_eligible_for_selection:
                    sel_elig_cnt += 1
                    
                calc = c.calculation_details or {}
                fs_calc = calc.get("financial_strength", {})
                std_debt = fs_calc.get("standard_debt_rule_applicable", True)
                
                if std_debt:
                    std_debt_app_cnt += 1
                else:
                    std_debt_na_cnt += 1
                    
                # Values
                for m_tuple in NORMAL_METRICS:
                    m_name = m_tuple[0]
                    cat = m_tuple[1]
                    sub = m_tuple[2] if len(m_tuple) > 2 else None
                    
                    if sub:
                        data = calc.get(cat, {}).get(sub, {})
                    else:
                        data = calc.get(cat, {})
                        
                    if data.get(f"{m_name}_available") or data.get("available"):
                        val = data.get(m_name)
                        if _is_finite(val):
                            metric_lists[m_name].append(val)
                            
                if std_debt:
                    for m_tuple in DEBT_METRICS:
                        m_name = m_tuple[0]
                        cat = m_tuple[1]
                        data = calc.get(cat, {})
                        if data.get(f"{m_name}_available") or data.get("available"):
                            val = data.get(m_name)
                            if _is_finite(val):
                                metric_lists[m_name].append(val)
                                
                # Transitions
                npt = calc.get("growth", {}).get("net_profit_transition")
                if npt:
                    np_trans[npt] = np_trans.get(npt, 0) + 1
                    
                if std_debt:
                    bt = fs_calc.get("borrowing_transition")
                    if bt:
                        bor_trans[bt] = bor_trans.get(bt, 0) + 1
                        
                cct = calc.get("earnings_quality", {}).get("cash_conversion", {}).get("latest_cash_conversion_status")
                if cct:
                    cc_trans[cct] = cc_trans.get(cct, 0) + 1
                    
            metrics_res = {}
            
            for m_tuple in NORMAL_METRICS:
                m_name = m_tuple[0]
                lst = sorted(metric_lists[m_name])
                val_cnt = len(lst)
                app_cnt = constituent_count
                
                cov = 0.0
                if app_cnt > 0:
                    cov = round((val_cnt / app_cnt) * 100.0, 2)
                    
                metrics_res[m_name] = {
                    "median": _median(lst),
                    "valid_count": val_cnt,
                    "applicable_count": app_cnt,
                    "coverage_pct": cov,
                    "reason": None
                }
                
            for m_tuple in DEBT_METRICS:
                m_name = m_tuple[0]
                lst = sorted(metric_lists[m_name])
                val_cnt = len(lst)
                app_cnt = std_debt_app_cnt
                
                if std_debt_app_cnt == 0:
                    metrics_res[m_name] = {
                        "median": None,
                        "valid_count": 0,
                        "applicable_count": 0,
                        "coverage_pct": None,
                        "reason": "N_A_NO_STANDARD_DEBT_RULE_COMPANIES"
                    }
                else:
                    metrics_res[m_name] = {
                        "median": _median(lst),
                        "valid_count": val_cnt,
                        "applicable_count": app_cnt,
                        "coverage_pct": round((val_cnt / app_cnt) * 100.0, 2),
                        "reason": None
                    }
                    
            def _t_res(counts_dict: Dict[str, int]) -> Dict[str, Any]:
                total = sum(counts_dict.values())
                pcts = {}
                if total > 0:
                    for k, v in counts_dict.items():
                        pcts[k] = round((v / total) * 100.0, 2)
                return {
                    "valid_status_count": total,
                    "counts": counts_dict,
                    "percentages": pcts
                }
                
            transitions_res = {
                "net_profit": _t_res(np_trans),
                "borrowing": _t_res(bor_trans),
                "cash_conversion": _t_res(cc_trans)
            }
            
            g = existing.get((sec, ind, bi))
            if not g:
                import uuid
                g = GroupScore(
                    id=str(uuid.uuid4()),
                    run_id=run_id,
                    entity_type="BASIC_INDUSTRY",
                    entity_name=bi,
                    parent_sector=sec,
                    parent_industry=ind,
                    horizon="1Y"
                )
                self._disc.add(g)
                
            calc_details = copy.deepcopy(g.calculation_details or {})
            if "fundamental" not in calc_details:
                calc_details["fundamental"] = {}
                
            calc_details["fundamental"]["raw_aggregation"] = {
                "constituent_count": constituent_count,
                "fundamental_score_available_count": score_avail_cnt,
                "fundamental_selection_eligible_count": sel_elig_cnt,
                "standard_debt_rule_applicable_count": std_debt_app_cnt,
                "standard_debt_rule_not_applicable_count": std_debt_na_cnt,
                "metrics": metrics_res,
                "transitions": transitions_res
            }
            
            g.calculation_details = calc_details
            
            warnings_set = set(g.warnings or [])
            warnings_set.discard("INSUFFICIENT_FUNDAMENTAL_CONSTITUENTS")
            if score_avail_cnt < MIN_BASIC_INDUSTRY_FUNDAMENTAL_COMPANIES:
                warnings_set.add("INSUFFICIENT_FUNDAMENTAL_CONSTITUENTS")
            
            w_list = list(warnings_set)
            w_list.sort()
            g.warnings = w_list
            
        self._disc.commit()
