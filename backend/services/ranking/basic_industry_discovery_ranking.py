"""Deterministic final basic-industry scoring, ranking, and selection."""
from __future__ import annotations

import copy
import datetime
import math
import uuid
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import or_
from sqlalchemy.orm import Session

import config
from models.discovery import DiscoverySelection, GroupScore


ENTITY_TYPE_INDUSTRY = "INDUSTRY"
ENTITY_TYPE_BASIC_INDUSTRY = "BASIC_INDUSTRY"
COMPONENT_WEIGHTS = {
    "technical": 40.0,
    "fundamental": 40.0,
    "macro": 20.0,
}
MIN_BASIC_INDUSTRY_DISCOVERY_COVERAGE = float(
    getattr(config, "MIN_BASIC_INDUSTRY_DISCOVERY_COVERAGE", 80.0)
)
BASIC_INDUSTRY_SELECTION_COUNT = int(
    getattr(config, "BASIC_INDUSTRY_SELECTION_COUNT", 1)
)
TECHNICAL_MIN_COVERAGE = 75.0

W_SELECTED_INDUSTRY_UNAVAILABLE = "SELECTED_INDUSTRY_UNAVAILABLE"
W_PARTIAL = "BASIC_INDUSTRY_FINAL_SCORE_PARTIAL"
W_LOW_COVERAGE = "BASIC_INDUSTRY_FINAL_SCORE_LOW_COVERAGE"
W_UNAVAILABLE = "BASIC_INDUSTRY_FINAL_SCORE_UNAVAILABLE"
W_TECHNICAL_INELIGIBLE = "BASIC_INDUSTRY_TECHNICAL_INELIGIBLE"
W_FUNDAMENTAL_INELIGIBLE = "BASIC_INDUSTRY_FUNDAMENTAL_INELIGIBLE"
W_MACRO_INELIGIBLE = "BASIC_INDUSTRY_MACRO_INELIGIBLE"
W_NO_ELIGIBLE = "NO_ELIGIBLE_BASIC_INDUSTRY"
W_STALE_SELECTION_REMOVED = "STALE_BASIC_INDUSTRY_SELECTION_REMOVED"

