"""Deterministic numeric macro scoring for sector group scores."""
from __future__ import annotations

import copy
import math
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

import config
from models.discovery import GroupScore, MacroEntityImpact
from services.macro.macro_filter_summary import CATEGORIES


CATEGORY_WEIGHT = 25.0
CATEGORY_WEIGHTS = {category: CATEGORY_WEIGHT for category in CATEGORIES}
IMPACT_NUMERIC_VALUES = {
    "POSITIVE": 100.0,
    "NEUTRAL": 50.0,
    "NEGATIVE": 0.0,
}
CONFIDENCE_MULTIPLIERS = {
    "HIGH": 1.0,
    "MEDIUM": 0.75,
    "LOW": 0.5,
}
VALID_IMPACTS = {"POSITIVE", "NEUTRAL", "NEGATIVE", "N_A", "UNCERTAIN"}
VALID_CONFIDENCES = set(CONFIDENCE_MULTIPLIERS)
MIN_SECTOR_MACRO_COVERAGE = float(
    getattr(config, "MIN_SECTOR_MACRO_COVERAGE", 75.0)
)

W_PARTIAL = "SECTOR_MACRO_SCORE_PARTIAL"
W_LOW_COVERAGE = "SECTOR_MACRO_LOW_COVERAGE"
W_UNAVAILABLE = "SECTOR_MACRO_SCORE_UNAVAILABLE"
W_ALL_NA = "ALL_MACRO_CATEGORIES_NOT_APPLICABLE"
W_INVALID_IMPACT = "INVALID_CATEGORY_IMPACT"
W_INVALID_CONFIDENCE = "INVALID_CATEGORY_CONFIDENCE"
W_OVERALL_CONFLICT = "OVERALL_IMPACT_SCORE_CONFLICT"

SCORING_WARNINGS = {
    W_PARTIAL,
    W_LOW_COVERAGE,
    W_UNAVAILABLE,
    W_ALL_NA,
    W_INVALID_IMPACT,
    W_INVALID_CONFIDENCE,
    W_OVERALL_CONFLICT,
}


def _is_finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def _round_or_none(value: Optional[float]) -> Optional[float]:
    return round(value, 2) if value is not None else None


def _status_from_score(score: Optional[float], applicable_weight: float, available_weight: float) -> str:
    if applicable_weight == 0:
        return "N_A"
    if available_weight == 0 or score is None:
        return "UNAVAILABLE"
    if score >= 80.0:
        return "VERY_POSITIVE"
    if score >= 60.0:
        return "POSITIVE"
    if score >= 40.0:
        return "NEUTRAL"
    if score >= 20.0:
        return "NEGATIVE"
    return "VERY_NEGATIVE"


def _broad_impact_from_status(status: str) -> Optional[str]:
    if status in {"VERY_POSITIVE", "POSITIVE"}:
        return "POSITIVE"
    if status == "NEUTRAL":
        return "NEUTRAL"
    if status in {"NEGATIVE", "VERY_NEGATIVE"}:
        return "NEGATIVE"
    return None


def _category_detail(raw: Any, category: str) -> tuple[Dict[str, Any], List[str]]:
    warnings: List[str] = []
    data = raw if isinstance(raw, dict) else {}
    impact = data.get("impact")
    confidence = data.get("confidence")

    valid_impact = impact in VALID_IMPACTS
    valid_confidence = confidence in VALID_CONFIDENCES

    if not valid_impact:
        impact = "UNCERTAIN"
        warnings.append(W_INVALID_IMPACT)
    if not valid_confidence:
        confidence_multiplier = None
        warnings.append(W_INVALID_CONFIDENCE)
    else:
        confidence_multiplier = CONFIDENCE_MULTIPLIERS[confidence]

    configured_weight = CATEGORY_WEIGHTS[category]
    applicable = impact != "N_A"
    numeric_value = IMPACT_NUMERIC_VALUES.get(impact)
    available = numeric_value is not None and valid_confidence
    effective_weight = configured_weight * confidence_multiplier if available else None

    return (
        {
            "impact": impact,
            "confidence": confidence if valid_confidence else None,
            "numeric_value": numeric_value if available else None,
            "configured_weight": configured_weight,
            "confidence_multiplier": confidence_multiplier,
            "effective_weight": effective_weight,
            "applicable": applicable,
            "available": available,
        },
        warnings,
    )


