"""Deterministic scoring for existing stock candidates."""
from __future__ import annotations

import datetime
import math
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

import config
from models.discovery import (
    CompanyFundamentalMetric,
    CompanyTechnicalMetric,
    GroupScore,
    StockCandidateSnapshot,
)
from services.stock.stock_candidate_universe import STATUS_ELIGIBLE


ENTITY_TYPE_BASIC_INDUSTRY = "BASIC_INDUSTRY"
COMPONENT_WEIGHTS = {
    "technical": 40.0,
    "fundamental": 40.0,
    "macro": 20.0,
}
MIN_STOCK_SCORE_COVERAGE = float(getattr(config, "MIN_STOCK_SCORE_COVERAGE", 80.0))

W_PARTIAL = "STOCK_SCORE_PARTIAL"
W_LOW_COVERAGE = "STOCK_SCORE_LOW_COVERAGE"
W_UNAVAILABLE = "STOCK_SCORE_UNAVAILABLE"
W_TECHNICAL_UNAVAILABLE = "STOCK_TECHNICAL_SCORE_UNAVAILABLE"
W_TECHNICAL_INELIGIBLE = "STOCK_TECHNICAL_SCORE_INELIGIBLE"
W_FUNDAMENTAL_UNAVAILABLE = "STOCK_FUNDAMENTAL_SCORE_UNAVAILABLE"
W_FUNDAMENTAL_INELIGIBLE = "STOCK_FUNDAMENTAL_SCORE_INELIGIBLE"
W_MACRO_UNAVAILABLE = "STOCK_MACRO_SCORE_UNAVAILABLE"
W_MACRO_INELIGIBLE = "STOCK_MACRO_SCORE_INELIGIBLE"
W_STALE = "STOCK_SCORE_STALE"

SCORING_WARNINGS = {
    W_PARTIAL,
    W_LOW_COVERAGE,
    W_UNAVAILABLE,
    W_TECHNICAL_UNAVAILABLE,
    W_TECHNICAL_INELIGIBLE,
    W_FUNDAMENTAL_UNAVAILABLE,
    W_FUNDAMENTAL_INELIGIBLE,
    W_MACRO_UNAVAILABLE,
    W_MACRO_INELIGIBLE,
    W_STALE,
}


def _is_finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def _status_from_score(score: Optional[float]) -> str:
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


def _nested_get(data: Any, path: Tuple[str, ...]) -> Any:
    current = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _technical_score_from(metric: CompanyTechnicalMetric) -> Optional[float]:
    calc = metric.calculation_details or {}
    for path in (
        ("technical_score",),
        ("technical", "score"),
        ("technical", "technical_score"),
        ("technical", "final_score", "score"),
        ("final_score", "score"),
        ("company_technical_score",),
    ):
        value = _nested_get(calc, path)
        if _is_finite(value):
            return float(value)
    return None


def _component_detail(
    configured_weight: float,
    applicable: bool,
    available: bool,
    eligible: bool,
    score: Optional[float],
    **extra: Any,
) -> Dict[str, Any]:
    clean_score = float(score) if available and _is_finite(score) else None
    detail = {
        "configured_weight": configured_weight,
        "applicable": applicable,
        "available": bool(available),
        "eligible": bool(eligible),
        "score": clean_score,
        "weighted_contribution": clean_score * configured_weight
        if clean_score is not None
        else None,
    }
    detail.update(extra)
    return detail


def _clear_score_fields(candidate: StockCandidateSnapshot) -> bool:
    had_score = any(
        value is not None
        for value in (
            candidate.technical_score,
            candidate.fundamental_score,
            candidate.inherited_macro_score,
            candidate.final_score,
            candidate.score_coverage_pct,
            candidate.score_status,
            candidate.score_eligible,
            candidate.score_details,
            candidate.scored_at,
        )
    ) or bool(candidate.score_warnings)
    candidate.technical_score = None
    candidate.fundamental_score = None
    candidate.inherited_macro_score = None
    candidate.final_score = None
    candidate.score_coverage_pct = None
    candidate.score_status = None
    candidate.score_eligible = None
    candidate.score_warnings = []
    candidate.score_details = None
    candidate.scored_at = None
    return had_score


