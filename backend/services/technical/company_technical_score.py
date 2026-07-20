"""Authoritative company-level technical final scoring."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from sqlalchemy import update
from sqlalchemy.orm import Session

import config
from models.discovery import CompanyTechnicalMetric


COMPONENT_WEIGHTS = {
    "return": 40.0,
    "volume": 20.0,
    "consistency": 40.0,
}
MIN_COMPANY_TECHNICAL_COVERAGE = float(
    getattr(config, "MIN_COMPANY_TECHNICAL_COVERAGE", 75.0)
)


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


def _return_score(row: CompanyTechnicalMetric) -> Optional[float]:
    if row.return_available is not True or not _is_finite(row.relative_return):
        return None
    scale = getattr(config, "RELATIVE_RETURN_FULL_SCALE_PCT", 10.0)
    score = 50.0 + (row.relative_return / scale) * 50.0
    return max(0.0, min(100.0, score))


def _volume_score(row: CompanyTechnicalMetric) -> Optional[float]:
    if row.volume_available is not True or not _is_finite(row.volume_change):
        return None
    if not _is_finite(row.company_return):
        return None
        
    scale = getattr(config, "VOLUME_CHANGE_FULL_SCALE_PCT", 50.0)
    
    if row.company_return > 0:
        score = 50.0 + (row.volume_change / scale) * 50.0
    elif row.company_return < 0:
        score = 50.0 - (row.volume_change / scale) * 50.0
    else:
        score = 50.0
        
    return max(0.0, min(100.0, score))


def _consistency_score(row: CompanyTechnicalMetric) -> Optional[float]:
    if row.consistency_available is not True:
        return None
    if not _is_finite(row.company_consistency_score):
        return None
    return float(row.company_consistency_score)


class CompanyTechnicalScoreService:
    def __init__(self, discovery_session: Session):
        self._disc = discovery_session

    def score_companies(self, run_id: str, horizon: str) -> Dict[str, Any]:
        rows = (
            self._disc.query(CompanyTechnicalMetric)
            .filter_by(run_id=run_id, horizon=horizon)
            .order_by(CompanyTechnicalMetric.symbol.asc())
            .all()
        )
        updates: List[Dict[str, Any]] = []
        eligible_count = 0
        scored_count = 0
        for row in rows:
            components = {
                "return": _return_score(row),
                "volume": _volume_score(row),
                "consistency": _consistency_score(row),
            }
            available_weight = 0.0
            weighted_sum = 0.0
            details = {}
            for name, score in components.items():
                available = _is_finite(score)
                weight = COMPONENT_WEIGHTS[name]
                if available:
                    available_weight += weight
                    weighted_sum += float(score) * weight
                details[name] = {
                    "configured_weight": weight,
                    "available": available,
                    "score": float(score) if available else None,
                    "weighted_contribution": float(score) * weight if available else None,
                }

            total_weight = sum(COMPONENT_WEIGHTS.values())
            coverage_pct = round((available_weight / total_weight) * 100.0, 2)
            final_score = round(weighted_sum / available_weight, 2) if available_weight else None
            eligible = bool(
                _is_finite(final_score)
                and coverage_pct >= MIN_COMPANY_TECHNICAL_COVERAGE
                and row.return_available is True
            )
            if final_score is not None:
                scored_count += 1
            if eligible:
                eligible_count += 1

            calc = dict(row.calculation_details or {})
            calc["technical_score"] = {
                "components": details,
                "available_weight": available_weight,
                "applicable_weight": total_weight,
                "coverage_pct": coverage_pct,
                "score": final_score,
                "status": _status_from_score(final_score),
                "eligible_for_selection": eligible,
            }
            updates.append(
                {
                    "id": row.id,
                    "final_technical_score": final_score,
                    "technical_status": _status_from_score(final_score),
                    "technical_eligible_for_selection": eligible,
                    "data_coverage": coverage_pct,
                    "calculation_details": calc,
                }
            )

        if updates:
            self._disc.execute(update(CompanyTechnicalMetric), updates)
            self._disc.commit()
        return {
            "horizon": horizon,
            "company_count": len(rows),
            "scored_company_count": scored_count,
            "eligible_company_count": eligible_count,
        }