SCORING_WARNINGS = {
    W_PARTIAL,
    W_LOW_COVERAGE,
    W_UNAVAILABLE,
    W_TECHNICAL_INELIGIBLE,
    W_FUNDAMENTAL_INELIGIBLE,
    W_MACRO_INELIGIBLE,
    W_NO_ELIGIBLE,
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


def _technical_return_count(group: GroupScore, calc: Dict[str, Any]) -> int:
    technical = calc.get("technical") or {}
    return_details = technical.get("return") or {}
    for value in (
        return_details.get("return_eligible_count"),
        technical.get("return_eligible_count"),
        calc.get("return_eligible_count"),
        group.eligible_constituent_count,
    ):
        if isinstance(value, int):
            return value
    return 0


def _technical_coverage(group: GroupScore, calc: Dict[str, Any]) -> float:
    technical = calc.get("technical") or {}
    for value in (
        technical.get("coverage_pct"),
        technical.get("data_coverage"),
        group.data_coverage,
    ):
        if _is_finite(value):
            return float(value)
    return 0.0


def _component_detail(
    configured_weight: float,
    applicable: bool,
    available: bool,
    eligible: bool,
    score: Optional[float],
) -> Dict[str, Any]:
    clean_score = float(score) if _is_finite(score) and available else None
    return {
        "configured_weight": configured_weight,
        "applicable": applicable,
        "available": bool(available),
        "eligible": bool(eligible),
        "score": clean_score,
        "weighted_contribution": clean_score * configured_weight
        if clean_score is not None
        else None,
    }


def calculate_final_basic_industry_score(group: GroupScore) -> Tuple[Dict[str, Any], List[str]]:
    calc = copy.deepcopy(group.calculation_details or {})
    warnings: List[str] = []

    technical_score = group.technical_score
    fundamental_score = group.fundamental_score
    macro_score = group.macro_score
    macro_details = (calc.get("macro") or {}).get("basic_industry_score") or {}
    macro_status = macro_details.get("status")
    macro_applicable = macro_status != "N_A"

    return_count = _technical_return_count(group, calc)
    technical_coverage = _technical_coverage(group, calc)
    active_warnings = set(group.warnings or [])

    technical_available = _is_finite(technical_score)
    technical_eligible = bool(
        technical_available
        and technical_coverage >= TECHNICAL_MIN_COVERAGE
        and return_count >= config.MIN_BASIC_INDUSTRY_COMPANIES
        and "INSUFFICIENT_CONSTITUENTS" not in active_warnings
    )

    fundamental_available = _is_finite(fundamental_score)
    fundamental_eligible = bool(
        ((calc.get("fundamental") or {}).get("final_score") or {}).get(
            "eligible_for_selection"
        )
        is True
    )

    macro_available = macro_applicable and _is_finite(macro_score)
    macro_eligible = bool(
        not macro_applicable
        or macro_details.get("eligible_for_selection") is True
    )

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

    score = round(weighted_sum / available_weight, 2) if available_weight > 0 else None
    coverage_pct = (
        round((available_weight / applicable_weight) * 100.0, 2)
        if applicable_weight > 0
        else None
    )
    status = _status_from_score(score)

    if available_weight == 0:
        warnings.append(W_UNAVAILABLE)
    if available_weight < applicable_weight:
        warnings.append(W_PARTIAL)
    if (
        coverage_pct is not None
        and coverage_pct < MIN_BASIC_INDUSTRY_DISCOVERY_COVERAGE
    ):
        warnings.append(W_LOW_COVERAGE)
    if not technical_eligible:
        warnings.append(W_TECHNICAL_INELIGIBLE)
    if not fundamental_eligible:
        warnings.append(W_FUNDAMENTAL_INELIGIBLE)
    if macro_applicable and not macro_eligible:
        warnings.append(W_MACRO_INELIGIBLE)

    eligible = bool(
        _is_finite(score)
        and coverage_pct is not None
        and coverage_pct >= MIN_BASIC_INDUSTRY_DISCOVERY_COVERAGE
        and technical_eligible
        and fundamental_eligible
        and macro_eligible
    )

    return (
        {
            "parent_sector": group.parent_sector or "",
            "parent_industry": group.parent_industry or "",
            "components": components,
            "applicable_weight": applicable_weight,
            "available_weight": available_weight,
            "coverage_pct": coverage_pct,
            "score": score,
            "status": status,
            "eligible_for_selection": eligible,
            "rank": None,
        },
        sorted(set(warnings)),
    )


class BasicIndustryDiscoveryRankingService:
    def __init__(self, discovery_session: Session):
        self._disc = discovery_session

    def rank_and_select(self, run_id: str, horizon: str) -> Dict[str, Any]:
        selected_hierarchy = self._selected_parent_hierarchy(run_id, horizon)
        if selected_hierarchy is None:
            stale_selection_count = self._deactivate_basic_industry_selections(
                run_id, horizon, set()
            )
            cleanup_count = self._cleanup_unselected_hierarchy_ranks(
                run_id, horizon, selected_hierarchy=None
            )
            self._disc.commit()
            warnings = [W_SELECTED_INDUSTRY_UNAVAILABLE]
            if stale_selection_count:
                warnings.append(W_STALE_SELECTION_REMOVED)
            return {
                "warnings": sorted(warnings),
                "metadata": self._metadata(
                    horizon=horizon,
                    selected_parent_sector=None,
                    selected_parent_industry=None,
                    basic_industries=[],
                    results={},
                    selected_names=[],
                    stale_selection_count=stale_selection_count,
                    cleanup_count=cleanup_count,
                ),
                "ranked_basic_industries": [],
                "selected_basic_industries": [],
            }

        selected_sector, selected_industry = selected_hierarchy
        cleanup_count = self._cleanup_unselected_hierarchy_ranks(
            run_id, horizon, selected_hierarchy=selected_hierarchy
        )
        basic_industries = (
            self._disc.query(GroupScore)
            .filter_by(
                run_id=run_id,
                horizon=horizon,
                entity_type=ENTITY_TYPE_BASIC_INDUSTRY,
                parent_sector=selected_sector,
                parent_industry=selected_industry,
            )
            .order_by(GroupScore.entity_name.asc())
            .all()
        )
        basic_industries = [
            row for row in basic_industries if (row.entity_name or "").strip()
        ]

        results: Dict[str, Dict[str, Any]] = {}
        entity_warnings: Dict[str, List[str]] = {}
        for basic_industry in basic_industries:
            details, warnings = calculate_final_basic_industry_score(basic_industry)
            results[basic_industry.entity_name] = details
            entity_warnings[basic_industry.entity_name] = warnings

        eligible_names = [
            row.entity_name
            for row in basic_industries
            if results[row.entity_name]["eligible_for_selection"]
        ]
        eligible_names.sort(key=lambda name: (-results[name]["score"], name))

        for rank, name in enumerate(eligible_names, start=1):
            results[name]["rank"] = rank

        global_warnings: List[str] = []
        if not eligible_names and basic_industries:
            global_warnings.append(W_NO_ELIGIBLE)

        for basic_industry in basic_industries:
            self._persist_group_result(
                basic_industry,
                results[basic_industry.entity_name],
                entity_warnings[basic_industry.entity_name],
            )

        selected_names = eligible_names[:BASIC_INDUSTRY_SELECTION_COUNT]
        selected_keys = {
            (name, selected_sector, selected_industry)
            for name in selected_names
        }
        stale_selection_count = self._deactivate_basic_industry_selections(
            run_id, horizon, selected_keys
        )
        if stale_selection_count:
            global_warnings.append(W_STALE_SELECTION_REMOVED)
        self._persist_selected_basic_industries(
            run_id,
            horizon,
            selected_sector,
            selected_industry,
            basic_industries,
            results,
            selected_names,
        )
        self._disc.commit()

        return {
            "warnings": sorted(set(global_warnings)),
            "metadata": self._metadata(
                horizon=horizon,
                selected_parent_sector=selected_sector,
                selected_parent_industry=selected_industry,
                basic_industries=basic_industries,
                results=results,
                selected_names=selected_names,
                stale_selection_count=stale_selection_count,
                cleanup_count=cleanup_count,
            ),
            "ranked_basic_industries": [
                {
                    "entity_name": name,
                    "parent_sector": selected_sector,
                    "parent_industry": selected_industry,
                    "rank": results[name]["rank"],
                    "final_score": results[name]["score"],
                    "status": results[name]["status"],
                    "coverage_pct": results[name]["coverage_pct"],
                    "eligible_for_selection": results[name]["eligible_for_selection"],
                }
                for name in eligible_names
            ],
            "selected_basic_industries": selected_names,
        }

    def _selected_parent_hierarchy(self, run_id: str, horizon: str) -> Optional[Tuple[str, str]]:
        rows = (
            self._disc.query(DiscoverySelection)
            .filter_by(
                run_id=run_id,
                horizon=horizon,
                entity_type=ENTITY_TYPE_INDUSTRY,
                selected=True,
            )
            .order_by(DiscoverySelection.rank.asc(), DiscoverySelection.entity_name.asc())
            .all()
        )
        for row in rows:
            sector = (row.parent_sector or "").strip()
            industry = (row.entity_name or "").strip()
            if sector and industry:
                return sector, industry
        return None

    def _cleanup_unselected_hierarchy_ranks(
        self,
        run_id: str,
        horizon: str,
        selected_hierarchy: Optional[Tuple[str, str]],
    ) -> int:
        query = self._disc.query(GroupScore).filter_by(
            run_id=run_id,
            horizon=horizon,
            entity_type=ENTITY_TYPE_BASIC_INDUSTRY,
        )
        if selected_hierarchy is not None:
            selected_sector, selected_industry = selected_hierarchy
            query = query.filter(
                or_(
                    GroupScore.parent_sector != selected_sector,
                    GroupScore.parent_industry != selected_industry,
                )
            )
        rows = query.all()

        cleanup_count = 0
        for row in rows:
            calc = copy.deepcopy(row.calculation_details or {})
            discovery = copy.deepcopy(calc.get("discovery") or {})
            details = discovery.get("final_basic_industry_score")
            had_active_rank = row.rank is not None or (
                isinstance(details, dict) and details.get("rank") is not None
            )
            if row.rank is not None:
                row.rank = None
            if isinstance(details, dict) and details.get("rank") is not None:
                details = copy.deepcopy(details)
                details["rank"] = None
                discovery["final_basic_industry_score"] = details
                calc["discovery"] = discovery
                row.calculation_details = calc
            if had_active_rank:
                cleanup_count += 1
        return cleanup_count

    def _persist_group_result(
        self,
        basic_industry: GroupScore,
        details: Dict[str, Any],
        warnings: List[str],
    ) -> None:
        calc = copy.deepcopy(basic_industry.calculation_details or {})
        discovery = copy.deepcopy(calc.get("discovery") or {})
        discovery["final_basic_industry_score"] = details
        calc["discovery"] = discovery

        existing_warnings = set(basic_industry.warnings or [])
        existing_warnings.difference_update(SCORING_WARNINGS)
        existing_warnings.update(warnings)

        basic_industry.final_score = details["score"]
        basic_industry.rank = details["rank"]
        basic_industry.warnings = sorted(existing_warnings)
        basic_industry.calculation_details = calc

    def _deactivate_basic_industry_selections(
        self,
        run_id: str,
        horizon: str,
        selected_keys: set[Tuple[str, str, str]],
    ) -> int:
        rows = (
            self._disc.query(DiscoverySelection)
            .filter_by(
                run_id=run_id,
                horizon=horizon,
                entity_type=ENTITY_TYPE_BASIC_INDUSTRY,
            )
            .all()
        )
        stale_count = 0
        now = datetime.datetime.utcnow()
        for row in rows:
            key = (row.entity_name, row.parent_sector or "", row.parent_industry or "")
            if key in selected_keys:
                continue
            if row.selected:
                stale_count += 1
            row.selected = False
            row.updated_at = now
        return stale_count

    def _persist_selected_basic_industries(
        self,
        run_id: str,
        horizon: str,
        selected_sector: str,
        selected_industry: str,
        basic_industries: List[GroupScore],
        results: Dict[str, Dict[str, Any]],
        selected_names: List[str],
    ) -> None:
        by_name = {row.entity_name: row for row in basic_industries}
        for name in selected_names:
            group = by_name[name]
            details = results[name]
            row = (
                self._disc.query(DiscoverySelection)
                .filter_by(
                    run_id=run_id,
                    horizon=horizon,
                    entity_type=ENTITY_TYPE_BASIC_INDUSTRY,
                    entity_name=name,
                    parent_sector=selected_sector,
                    parent_industry=selected_industry,
                )
                .first()
            )
            now = datetime.datetime.utcnow()
            if row is None:
                row = DiscoverySelection(
                    id=str(uuid.uuid4()),
                    run_id=run_id,
                    horizon=horizon,
                    entity_type=ENTITY_TYPE_BASIC_INDUSTRY,
                    entity_name=name,
                    parent_sector=selected_sector,
                    parent_industry=selected_industry,
                    created_at=now,
                )
                self._disc.add(row)

            row.rank = details["rank"]
            row.final_score = details["score"]
            row.technical_score = group.technical_score
            row.fundamental_score = group.fundamental_score
            row.macro_score = group.macro_score
            row.selected = True
            row.selection_reason = (
                f"Selected rank {details['rank']} basic industry in "
                f"{selected_sector} / {selected_industry} for {horizon} horizon."
            )
            row.calculation_details = {
                "discovery": {
                    "final_basic_industry_score": copy.deepcopy(details),
                }
            }
            row.updated_at = now

    def _metadata(
        self,
        horizon: str,
        selected_parent_sector: Optional[str],
        selected_parent_industry: Optional[str],
        basic_industries: List[GroupScore],
        results: Dict[str, Dict[str, Any]],
        selected_names: List[str],
        stale_selection_count: int,
        cleanup_count: int,
    ) -> Dict[str, Any]:
        metadata = {
            "horizon": horizon,
            "selected_parent_sector": selected_parent_sector,
            "selected_parent_industry": selected_parent_industry,
            "basic_industry_count": len(basic_industries),
            "scored_basic_industry_count": 0,
            "eligible_basic_industry_count": 0,
            "ineligible_basic_industry_count": 0,
            "selected_basic_industry_count": len(selected_names),
            "selected_basic_industry": selected_names[0] if selected_names else None,
            "very_strong_count": 0,
            "strong_count": 0,
            "neutral_count": 0,
            "weak_count": 0,
            "very_weak_count": 0,
            "stale_selection_count": stale_selection_count,
            "unselected_hierarchy_rank_cleanup_count": cleanup_count,
        }
        status_keys = {
            "VERY_STRONG": "very_strong_count",
            "STRONG": "strong_count",
            "NEUTRAL": "neutral_count",
            "WEAK": "weak_count",
            "VERY_WEAK": "very_weak_count",
        }
        for details in results.values():
            if _is_finite(details["score"]):
                metadata["scored_basic_industry_count"] += 1
            if details["eligible_for_selection"]:
                metadata["eligible_basic_industry_count"] += 1
            else:
                metadata["ineligible_basic_industry_count"] += 1
            key = status_keys.get(details["status"])
            if key:
                metadata[key] += 1
        return metadata
