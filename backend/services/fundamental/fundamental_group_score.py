"""
FundamentalGroupScoreService

Hierarchy-aware final deterministic fundamental score for sectors,
industries, and basic industries.
"""
from __future__ import annotations

import copy
import logging
from typing import Any, Dict

import config
from sqlalchemy.orm import Session

from models.discovery import GroupScore

logger = logging.getLogger(__name__)

MIN_GROUP_FUNDAMENTAL_COVERAGE = getattr(config, "MIN_GROUP_FUNDAMENTAL_COVERAGE", 75.0)
MIN_SECTOR_FUNDAMENTAL_COMPANIES = getattr(config, "MIN_SECTOR_FUNDAMENTAL_COMPANIES", 5)
MIN_INDUSTRY_FUNDAMENTAL_COMPANIES = getattr(config, "MIN_INDUSTRY_FUNDAMENTAL_COMPANIES", 3)
MIN_BASIC_INDUSTRY_FUNDAMENTAL_COMPANIES = getattr(
    config, "MIN_BASIC_INDUSTRY_FUNDAMENTAL_COMPANIES", 2
)


def _is_finite(val: float | None) -> bool:
    if val is None:
        return False
    if isinstance(val, (int, float)):
        return val == val and val != float("inf") and val != float("-inf")
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


ENTITY_CONFIG: Dict[str, Dict[str, Any]] = {
    "SECTOR": {
        "warning_prefix": "SECTOR",
        "min_constituents": MIN_SECTOR_FUNDAMENTAL_COMPANIES,
        "entity_type": "SECTOR",
        "query_parent_industry": None,
    },
    "INDUSTRY": {
        "warning_prefix": "INDUSTRY",
        "min_constituents": MIN_INDUSTRY_FUNDAMENTAL_COMPANIES,
        "entity_type": "INDUSTRY",
        "query_parent_industry": "",
    },
    "BASIC_INDUSTRY": {
        "warning_prefix": "BASIC_INDUSTRY",
        "min_constituents": MIN_BASIC_INDUSTRY_FUNDAMENTAL_COMPANIES,
        "entity_type": "BASIC_INDUSTRY",
        "query_parent_industry": None,
    },
}


class FundamentalGroupScoreService:
    def __init__(self, discovery_session: Session):
        self._disc = discovery_session

    def calculate_final_scores(
        self,
        run_id: str,
        horizon: str | None = None,
        entity_type: str = "SECTOR",
        parent_sector: str | None = None,
        parent_industry: str | None = None,
    ) -> None:
        entity_type = (entity_type or "").upper().strip()
        if entity_type not in ENTITY_CONFIG:
            raise ValueError(f"Unsupported entity_type: {entity_type}")
        self._calculate_for_entity(
            run_id=run_id,
            horizon=horizon,
            entity_type=entity_type,
            parent_sector=parent_sector,
            parent_industry=parent_industry,
        )

    def _calculate_for_entity(
        self,
        run_id: str,
        horizon: str | None,
        entity_type: str,
        parent_sector: str | None,
        parent_industry: str | None,
    ) -> None:
        cfg = ENTITY_CONFIG[entity_type]
        query = self._disc.query(GroupScore).filter_by(run_id=run_id, entity_type=cfg["entity_type"])
        if horizon is not None:
            query = query.filter_by(horizon=horizon)
        if entity_type == "INDUSTRY":
            if parent_sector is not None:
                query = query.filter(GroupScore.parent_sector == parent_sector)
            query = query.filter(GroupScore.parent_industry == "")
        elif entity_type == "BASIC_INDUSTRY":
            if parent_sector is not None:
                query = query.filter(GroupScore.parent_sector == parent_sector)
            if parent_industry is not None:
                query = query.filter(GroupScore.parent_industry == parent_industry)

        groups = query.all()
        if not groups:
            return

        warning_prefix = cfg["warning_prefix"]
        min_constituents = cfg["min_constituents"]
        score_partial_warning = f"{warning_prefix}_FUNDAMENTAL_SCORE_PARTIAL"
        low_coverage_warning = f"{warning_prefix}_FUNDAMENTAL_LOW_COVERAGE"
        unavailable_warning = f"{warning_prefix}_FUNDAMENTAL_SCORE_UNAVAILABLE"

        for group in groups:
            calc = copy.deepcopy(group.calculation_details or {})
            fund = calc.get("fundamental", {})
            pillars = fund.get("pillar_scores", {})
            raw_agg = fund.get("raw_aggregation", {})
            avail_cnt = raw_agg.get("fundamental_score_available_count", 0)

            warnings_set = set(group.warnings or [])
            warnings_set.discard(score_partial_warning)
            warnings_set.discard(low_coverage_warning)
            warnings_set.discard(unavailable_warning)
            warnings_set.discard("INSUFFICIENT_FUNDAMENTAL_CONSTITUENTS")

            p_config = {
                "growth": {"weight": 25.0, "applicable": True},
                "profitability": {"weight": 25.0, "applicable": True},
                "financial_strength": {"weight": 25.0, "applicable": True},
                "earnings_quality": {"weight": 25.0, "applicable": True},
            }

            if not pillars.get("financial_strength", {}).get("applicable", True):
                p_config["financial_strength"]["applicable"] = False

            applicable_w = 0.0
            available_w = 0.0
            weighted_sum = 0.0
            pillar_res: Dict[str, Any] = {}

            for p_name, p_conf in p_config.items():
                weight = p_conf["weight"]
                applicable = p_conf["applicable"]
                p_data = pillars.get(p_name, {})
                p_score = p_data.get("score")

                available = False
                contribution = None
                reason = p_data.get("reason")

                if applicable:
                    applicable_w += weight
                    if _is_finite(p_score) and p_data.get("status") not in ("UNAVAILABLE", "N_A"):
                        available = True
                        available_w += weight
                        contribution = p_score * weight
                        weighted_sum += contribution

                pillar_res[p_name] = {
                    "configured_weight": weight,
                    "applicable": applicable,
                    "available": available,
                    "score": p_score if available else None,
                    "weighted_contribution": contribution,
                    "reason": reason,
                }

            coverage_pct = 0.0
            if applicable_w > 0:
                coverage_pct = round((available_w / applicable_w) * 100.0, 2)

            final_score = None
            if available_w > 0:
                final_score = round(weighted_sum / available_w, 2)

            status = _get_status(final_score)

            eligible = False
            if final_score is not None and coverage_pct >= MIN_GROUP_FUNDAMENTAL_COVERAGE and avail_cnt >= min_constituents:
                eligible = True

            if available_w == 0:
                warnings_set.add(unavailable_warning)
            elif available_w < applicable_w:
                warnings_set.add(score_partial_warning)

            if coverage_pct < MIN_GROUP_FUNDAMENTAL_COVERAGE:
                warnings_set.add(low_coverage_warning)

            if avail_cnt < min_constituents:
                warnings_set.add("INSUFFICIENT_FUNDAMENTAL_CONSTITUENTS")

            if "fundamental" not in calc:
                calc["fundamental"] = {}

            calc["fundamental"]["final_score"] = {
                "pillars": pillar_res,
                "applicable_weight": applicable_w,
                "available_weight": available_w,
                "coverage_pct": coverage_pct,
                "score": final_score,
                "status": status,
                "fundamental_score_available_count": avail_cnt,
                "minimum_constituents_required": min_constituents,
                "eligible_for_selection": eligible,
            }

            group.fundamental_score = final_score
            group.warnings = sorted(warnings_set)
            group.calculation_details = calc

        self._disc.commit()