def calculate_sector_macro_score(
    category_impacts: Dict[str, Any],
    overall_impact: Optional[Dict[str, Any]] = None,
) -> tuple[Dict[str, Any], List[str]]:
    warnings: List[str] = []
    categories: Dict[str, Any] = {}
    applicable_weight = 0.0
    available_weight = 0.0
    effective_available_weight = 0.0
    weighted_sum = 0.0

    for category in CATEGORIES:
        detail, detail_warnings = _category_detail(
            (category_impacts or {}).get(category), category
        )
        categories[category] = detail
        warnings.extend(detail_warnings)

        if detail["applicable"]:
            applicable_weight += detail["configured_weight"]
        if detail["available"]:
            available_weight += detail["configured_weight"]
            effective_available_weight += detail["effective_weight"]
            weighted_sum += detail["numeric_value"] * detail["effective_weight"]

    score = None
    if effective_available_weight > 0:
        score = round(weighted_sum / effective_available_weight, 2)

    coverage_pct = None
    if applicable_weight > 0:
        coverage_pct = round((available_weight / applicable_weight) * 100.0, 2)

    confidence_quality_pct = None
    if available_weight > 0:
        confidence_quality_pct = round(
            (effective_available_weight / available_weight) * 100.0, 2
        )

    status = _status_from_score(score, applicable_weight, available_weight)
    if applicable_weight == 0:
        eligible = True
        warnings.append(W_ALL_NA)
    else:
        eligible = bool(
            _is_finite(score)
            and coverage_pct is not None
            and coverage_pct >= MIN_SECTOR_MACRO_COVERAGE
        )
        if available_weight == 0:
            warnings.append(W_UNAVAILABLE)
        elif available_weight < applicable_weight:
            warnings.append(W_PARTIAL)
        if coverage_pct is not None and coverage_pct < MIN_SECTOR_MACRO_COVERAGE:
            warnings.append(W_LOW_COVERAGE)

    llm_overall = None
    if isinstance(overall_impact, dict):
        llm_overall = overall_impact.get("impact")
    derived_broad = _broad_impact_from_status(status)
    if (
        derived_broad in {"POSITIVE", "NEUTRAL", "NEGATIVE"}
        and llm_overall in {"POSITIVE", "NEUTRAL", "NEGATIVE"}
        and derived_broad != llm_overall
    ):
        warnings.append(W_OVERALL_CONFLICT)

    return (
        {
            "categories": categories,
            "applicable_weight": applicable_weight,
            "available_weight": available_weight,
            "effective_available_weight": round(effective_available_weight, 2),
            "coverage_pct": coverage_pct,
            "confidence_quality_pct": confidence_quality_pct,
            "score": score,
            "status": status,
            "eligible_for_selection": eligible,
            "llm_overall_impact": llm_overall,
            "derived_broad_impact": derived_broad,
        },
        sorted(set(warnings)),
    )


