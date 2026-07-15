"""
FundamentalIndustryPillarScoreService

Calculates the four deterministic industry fundamental pillar scores.
"""
from __future__ import annotations

import logging
import copy
from typing import Dict, Any, Tuple
from sqlalchemy.orm import Session

from models.discovery import GroupScore

logger = logging.getLogger(__name__)

def _get_status(score: float | None) -> str:
    if score is None:
        return "UNAVAILABLE"
    if score >= 80.0:
        return "VERY_STRONG"
    if score >= 65.0:
        return "STRONG"
    if score >= 50.0:
        return "NEUTRAL"
    if score >= 35.0:
        return "WEAK"
    return "VERY_WEAK"

def _blend_effective_score(
    norm_metrics: Dict, 
    transitions: Dict, 
    metric_key: str, 
    transition_key: str
) -> Dict[str, Any]:
    metric = norm_metrics.get(metric_key, {})
    trans = transitions.get(transition_key, {})
    
    num_score = metric.get("score")
    num_cnt = trans.get("numeric_status_count", 0)
    
    fall_score = trans.get("fallback_score")
    fall_cnt = trans.get("fallback_status_count", 0)
    
    avail_num_cnt = num_cnt if num_score is not None else 0
    avail_fall_cnt = fall_cnt if fall_score is not None else 0
    
    total_avail_cnt = avail_num_cnt + avail_fall_cnt
    total_cnt = num_cnt + fall_cnt
    
    eff_score = None
    eff_cov = None
    
    if total_avail_cnt > 0:
        eff_score = ((num_score or 0.0) * avail_num_cnt + (fall_score or 0.0) * avail_fall_cnt) / total_avail_cnt
        eff_score = round(eff_score, 2)
        
    if total_cnt > 0:
        eff_cov = round((total_avail_cnt / total_cnt) * 100.0, 2)
        
    return {
        "numeric_score": num_score,
        "numeric_count": num_cnt,
        "fallback_score": fall_score,
        "fallback_count": fall_cnt,
        "effective_score": eff_score,
        "evidence_coverage_pct": eff_cov,
        "available": eff_score is not None
    }

def _calculate_pillar(
    components: Dict[str, Dict], 
    pillar_name: str
) -> Tuple[Dict[str, Any], set]:
    
    total_avail_weight = 0.0
    weighted_sum = 0.0
    
    for comp_name, comp_data in components.items():
        if comp_data.get("available"):
            total_avail_weight += comp_data["weight"]
            weighted_sum += comp_data.get("score", comp_data.get("effective_score", 0.0)) * comp_data["weight"]
            
    score = None
    if total_avail_weight > 0:
        score = round(weighted_sum / total_avail_weight, 2)
        
    cov_pct = round(total_avail_weight, 2)
    status = _get_status(score)
    
    warnings = set()
    if cov_pct == 0.0:
        warnings.add(f"{pillar_name.upper()}_UNAVAILABLE")
    elif cov_pct < 100.0:
        warnings.add(f"{pillar_name.upper()}_PARTIAL")
        
    return {
        "components": components,
        "available_weight": total_avail_weight,
        "coverage_pct": cov_pct,
        "score": score,
        "status": status
    }, warnings

