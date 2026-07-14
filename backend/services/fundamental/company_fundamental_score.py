"""
CompanyFundamentalScoreService

Calculates the final deterministic company fundamental score.
"""
from __future__ import annotations

import logging
import copy
from sqlalchemy.orm import Session

import config
from models.discovery import CompanyFundamentalMetric

logger = logging.getLogger(__name__)

MIN_COMPANY_FUNDAMENTAL_COVERAGE = getattr(config, 'MIN_COMPANY_FUNDAMENTAL_COVERAGE', 75.0)

def _get_status(score: float | None) -> str:
    if score is None: return "UNAVAILABLE"
    if score >= 80.0: return "VERY_STRONG"
    if score >= 65.0: return "STRONG"
    if score >= 50.0: return "NEUTRAL"
    if score >= 35.0: return "WEAK"
    return "VERY_WEAK"

def _is_finite(val: float | None) -> bool:
    if val is None: return False
    if isinstance(val, (int, float)):
        return val == val and val != float('inf') and val != float('-inf')
    return False

class CompanyFundamentalScoreService:
    def __init__(self, discovery_session: Session):
        self._disc = discovery_session

    def score_companies(self, run_id: str) -> None:
        records = self._disc.query(CompanyFundamentalMetric).filter_by(run_id=run_id).all()
        if not records:
            return

        for rec in records:
            calc_details = rec.calculation_details or {}
            fund_scoring = calc_details.get("fundamental_scoring", {})
            warnings_set = set(calc_details.get("warnings", []))
            
            growth = fund_scoring.get("growth", {})
            prof = fund_scoring.get("profitability", {})
            fs = fund_scoring.get("financial_strength", {})
            eq = fund_scoring.get("earnings_quality", {})

            # 1. Component parsing
            growth_avail = (growth.get("score") is not None) and _is_finite(growth.get("score"))
            prof_avail = (prof.get("score") is not None) and _is_finite(prof.get("score"))
            
            # financial strength is applicable unless explicitly false
            fs_applicable = fs.get("applicable", True)
            fs_avail = (fs.get("score") is not None) and _is_finite(fs.get("score"))
            
            eq_avail = (eq.get("score") is not None) and _is_finite(eq.get("score"))

            components = {}
            
            def add_comp(name, weight, applicable, available, score, reason=None):
                weighted = None
                if applicable and available and _is_finite(score):
                    weighted = score * weight
                    
                components[name] = {
                    "configured_weight": weight,
                    "applicable": applicable,
                    "available": available,
                    "score": score if (applicable and available and _is_finite(score)) else None,
                    "weighted_contribution": weighted,
                    "reason": reason
                }

            add_comp("growth", 25.0, True, growth_avail, growth.get("score"))
            add_comp("profitability", 25.0, True, prof_avail, prof.get("score"))
            
            fs_reason = None
            if not fs_applicable:
                fs_reason = "N_A_STANDARD_DEBT_RULE"
            add_comp("financial_strength", 25.0, fs_applicable, fs_avail, fs.get("score"), reason=fs_reason)
            
            add_comp("earnings_quality", 25.0, True, eq_avail, eq.get("score"))

            # 2. Coverage calculation
            applicable_weight = 0.0
            available_weight = 0.0
            sum_weighted = 0.0
            
            for c_val in components.values():
                if c_val["applicable"]:
                    applicable_weight += c_val["configured_weight"]
                    if c_val["available"]:
                        available_weight += c_val["configured_weight"]
                        sum_weighted += c_val["weighted_contribution"]

            coverage_pct = 0.0
            if applicable_weight > 0:
                coverage_pct = (available_weight / applicable_weight) * 100.0

            # 3. Final score calculation
            final_score = None
            if available_weight > 0:
                final_score = sum_weighted / available_weight

            # 4. Status and eligibility
            status = _get_status(final_score)
            eligible = (final_score is not None) and (coverage_pct >= MIN_COMPANY_FUNDAMENTAL_COVERAGE)

            # 5. Warnings
            warnings_set.discard("FUNDAMENTAL_SCORE_PARTIAL")
            warnings_set.discard("FUNDAMENTAL_SCORE_LOW_COVERAGE")
            warnings_set.discard("FUNDAMENTAL_SCORE_UNAVAILABLE")

            if available_weight == 0:
                warnings_set.add("FUNDAMENTAL_SCORE_UNAVAILABLE")
            else:
                if available_weight < applicable_weight:
                    warnings_set.add("FUNDAMENTAL_SCORE_PARTIAL")
                if coverage_pct < MIN_COMPANY_FUNDAMENTAL_COVERAGE:
                    warnings_set.add("FUNDAMENTAL_SCORE_LOW_COVERAGE")

            # 6. Persistence
            new_calc = copy.deepcopy(calc_details)
            if "fundamental_scoring" not in new_calc:
                new_calc["fundamental_scoring"] = {}
                
            new_calc["fundamental_scoring"]["final"] = {
                "components": components,
                "applicable_weight": applicable_weight,
                "available_weight": available_weight,
                "coverage_pct": coverage_pct,
                "score": final_score,
                "status": status,
                "eligible_for_selection": eligible
            }
            
            warn_list = list(warnings_set)
            warn_list.sort()
            new_calc["warnings"] = warn_list
            
            rec.calculation_details = new_calc
            rec.final_fundamental_score = final_score
            rec.fundamental_status = status
            rec.fundamental_eligible_for_selection = eligible
            
        self._disc.commit()