class StockCandidateScoreService:
    def __init__(self, discovery_session: Session):
        self._disc = discovery_session

    def score_candidates(self, run_id: str, horizon: str) -> Dict[str, Any]:
        candidates = (
            self._disc.query(StockCandidateSnapshot)
            .filter_by(run_id=run_id, horizon=horizon)
            .order_by(StockCandidateSnapshot.symbol.asc(), StockCandidateSnapshot.company_id.asc())
            .all()
        )
        metadata = {
            "candidate_count": len(candidates),
            "universe_eligible_count": 0,
            "scored_candidate_count": 0,
            "score_eligible_count": 0,
            "score_ineligible_count": 0,
            "very_strong_count": 0,
            "strong_count": 0,
            "neutral_count": 0,
            "weak_count": 0,
            "very_weak_count": 0,
            "unavailable_count": 0,
            "macro_n_a_count": 0,
            "partial_score_count": 0,
            "stale_score_count": 0,
        }

        for candidate in candidates:
            if candidate.eligible:
                metadata["universe_eligible_count"] += 1
            if not candidate.eligible or candidate.status != STATUS_ELIGIBLE:
                if _clear_score_fields(candidate):
                    metadata["stale_score_count"] += 1
                continue

            sources = self._load_sources(candidate)
            if sources["stale"]:
                if _clear_score_fields(candidate):
                    metadata["stale_score_count"] += 1
                candidate.score_warnings = [W_STALE]
                continue

            details, warnings = self._calculate_score(candidate, sources)
            self._persist_score(candidate, details, warnings)
            self._count_result(metadata, details, warnings)

        self._disc.commit()
        return metadata

    def _load_sources(self, candidate: StockCandidateSnapshot) -> Dict[str, Any]:
        technical = (
            self._disc.query(CompanyTechnicalMetric)
            .filter_by(id=candidate.technical_metric_id)
            .first()
            if candidate.technical_metric_id
            else None
        )
        fundamental = (
            self._disc.query(CompanyFundamentalMetric)
            .filter_by(id=candidate.fundamental_metric_id)
            .first()
            if candidate.fundamental_metric_id
            else None
        )
        macro_group = (
            self._disc.query(GroupScore)
            .filter_by(
                run_id=candidate.run_id,
                horizon=candidate.horizon,
                entity_type=ENTITY_TYPE_BASIC_INDUSTRY,
                entity_name=candidate.basic_industry,
                parent_sector=candidate.sector,
                parent_industry=candidate.industry,
            )
            .first()
        )

        technical_identity_ok = bool(
            technical is not None
            and technical.run_id == candidate.run_id
            and technical.horizon == candidate.horizon
            and technical.source_company_id == candidate.company_id
        )
        fundamental_identity_ok = bool(
            fundamental is not None
            and fundamental.run_id == candidate.run_id
            and fundamental.source_company_id == candidate.company_id
        )
        stale = not technical_identity_ok or not fundamental_identity_ok or macro_group is None
        return {
            "technical": technical if technical_identity_ok else None,
            "fundamental": fundamental if fundamental_identity_ok else None,
            "macro_group": macro_group,
            "stale": stale,
        }

    def _calculate_score(
        self,
        candidate: StockCandidateSnapshot,
        sources: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], List[str]]:
        warnings: List[str] = []
        technical = sources["technical"]
        fundamental = sources["fundamental"]
        macro_group = sources["macro_group"]

        technical_score = _technical_score_from(technical)
        technical_available = bool(candidate.technical_available and _is_finite(technical_score))
        technical_eligible = bool(candidate.technical_available and technical_available)
        if not technical_available:
            warnings.append(W_TECHNICAL_UNAVAILABLE)
        if not technical_eligible:
            warnings.append(W_TECHNICAL_INELIGIBLE)

        fundamental_score = fundamental.final_fundamental_score
        fundamental_available = bool(candidate.fundamental_available and _is_finite(fundamental_score))
        fundamental_eligible = bool(candidate.fundamental_available and fundamental_available)
        if not fundamental_available:
            warnings.append(W_FUNDAMENTAL_UNAVAILABLE)
        if not fundamental_eligible:
            warnings.append(W_FUNDAMENTAL_INELIGIBLE)

        macro_details = ((macro_group.calculation_details or {}).get("macro") or {}).get(
            "basic_industry_score"
        ) or {}
        macro_status = macro_details.get("status")
        macro_applicable = macro_status != "N_A"
        macro_score = macro_group.macro_score
        macro_available = bool(macro_applicable and _is_finite(macro_score))
        macro_eligible = bool(
            not macro_applicable
            or (
                macro_available
                and macro_details.get("eligible_for_selection") is True
            )
        )
        if macro_applicable and not macro_available:
            warnings.append(W_MACRO_UNAVAILABLE)
        if macro_applicable and not macro_eligible:
            warnings.append(W_MACRO_INELIGIBLE)

        components = {
            "technical": _component_detail(
                COMPONENT_WEIGHTS["technical"],
                True,
                technical_available,
                technical_eligible,
                technical_score,
            ),
            "fundamental": _component_detail(
                COMPONENT_WEIGHTS["fundamental"],
                True,
                fundamental_available,
                fundamental_eligible,
                fundamental_score,
            ),
            "macro": _component_detail(
                COMPONENT_WEIGHTS["macro"],
                macro_applicable,
                macro_available,
                macro_eligible,
                macro_score,
                source_entity_type=ENTITY_TYPE_BASIC_INDUSTRY,
                source_entity_name=candidate.basic_industry,
            ),
        }
        applicable_weight = sum(
            item["configured_weight"] for item in components.values() if item["applicable"]
        )
        available_weight = sum(
            item["configured_weight"] for item in components.values() if item["available"]
        )
        weighted_sum = sum(
            item["weighted_contribution"]
            for item in components.values()
            if item["weighted_contribution"] is not None
        )
        final_score = round(weighted_sum / available_weight, 2) if available_weight > 0 else None
        coverage_pct = (
            round((available_weight / applicable_weight) * 100.0, 2)
            if applicable_weight > 0
            else None
        )
        status = _status_from_score(final_score)
        if available_weight == 0:
            warnings.append(W_UNAVAILABLE)
        if available_weight < applicable_weight:
            warnings.append(W_PARTIAL)
        if coverage_pct is not None and coverage_pct < MIN_STOCK_SCORE_COVERAGE:
            warnings.append(W_LOW_COVERAGE)

        score_eligible = bool(
            candidate.eligible is True
            and _is_finite(final_score)
            and coverage_pct is not None
            and coverage_pct >= MIN_STOCK_SCORE_COVERAGE
            and technical_eligible
            and fundamental_eligible
            and macro_eligible
        )
        return (
            {
                "components": components,
                "applicable_weight": applicable_weight,
                "available_weight": available_weight,
                "coverage_pct": coverage_pct,
                "final_score": final_score,
                "status": status,
                "score_eligible": score_eligible,
            },
            sorted(set(warnings)),
        )

    def _persist_score(
        self,
        candidate: StockCandidateSnapshot,
        details: Dict[str, Any],
        warnings: List[str],
    ) -> None:
        components = details["components"]
        candidate.technical_score = components["technical"]["score"]
        candidate.fundamental_score = components["fundamental"]["score"]
        candidate.inherited_macro_score = components["macro"]["score"]
        candidate.final_score = details["final_score"]
        candidate.score_coverage_pct = details["coverage_pct"]
        candidate.score_status = details["status"]
        candidate.score_eligible = details["score_eligible"]
        candidate.score_warnings = sorted(set(warnings))
        candidate.score_details = details
        candidate.scored_at = datetime.datetime.utcnow()

    def _count_result(
        self,
        metadata: Dict[str, Any],
        details: Dict[str, Any],
        warnings: List[str],
    ) -> None:
        if details["final_score"] is not None:
            metadata["scored_candidate_count"] += 1
        if details["score_eligible"]:
            metadata["score_eligible_count"] += 1
        else:
            metadata["score_ineligible_count"] += 1

        status_key = {
            "VERY_STRONG": "very_strong_count",
            "STRONG": "strong_count",
            "NEUTRAL": "neutral_count",
            "WEAK": "weak_count",
            "VERY_WEAK": "very_weak_count",
            "UNAVAILABLE": "unavailable_count",
        }.get(details["status"])
        if status_key:
            metadata[status_key] += 1
        if not details["components"]["macro"]["applicable"]:
            metadata["macro_n_a_count"] += 1
        if W_PARTIAL in warnings:
            metadata["partial_score_count"] += 1
