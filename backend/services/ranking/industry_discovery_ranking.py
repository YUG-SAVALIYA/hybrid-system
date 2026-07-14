"""Deterministic final industry scoring, ranking, and selection."""
from __future__ import annotations

import copy
import datetime
import math
import uuid
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

import config
from models.discovery import DiscoverySelection, GroupScore


ENTITY_TYPE_SECTOR = "SECTOR"
ENTITY_TYPE_INDUSTRY = "INDUSTRY"
COMPONENT_WEIGHTS = {
    "technical": 40.0,
    "fundamental": 40.0,
    "macro": 20.0,
}
MIN_INDUSTRY_DISCOVERY_COVERAGE = float(
    getattr(config, "MIN_INDUSTRY_DISCOVERY_COVERAGE", 80.0)
)
INDUSTRY_SELECTION_COUNT = int(getattr(config, "INDUSTRY_SELECTION_COUNT", 1))
TECHNICAL_MIN_COVERAGE = 75.0

W_SELECTED_SECTOR_UNAVAILABLE = "SELECTED_SECTOR_UNAVAILABLE"
W_PARTIAL = "INDUSTRY_FINAL_SCORE_PARTIAL"
W_LOW_COVERAGE = "INDUSTRY_FINAL_SCORE_LOW_COVERAGE"
W_UNAVAILABLE = "INDUSTRY_FINAL_SCORE_UNAVAILABLE"
W_TECHNICAL_INELIGIBLE = "INDUSTRY_TECHNICAL_INELIGIBLE"
W_FUNDAMENTAL_INELIGIBLE = "INDUSTRY_FUNDAMENTAL_INELIGIBLE"
W_MACRO_INELIGIBLE = "INDUSTRY_MACRO_INELIGIBLE"
W_NO_ELIGIBLE = "NO_ELIGIBLE_INDUSTRY"
W_STALE_SELECTION_REMOVED = "STALE_INDUSTRY_SELECTION_REMOVED"

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