class MacroSectorScoreService:
    def __init__(self, discovery_session: Session):
        self._disc = discovery_session

    def calculate_sector_scores(self, run_id: str, horizon: str | None = None) -> Dict[str, int]:
        impact_query = self._disc.query(MacroEntityImpact).filter_by(run_id=run_id, entity_type="SECTOR")
        if horizon is not None:
            impact_query = impact_query.filter_by(horizon=horizon)
        impacts = impact_query.order_by(MacroEntityImpact.horizon.asc(), MacroEntityImpact.entity_name.asc()).all()

        metadata = {
            "sector_count": len(impacts),
            "scored_sector_count": 0,
            "n_a_sector_count": 0,
            "unavailable_sector_count": 0,
            "eligible_sector_count": 0,
            "ineligible_sector_count": 0,
            "very_positive_count": 0,
            "positive_count": 0,
            "neutral_count": 0,
            "negative_count": 0,
            "very_negative_count": 0,
            "overall_conflict_count": 0,
            "stale_score_count": 0,
        }

        for impact_row in impacts:
            group_query = self._disc.query(GroupScore).filter_by(
                run_id=run_id,
                entity_type="SECTOR",
                entity_name=impact_row.entity_name,
                parent_sector="",
                parent_industry="",
            )
            if horizon is not None:
                group_query = group_query.filter_by(horizon=impact_row.horizon)
            groups = group_query.all()
            if not groups:
                continue

            score_details, warnings = calculate_sector_macro_score(
                impact_row.category_impacts or {},
                impact_row.overall_impact or {},
            )
            self._count_result(metadata, score_details, warnings)

            for group in groups:
                calc = copy.deepcopy(group.calculation_details or {})
                macro = copy.deepcopy(calc.get("macro") or {})
                macro["sector_score"] = score_details
                calc["macro"] = macro

                existing_warnings = set(group.warnings or [])
                existing_warnings.difference_update(SCORING_WARNINGS)
                existing_warnings.update(warnings)

                group.macro_score = score_details["score"]
                group.warnings = sorted(existing_warnings)
                group.calculation_details = calc

        metadata["stale_score_count"] += self._cleanup_stale_scores(
            run_id,
            horizon,
            {
                (impact.parent_sector or "", impact.entity_name or "")
                for impact in impacts
            },
        )

        self._disc.commit()
        return metadata

    def _count_result(
        self,
        metadata: Dict[str, int],
        details: Dict[str, Any],
        warnings: List[str],
    ) -> None:
        status = details["status"]
        if status == "N_A":
            metadata["n_a_sector_count"] += 1
        elif status == "UNAVAILABLE":
            metadata["unavailable_sector_count"] += 1
        else:
            metadata["scored_sector_count"] += 1

        if details["eligible_for_selection"]:
            metadata["eligible_sector_count"] += 1
        else:
            metadata["ineligible_sector_count"] += 1

        status_key = {
            "VERY_POSITIVE": "very_positive_count",
            "POSITIVE": "positive_count",
            "NEUTRAL": "neutral_count",
            "NEGATIVE": "negative_count",
            "VERY_NEGATIVE": "very_negative_count",
        }.get(status)
        if status_key:
            metadata[status_key] += 1
        if W_OVERALL_CONFLICT in warnings:
            metadata["overall_conflict_count"] += 1

    def _cleanup_stale_scores(self, run_id: str, horizon: str | None, current_keys: set[tuple[str, str]]) -> int:
        group_query = self._disc.query(GroupScore).filter_by(run_id=run_id, entity_type="SECTOR")
        if horizon is not None:
            group_query = group_query.filter_by(horizon=horizon)
        groups = group_query.all()
        stale_count = 0
        for group in groups:
            calc = copy.deepcopy(group.calculation_details or {})
            macro = copy.deepcopy(calc.get("macro") or {})
            if "sector_score" not in macro:
                continue
            key = (group.parent_sector or "", group.entity_name or "")
            if key in current_keys:
                continue
            macro["sector_score"] = {
                "available": False,
                "eligible_for_selection": False,
                "status": "UNAVAILABLE",
                "reason": W_UNAVAILABLE,
            }
            calc["macro"] = macro
            warnings = set(group.warnings or [])
            warnings.add(W_UNAVAILABLE)
            group.macro_score = None
            group.warnings = sorted(warnings)
            group.calculation_details = calc
            stale_count += 1
        return stale_count