class FundamentalIndustryPillarScoreService:
    def __init__(self, discovery_session: Session):
        self._disc = discovery_session

    def calculate_pillar_scores(
        self,
        run_id: str,
        horizon: str | None = None,
        parent_sector: str | None = None,
    ) -> None:
        query = self._disc.query(GroupScore).filter_by(
            run_id=run_id,
            entity_type="INDUSTRY",
            parent_industry="",
        )
        if horizon is not None:
            query = query.filter_by(horizon=horizon)
        if parent_sector is not None:
            query = query.filter(GroupScore.parent_sector == parent_sector)
        industries = query.all()
        
        if not industries:
            return

        for ind in industries:
            calc = copy.deepcopy(ind.calculation_details or {})
            fund = calc.get("fundamental", {})
            norm_metrics = fund.get("metric_normalization", {}).get("metrics", {})
            transitions = fund.get("structural_transition_scores", {})
            raw_agg = fund.get("raw_aggregation", {})
            
            warnings_set = set(ind.warnings or [])
            for p in ["GROWTH_PILLAR", "PROFITABILITY_PILLAR", "FINANCIAL_STRENGTH_PILLAR", "EARNINGS_QUALITY_PILLAR"]:
                warnings_set.discard(f"{p}_UNAVAILABLE")
                warnings_set.discard(f"{p}_PARTIAL")
                
            pillar_scores = {}
            
            # 1. Growth
            sg_score = norm_metrics.get("sales_growth_pct", {}).get("score")
            sg_comp = {
                "score": sg_score,
                "weight": 50.0,
                "available": sg_score is not None
            }
            np_eff = _blend_effective_score(norm_metrics, transitions, "net_profit_growth_pct", "net_profit")
            np_eff["weight"] = 50.0
            
            growth_res, g_warn = _calculate_pillar({
                "sales_growth": sg_comp,
                "net_profit_growth": np_eff
            }, "GROWTH_PILLAR")
            pillar_scores["growth"] = growth_res
            warnings_set.update(g_warn)
            
            # 2. Profitability
            op_lvl = norm_metrics.get("latest_operating_margin_pct", {}).get("score")
            op_chg = norm_metrics.get("operating_margin_change_pp", {}).get("score")
            prof_res, p_warn = _calculate_pillar({
                "latest_operating_margin": {
                    "score": op_lvl,
                    "weight": 60.0,
                    "available": op_lvl is not None
                },
                "operating_margin_change": {
                    "score": op_chg,
                    "weight": 40.0,
                    "available": op_chg is not None
                }
            }, "PROFITABILITY_PILLAR")
            pillar_scores["profitability"] = prof_res
            warnings_set.update(p_warn)
            
            # 3. Financial Strength
            std_debt_app = raw_agg.get("standard_debt_rule_applicable_count", 0) > 0
            if std_debt_app:
                dte_score = norm_metrics.get("debt_to_equity", {}).get("score")
                bor_eff = _blend_effective_score(norm_metrics, transitions, "borrowing_change_pct", "borrowing")
                bor_eff["weight"] = 40.0
                
                fs_res, fs_warn = _calculate_pillar({
                    "debt_to_equity": {
                        "score": dte_score,
                        "weight": 60.0,
                        "available": dte_score is not None
                    },
                    "borrowing_trend": bor_eff
                }, "FINANCIAL_STRENGTH_PILLAR")
                fs_res["applicable"] = True
                fs_res["reason"] = None
                pillar_scores["financial_strength"] = fs_res
                warnings_set.update(fs_warn)
            else:
                pillar_scores["financial_strength"] = {
                    "applicable": False,
                    "coverage_pct": None,
                    "score": None,
                    "status": "N_A",
                    "reason": "N_A_NO_STANDARD_DEBT_RULE_COMPANIES"
                }
                
            # 4. Earnings Quality
            cc_eff = _blend_effective_score(norm_metrics, transitions, "latest_ocf_to_pat", "cash_conversion")
            cc_eff["weight"] = 40.0
            
            ocf_chg = norm_metrics.get("ocf_to_pat_change", {}).get("score")
            pat_pos = norm_metrics.get("positive_pat_period_ratio", {}).get("score")
            pat_vol = norm_metrics.get("pat_growth_volatility_pct", {}).get("score")
            
            eq_res, eq_warn = _calculate_pillar({
                "latest_cash_conversion": cc_eff,
                "ocf_to_pat_change": {
                    "score": ocf_chg,
                    "weight": 20.0,
                    "available": ocf_chg is not None
                },
                "positive_pat_period_ratio": {
                    "score": pat_pos,
                    "weight": 25.0,
                    "available": pat_pos is not None
                },
                "pat_growth_volatility": {
                    "score": pat_vol,
                    "weight": 15.0,
                    "available": pat_vol is not None
                }
            }, "EARNINGS_QUALITY_PILLAR")
            pillar_scores["earnings_quality"] = eq_res
            warnings_set.update(eq_warn)

            if "fundamental" not in calc:
                calc["fundamental"] = {}
                
            calc["fundamental"]["pillar_scores"] = pillar_scores
            
            warn_list = list(warnings_set)
            warn_list.sort()
            ind.warnings = warn_list
            ind.calculation_details = calc
            
        self._disc.commit()
