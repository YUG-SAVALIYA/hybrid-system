"""
FundamentalBasicIndustryPillarScoreService

Calculates the four deterministic fundamental pillar scores for basic industries.
"""
from __future__ import annotations

import logging
import copy
from typing import Dict, Any, Tuple
from sqlalchemy.orm import Session

from models.discovery import GroupScore

logger = logging.getLogger(__name__)

def _is_finite(val: float | None) -> bool:
    if val is None: return False
    if isinstance(val, (int, float)):
        return val == val and val != float('inf') and val != float('-inf')
    return False

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

def _blend_evidence(num_score: float | None, 
                    num_cnt: int, 
                    fall_score: float | None, 
                    fall_cnt: int,
                    total_evidence_cnt: int) -> Dict[str, Any]:
    
    avail_num_cnt = num_cnt if _is_finite(num_score) else 0
    avail_fall_cnt = fall_cnt if _is_finite(fall_score) else 0
    
    avail_cnt = avail_num_cnt + avail_fall_cnt
    
    eff_score = None
    avail = False
    
    if avail_cnt > 0:
        ns = num_score if _is_finite(num_score) else 0.0
        fs = fall_score if _is_finite(fall_score) else 0.0
        
        eff = ((ns * avail_num_cnt) + (fs * avail_fall_cnt)) / avail_cnt
        eff_score = round(eff, 2)
        avail = True
        
    cov_pct = None
    if total_evidence_cnt > 0:
        cov_pct = round((avail_cnt / total_evidence_cnt) * 100.0, 2)
        
    return {
        "numeric_score": num_score,
        "numeric_count": num_cnt,
        "fallback_score": fall_score,
        "fallback_count": fall_cnt,
        "effective_score": eff_score,
        "evidence_coverage_pct": cov_pct,
        "available": avail
    }

