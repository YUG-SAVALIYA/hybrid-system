"""Deterministic final sector scoring, ranking, and selection."""
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
COMPONENT_WEIGHTS = {
    "technical": 40.0,
    "fundamental": 40.0,
    "macro": 20.0,
}
MIN_SECTOR_DISCOVERY_COVERAGE = float(
    getattr(config, "MIN_SECTOR_DISCOVERY_COVERAGE", 80.0)
)
SECTOR_SELECTION_COUNT = int(getattr(config, "SECTOR_SELECTION_COUNT", 1))
TECHNICAL_MIN_COVERAGE = 75.0

W_PARTIAL = "SECTOR_FINAL_SCORE_PARTIAL"
W_LOW_COVERAGE = "SECTOR_FINAL_SCORE_LOW_COVERAGE"
W_UNAVAILABLE = "SECTOR_FINAL_SCORE_UNAVAILABLE"
W_TECHNICAL_INELIGIBLE = "SECTOR_TECHNICAL_INELIGIBLE"
W_FUNDAMENTAL_INELIGIBLE = "SECTOR_FUNDAMENTAL_INELIGIBLE"
W_MACRO_INELIGIBLE = "SECTOR_MACRO_INELIGIBLE"
W_NO_ELIGIBLE = "NO_ELIGIBLE_SECTOR"

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


def calculate_final_sector_score(group: GroupScore) -> Tuple[Dict[str, Any], List[str]]:
    calc = copy.deepcopy(group.calculation_details or {})
    warnings: List[str] = []

    technical_score = group.technical_score
    fundamental_score = group.fundamental_score
    macro_score = group.macro_score
    macro_details = (calc.get("macro") or {}).get("sector_score") or {}
    macro_status = macro_details.get("status")
    macro_applicable = macro_status != "N_A"

    return_count = _technical_return_count(group, calc)
    technical_coverage = _technical_coverage(group, calc)
    active_warnings = set(group.warnings or [])

    technical_available = _is_finite(technical_score)
    technical_eligible = bool(
        technical_available
        and technical_coverage >= TECHNICAL_MIN_COVERAGE
        and return_count >= config.MIN_SECTOR_COMPANIES
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
    elif available_weight < applicable_weight:
        warnings.append(W_PARTIAL)
    if (
        coverage_pct is not None
        and coverage_pct < MIN_SECTOR_DISCOVERY_COVERAGE
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
        and coverage_pct >= MIN_SECTOR_DISCOVERY_COVERAGE
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
            "score": score,
            "status": status,
            "eligible_for_selection": eligible,
            "rank": None,
        },
        sorted(set(warnings)),
    )


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