def calculate_final_industry_score(group: GroupScore) -> Tuple[Dict[str, Any], List[str]]:
    calc = copy.deepcopy(group.calculation_details or {})
    warnings: List[str] = []

    technical_score = group.technical_score
    fundamental_score = group.fundamental_score
    macro_score = group.macro_score
    macro_details = (calc.get("macro") or {}).get("industry_score") or {}
    macro_status = macro_details.get("status")
    macro_applicable = macro_status != "N_A"

    return_count = _technical_return_count(group, calc)
    technical_coverage = _technical_coverage(group, calc)
    active_warnings = set(group.warnings or [])

    technical_available = _is_finite(technical_score)
    technical_eligible = bool(
        technical_available
        and technical_coverage >= TECHNICAL_MIN_COVERAGE
        and return_count >= config.MIN_INDUSTRY_COMPANIES
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
        and coverage_pct < MIN_INDUSTRY_DISCOVERY_COVERAGE
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
        and coverage_pct >= MIN_INDUSTRY_DISCOVERY_COVERAGE
        and technical_eligible
        and fundamental_eligible
        and macro_eligible
    )

    return (
        {
            "parent_sector": group.parent_sector or "",
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


class IndustryDiscoveryRankingService:
    def __init__(self, discovery_session: Session):
        self._disc = discovery_session

    def rank_and_select(self, run_id: str, horizon: str) -> Dict[str, Any]:
        selected_sector = self._selected_parent_sector(run_id, horizon)
        if selected_sector is None:
            stale_selection_count = self._deactivate_industry_selections(
                run_id, horizon, set()
            )
            cleanup_count = self._cleanup_unselected_sector_ranks(
                run_id, horizon, selected_sector=None
            )
            self._disc.commit()
            warnings = [W_SELECTED_SECTOR_UNAVAILABLE]
            if stale_selection_count:
                warnings.append(W_STALE_SELECTION_REMOVED)
            return {
                "warnings": sorted(warnings),
                "metadata": self._metadata(
                    horizon=horizon,
                    selected_parent_sector=None,
                    industries=[],
                    results={},
                    selected_names=[],
                    stale_selection_count=stale_selection_count,
                    cleanup_count=cleanup_count,
                ),
                "ranked_industries": [],
                "selected_industries": [],
            }

        cleanup_count = self._cleanup_unselected_sector_ranks(
            run_id, horizon, selected_sector=selected_sector
        )
        industries = (
            self._disc.query(GroupScore)
            .filter_by(
                run_id=run_id,
                horizon=horizon,
                entity_type=ENTITY_TYPE_INDUSTRY,
                parent_sector=selected_sector,
                parent_industry="",
            )
            .order_by(GroupScore.entity_name.asc())
            .all()
        )
        industries = [row for row in industries if (row.entity_name or "").strip()]

        results: Dict[str, Dict[str, Any]] = {}
        industry_warnings: Dict[str, List[str]] = {}
        for industry in industries:
            details, warnings = calculate_final_industry_score(industry)
            results[industry.entity_name] = details
            industry_warnings[industry.entity_name] = warnings

        eligible_names = [
            industry.entity_name
            for industry in industries
            if results[industry.entity_name]["eligible_for_selection"]
        ]
        eligible_names.sort(key=lambda name: (-results[name]["score"], name))

        for rank, name in enumerate(eligible_names, start=1):
            results[name]["rank"] = rank

        global_warnings: List[str] = []
        if not eligible_names and industries:
            global_warnings.append(W_NO_ELIGIBLE)

        for industry in industries:
            details = results[industry.entity_name]
            self._persist_group_result(
                industry, details, industry_warnings[industry.entity_name]
            )

        selected_names = eligible_names[:INDUSTRY_SELECTION_COUNT]
        selected_keys = {
            (name, selected_sector, "")
            for name in selected_names
        }
        stale_selection_count = self._deactivate_industry_selections(
            run_id, horizon, selected_keys
        )
        if stale_selection_count:
            global_warnings.append(W_STALE_SELECTION_REMOVED)
        self._persist_selected_industries(
            run_id, horizon, selected_sector, industries, results, selected_names
        )
        self._disc.commit()

        return {
            "warnings": sorted(set(global_warnings)),
            "metadata": self._metadata(
                horizon=horizon,
                selected_parent_sector=selected_sector,
                industries=industries,
                results=results,
                selected_names=selected_names,
                stale_selection_count=stale_selection_count,
                cleanup_count=cleanup_count,
            ),
            "ranked_industries": [
                {
                    "entity_name": name,
                    "parent_sector": selected_sector,
                    "rank": results[name]["rank"],
                    "final_score": results[name]["score"],
                    "status": results[name]["status"],
                    "coverage_pct": results[name]["coverage_pct"],
                    "eligible_for_selection": results[name]["eligible_for_selection"],
                }
                for name in eligible_names
            ],
            "selected_industries": selected_names,
        }

    def _selected_parent_sector(self, run_id: str, horizon: str) -> Optional[str]:
        rows = (
            self._disc.query(DiscoverySelection)
            .filter_by(
                run_id=run_id,
                horizon=horizon,
                entity_type=ENTITY_TYPE_SECTOR,
                selected=True,
            )
            .order_by(DiscoverySelection.rank.asc(), DiscoverySelection.entity_name.asc())
            .all()
        )
        for row in rows:
            if (row.entity_name or "").strip():
                return row.entity_name
        return None

    def _cleanup_unselected_sector_ranks(
        self,
        run_id: str,
        horizon: str,
        selected_sector: Optional[str],
    ) -> int:
        query = self._disc.query(GroupScore).filter_by(
            run_id=run_id,
            horizon=horizon,
            entity_type=ENTITY_TYPE_INDUSTRY,
        )
        if selected_sector is not None:
            query = query.filter(GroupScore.parent_sector != selected_sector)
        rows = query.all()

        cleanup_count = 0
        for row in rows:
            calc = copy.deepcopy(row.calculation_details or {})
            discovery = copy.deepcopy(calc.get("discovery") or {})
            details = discovery.get("final_industry_score")
            had_active_rank = row.rank is not None or (
                isinstance(details, dict) and details.get("rank") is not None
            )
            if row.rank is not None:
                row.rank = None
            if isinstance(details, dict) and details.get("rank") is not None:
                details = copy.deepcopy(details)
                details["rank"] = None
                discovery["final_industry_score"] = details
                calc["discovery"] = discovery
                row.calculation_details = calc
            if had_active_rank:
                cleanup_count += 1
        return cleanup_count

    def _persist_group_result(
        self,
        industry: GroupScore,
        details: Dict[str, Any],
        warnings: List[str],
    ) -> None:
        calc = copy.deepcopy(industry.calculation_details or {})
        discovery = copy.deepcopy(calc.get("discovery") or {})
        discovery["final_industry_score"] = details
        calc["discovery"] = discovery

        existing_warnings = set(industry.warnings or [])
        existing_warnings.difference_update(SCORING_WARNINGS)
        existing_warnings.update(warnings)

        industry.final_score = details["score"]
        industry.rank = details["rank"]
        industry.warnings = sorted(existing_warnings)
        industry.calculation_details = calc

    def _deactivate_industry_selections(
        self,
        run_id: str,
        horizon: str,
        selected_keys: set[Tuple[str, str, str]],
    ) -> int:
        rows = (
            self._disc.query(DiscoverySelection)
            .filter_by(run_id=run_id, horizon=horizon, entity_type=ENTITY_TYPE_INDUSTRY)
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

    def _persist_selected_industries(
        self,
        run_id: str,
        horizon: str,
        selected_sector: str,
        industries: List[GroupScore],
        results: Dict[str, Dict[str, Any]],
        selected_names: List[str],
    ) -> None:
        industries_by_name = {industry.entity_name: industry for industry in industries}
        for name in selected_names:
            industry = industries_by_name[name]
            details = results[name]
            row = (
                self._disc.query(DiscoverySelection)
                .filter_by(
                    run_id=run_id,
                    horizon=horizon,
                    entity_type=ENTITY_TYPE_INDUSTRY,
                    entity_name=name,
                    parent_sector=selected_sector,
                    parent_industry="",
                )
                .first()
            )
            now = datetime.datetime.utcnow()
            if row is None:
                row = DiscoverySelection(
                    id=str(uuid.uuid4()),
                    run_id=run_id,
                    horizon=horizon,
                    entity_type=ENTITY_TYPE_INDUSTRY,
                    entity_name=name,
                    parent_sector=selected_sector,
                    parent_industry="",
                    created_at=now,
                )
                self._disc.add(row)

            row.rank = details["rank"]
            row.final_score = details["score"]
            row.technical_score = industry.technical_score
            row.fundamental_score = industry.fundamental_score
            row.macro_score = industry.macro_score
            row.selected = True
            row.selection_reason = (
                f"Selected rank {details['rank']} industry in {selected_sector} "
                f"for {horizon} horizon."
            )
            row.calculation_details = {
                "discovery": {
                    "final_industry_score": copy.deepcopy(details),
                }
            }
            row.updated_at = now

    def _metadata(
        self,
        horizon: str,
        selected_parent_sector: Optional[str],
        industries: List[GroupScore],
        results: Dict[str, Dict[str, Any]],
        selected_names: List[str],
        stale_selection_count: int,
        cleanup_count: int,
    ) -> Dict[str, Any]:
        metadata = {
            "horizon": horizon,
            "selected_parent_sector": selected_parent_sector,
            "industry_count": len(industries),
            "scored_industry_count": 0,
            "eligible_industry_count": 0,
            "ineligible_industry_count": 0,
            "selected_industry_count": len(selected_names),
            "selected_industry": selected_names[0] if selected_names else None,
            "very_strong_count": 0,
            "strong_count": 0,
            "neutral_count": 0,
            "weak_count": 0,
            "very_weak_count": 0,
            "stale_selection_count": stale_selection_count,
            "unselected_sector_rank_cleanup_count": cleanup_count,
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
                metadata["scored_industry_count"] += 1
            if details["eligible_for_selection"]:
                metadata["eligible_industry_count"] += 1
            else:
                metadata["ineligible_industry_count"] += 1
            key = status_keys.get(details["status"])
            if key:
                metadata[key] += 1
        return metadata