def _calc_pillar(components: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    app_w = 0.0
    avail_w = 0.0
    weighted_sum = 0.0
    
    for k, c in components.items():
        w = c.get("weight", 0.0)
        app_w += w
        if c.get("available", False):
            avail_w += w
            sc = c.get("effective_score")
            if sc is None:
                sc = c.get("score")
            if sc is not None:
                weighted_sum += (sc * w)
                
    cov = 0.0
    if app_w > 0:
        cov = round((avail_w / app_w) * 100.0, 2)
        
    score = None
    if avail_w > 0:
        score = round(weighted_sum / avail_w, 2)
        
    return {
        "components": components,
        "available_weight": avail_w,
        "coverage_pct": cov,
        "score": score,
        "status": _get_status(score)
    }

class FundamentalBasicIndustryPillarScoreService:
    def __init__(self, discovery_session: Session):
        self._disc = discovery_session

    def calculate_pillar_scores(
        self,
        run_id: str,
        horizon: str | None = None,
        parent_sector: str | None = None,
        parent_industry: str | None = None,
    ) -> None:
        query = self._disc.query(GroupScore).filter_by(
            run_id=run_id,
            entity_type="BASIC_INDUSTRY",
        )
        if horizon is not None:
            query = query.filter_by(horizon=horizon)
        if parent_sector is not None:
            query = query.filter(GroupScore.parent_sector == parent_sector)
        if parent_industry is not None:
            query = query.filter(GroupScore.parent_industry == parent_industry)
        bi_groups = query.all()
        
        if not bi_groups:
            return
            
        for bi in bi_groups:
            calc = copy.deepcopy(bi.calculation_details or {})
            fund = calc.get("fundamental", {})
            raw_agg = fund.get("raw_aggregation", {})
            norm = fund.get("metric_normalization", {}).get("metrics", {})
            trans = fund.get("structural_transition_scores", {})
            
            std_debt_app_cnt = raw_agg.get("standard_debt_rule_applicable_count", 0)
            std_debt_na_cnt = raw_agg.get("standard_debt_rule_not_applicable_count", 0)
            
            # --- Growth ---
            sg_norm = norm.get("sales_growth_pct", {})
            sg_score = sg_norm.get("score")
            sg_comp = {
                "score": sg_score,
                "weight": 50.0,
                "available": _is_finite(sg_score)
            }
            
            np_norm = norm.get("net_profit_growth_pct", {})
            np_num_score = np_norm.get("score")
            np_trans = trans.get("net_profit", {})
            np_fall_score = np_trans.get("fallback_score")
            
            np_num_cnt = np_trans.get("numeric_status_count", 0)
            np_fall_cnt = np_trans.get("fallback_status_count", 0)
            np_tot_ev = np_num_cnt + np_fall_cnt
            
            np_eff = _blend_evidence(np_num_score, np_num_cnt, np_fall_score, np_fall_cnt, np_tot_ev)
            np_eff["weight"] = 50.0
            
            growth_res = _calc_pillar({
                "sales_growth": sg_comp,
                "net_profit_growth": np_eff
            })
            
            # --- Profitability ---
            lom_norm = norm.get("latest_operating_margin_pct", {})
            lom_score = lom_norm.get("score")
            lom_comp = {
                "score": lom_score,
                "weight": 60.0,
                "available": _is_finite(lom_score)
            }
            
            omc_norm = norm.get("operating_margin_change_pp", {})
            omc_score = omc_norm.get("score")
            omc_comp = {
                "score": omc_score,
                "weight": 40.0,
                "available": _is_finite(omc_score)
            }
            
            prof_res = _calc_pillar({
                "latest_operating_margin": lom_comp,
                "operating_margin_change": omc_comp
            })
            
            # --- Financial Strength ---
            fs_res = {}
            if std_debt_app_cnt == 0:
                fs_res = {
                    "applicable": False,
                    "coverage_pct": None,
                    "score": None,
                    "status": "N_A",
                    "reason": "N_A_NO_STANDARD_DEBT_RULE_COMPANIES"
                }
            else:
                dte_norm = norm.get("debt_to_equity", {})
                dte_score = dte_norm.get("score")
                dte_comp = {
                    "score": dte_score,
                    "weight": 60.0,
                    "available": _is_finite(dte_score)
                }
                
                bor_norm = norm.get("borrowing_change_pct", {})
                bor_num_score = bor_norm.get("score")
                bor_trans = trans.get("borrowing", {})
                bor_fall_score = bor_trans.get("fallback_score")
                
                bor_num_cnt = bor_trans.get("numeric_status_count", 0)
                bor_fall_cnt = bor_trans.get("fallback_status_count", 0)
                bor_tot_ev = bor_num_cnt + bor_fall_cnt
                
                bor_eff = _blend_evidence(bor_num_score, bor_num_cnt, bor_fall_score, bor_fall_cnt, bor_tot_ev)
                bor_eff["weight"] = 40.0
                
                fs_res = _calc_pillar({
                    "debt_to_equity": dte_comp,
                    "borrowing_trend": bor_eff
                })
                fs_res["applicable"] = True
                fs_res["reason"] = None
                
            # --- Earnings Quality ---
            ocfp_norm = norm.get("latest_ocf_to_pat", {})
            ocfp_num_score = ocfp_norm.get("score")
            cc_trans = trans.get("cash_conversion", {})
            cc_fall_score = cc_trans.get("fallback_score")
            
            cc_num_cnt = cc_trans.get("numeric_status_count", 0)
            cc_fall_cnt = cc_trans.get("fallback_status_count", 0)
            cc_tot_ev = cc_num_cnt + cc_fall_cnt
            
            cc_eff = _blend_evidence(ocfp_num_score, cc_num_cnt, cc_fall_score, cc_fall_cnt, cc_tot_ev)
            cc_eff["weight"] = 40.0
            
            ocfc_norm = norm.get("ocf_to_pat_change", {})
            ocfc_score = ocfc_norm.get("score")
            ocfc_comp = {
                "score": ocfc_score,
                "weight": 20.0,
                "available": _is_finite(ocfc_score)
            }
            
            ppr_norm = norm.get("positive_pat_period_ratio", {})
            ppr_score = ppr_norm.get("score")
            ppr_comp = {
                "score": ppr_score,
                "weight": 25.0,
                "available": _is_finite(ppr_score)
            }
            
            pgv_norm = norm.get("pat_growth_volatility_pct", {})
            pgv_score = pgv_norm.get("score")
            pgv_comp = {
                "score": pgv_score,
                "weight": 15.0,
                "available": _is_finite(pgv_score)
            }
            
            eq_res = _calc_pillar({
                "latest_cash_conversion": cc_eff,
                "ocf_to_pat_change": ocfc_comp,
                "positive_pat_period_ratio": ppr_comp,
                "pat_growth_volatility": pgv_comp
            })
            
            if "fundamental" not in calc:
                calc["fundamental"] = {}
                
            calc["fundamental"]["pillar_scores"] = {
                "growth": growth_res,
                "profitability": prof_res,
                "financial_strength": fs_res,
                "earnings_quality": eq_res
            }
            
            bi.calculation_details = calc
            
            # --- Warnings ---
            warnings_set = set(bi.warnings or [])
            for w in ["GROWTH_PILLAR_PARTIAL", "GROWTH_PILLAR_UNAVAILABLE",
                      "PROFITABILITY_PILLAR_PARTIAL", "PROFITABILITY_PILLAR_UNAVAILABLE",
                      "FINANCIAL_STRENGTH_PILLAR_PARTIAL", "FINANCIAL_STRENGTH_PILLAR_UNAVAILABLE",
                      "EARNINGS_QUALITY_PILLAR_PARTIAL", "EARNINGS_QUALITY_PILLAR_UNAVAILABLE"]:
                warnings_set.discard(w)
                
            def _check_warn(pillar_res, prefix):
                if pillar_res.get("applicable", True) is False:
                    return
                cov = pillar_res.get("coverage_pct", 0.0)
                if cov == 0.0:
                    warnings_set.add(f"{prefix}_UNAVAILABLE")
                elif cov < 100.0:
                    warnings_set.add(f"{prefix}_PARTIAL")
                    
            _check_warn(growth_res, "GROWTH_PILLAR")
            _check_warn(prof_res, "PROFITABILITY_PILLAR")
            _check_warn(fs_res, "FINANCIAL_STRENGTH_PILLAR")
            _check_warn(eq_res, "EARNINGS_QUALITY_PILLAR")
            
            w_list = list(warnings_set)
            w_list.sort()
            bi.warnings = w_list
            
        self._disc.commit()