class SectorDiscoveryRankingService:
    def __init__(self, discovery_session: Session):
        self._disc = discovery_session

    def rank_and_select(self, run_id: str, horizon: str) -> Dict[str, Any]:
        sectors = (
            self._disc.query(GroupScore)
            .filter_by(run_id=run_id, horizon=horizon, entity_type=ENTITY_TYPE_SECTOR)
            .order_by(GroupScore.entity_name.asc())
            .all()
        )

        results: Dict[str, Dict[str, Any]] = {}
        sector_warnings: Dict[str, List[str]] = {}
        for sector in sectors:
            details, warnings = calculate_final_sector_score(sector)
            results[sector.entity_name] = details
            sector_warnings[sector.entity_name] = warnings

        eligible_names = [
            sector.entity_name
            for sector in sectors
            if results[sector.entity_name]["eligible_for_selection"]
        ]
        eligible_names.sort(
            key=lambda name: (-results[name]["score"], name)
        )

        for rank, name in enumerate(eligible_names, start=1):
            results[name]["rank"] = rank

        global_warnings: List[str] = []
        if not eligible_names and sectors:
            global_warnings.append(W_NO_ELIGIBLE)

        for sector in sectors:
            details = results[sector.entity_name]
            self._persist_group_result(sector, details, sector_warnings[sector.entity_name])

        selected_names = eligible_names[:SECTOR_SELECTION_COUNT]
        self._persist_selections(run_id, horizon, sectors, results, selected_names)
        self._disc.commit()

        metadata = self._metadata(sectors, results, selected_names, horizon)
        return {
            "warnings": global_warnings,
            "metadata": metadata,
            "ranked_sectors": [
                {
                    "entity_name": name,
                    "rank": results[name]["rank"],
                    "final_score": results[name]["score"],
                }
                for name in eligible_names
            ],
            "selected_sectors": selected_names,
        }

    def _persist_group_result(
        self,
        sector: GroupScore,
        details: Dict[str, Any],
        warnings: List[str],
    ) -> None:
        calc = copy.deepcopy(sector.calculation_details or {})
        discovery = copy.deepcopy(calc.get("discovery") or {})
        discovery["final_sector_score"] = details
        calc["discovery"] = discovery

        existing_warnings = set(sector.warnings or [])
        existing_warnings.difference_update(SCORING_WARNINGS)
        existing_warnings.update(warnings)

        sector.final_score = details["score"]
        sector.rank = details["rank"]
        sector.warnings = sorted(existing_warnings)
        sector.calculation_details = calc

    def _persist_selections(
        self,
        run_id: str,
        horizon: str,
        sectors: List[GroupScore],
        results: Dict[str, Dict[str, Any]],
        selected_names: List[str],
    ) -> None:
        existing = (
            self._disc.query(DiscoverySelection)
            .filter_by(run_id=run_id, horizon=horizon, entity_type=ENTITY_TYPE_SECTOR)
            .all()
        )
        for row in existing:
            row.selected = False
            row.updated_at = datetime.datetime.utcnow()

        sectors_by_name = {sector.entity_name: sector for sector in sectors}
        for name in selected_names:
            sector = sectors_by_name[name]
            details = results[name]
            row = (
                self._disc.query(DiscoverySelection)
                .filter_by(
                    run_id=run_id,
                    horizon=horizon,
                    entity_type=ENTITY_TYPE_SECTOR,
                    entity_name=name,
                )
                .first()
            )
            now = datetime.datetime.utcnow()
            if row is None:
                row = DiscoverySelection(
                    id=str(uuid.uuid4()),
                    run_id=run_id,
                    horizon=horizon,
                    entity_type=ENTITY_TYPE_SECTOR,
                    entity_name=name,
                    created_at=now,
                )
                self._disc.add(row)

            row.rank = details["rank"]
            row.final_score = details["score"]
            row.technical_score = sector.technical_score
            row.fundamental_score = sector.fundamental_score
            row.macro_score = sector.macro_score
            row.selected = True
            row.selection_reason = (
                f"Selected rank {details['rank']} sector for {horizon} horizon."
            )
            row.calculation_details = {
                "discovery": {
                    "final_sector_score": copy.deepcopy(details),
                }
            }
            row.updated_at = now

    def _metadata(
        self,
        sectors: List[GroupScore],
        results: Dict[str, Dict[str, Any]],
        selected_names: List[str],
        horizon: str,
    ) -> Dict[str, Any]:
        metadata = {
            "sector_count": len(sectors),
            "scored_sector_count": 0,
            "eligible_sector_count": 0,
            "ineligible_sector_count": 0,
            "selected_sector_count": len(selected_names),
            "selected_sectors_by_horizon": {horizon: list(selected_names)},
            "very_strong_count": 0,
            "strong_count": 0,
            "neutral_count": 0,
            "weak_count": 0,
            "very_weak_count": 0,
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
                metadata["scored_sector_count"] += 1
            if details["eligible_for_selection"]:
                metadata["eligible_sector_count"] += 1
            else:
                metadata["ineligible_sector_count"] += 1
            key = status_keys.get(details["status"])
            if key:
                metadata[key] += 1
        return metadata
