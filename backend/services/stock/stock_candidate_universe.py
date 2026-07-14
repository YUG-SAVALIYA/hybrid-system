"""Deterministic stock-candidate universe creation for selected basic industries."""
from __future__ import annotations

import copy
import datetime
import math
import uuid
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

import config
from models.discovery import (
    CompanyFundamentalMetric,
    CompanyTechnicalMetric,
    DiscoverySelection,
    EligibleUniverseSnapshot,
    StockCandidateSnapshot,
)


ENTITY_TYPE_BASIC_INDUSTRY = "BASIC_INDUSTRY"
STATUS_ELIGIBLE = "ELIGIBLE"
STATUS_TECHNICAL_UNAVAILABLE = "TECHNICAL_UNAVAILABLE"
STATUS_FUNDAMENTAL_UNAVAILABLE = "FUNDAMENTAL_UNAVAILABLE"
STATUS_BOTH_UNAVAILABLE = "TECHNICAL_AND_FUNDAMENTAL_UNAVAILABLE"

W_SELECTED_UNAVAILABLE = "SELECTED_BASIC_INDUSTRY_UNAVAILABLE"
W_EMPTY = "STOCK_UNIVERSE_EMPTY"
W_SYMBOL_MISSING = "STOCK_SYMBOL_MISSING"
W_TECHNICAL_METRIC_UNAVAILABLE = "STOCK_TECHNICAL_METRIC_UNAVAILABLE"
W_TECHNICAL_INELIGIBLE = "STOCK_TECHNICAL_INELIGIBLE"
W_FUNDAMENTAL_METRIC_UNAVAILABLE = "STOCK_FUNDAMENTAL_METRIC_UNAVAILABLE"
W_FUNDAMENTAL_INELIGIBLE = "STOCK_FUNDAMENTAL_INELIGIBLE"
W_STALE_REMOVED = "STALE_STOCK_CANDIDATE_REMOVED"

TECHNICAL_MIN_COVERAGE = float(getattr(config, "MIN_GROUP_TECHNICAL_COVERAGE", 75.0))
FUNDAMENTAL_MIN_COVERAGE = 75.0


def _is_finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def _coverage_pct(value: Any) -> Optional[float]:
    if not _is_finite(value):
        return None
    coverage = float(value)
    if 0.0 <= coverage <= 1.0:
        coverage *= 100.0
    return round(coverage, 2)


def _status_unavailable(value: Any) -> bool:
    if isinstance(value, str):
        return value.upper() == "UNAVAILABLE"
    if isinstance(value, dict):
        for key in ("status", "technical_status", "final_status"):
            if _status_unavailable(value.get(key)):
                return True
    return False


class StockCandidateUniverseService:
    def __init__(self, discovery_session: Session):
        self._disc = discovery_session
        self.last_selected_hierarchy: Optional[Dict[str, str]] = None

    def build_candidates(self, run_id: str, horizon: str) -> Dict[str, Any]:
        hierarchy = self._selected_hierarchy(run_id, horizon)
        if hierarchy is None:
            stale_count = self._cleanup_stale_candidates(run_id, horizon, set())
            self._disc.commit()
            return {
                "warnings": [W_SELECTED_UNAVAILABLE] + ([W_STALE_REMOVED] if stale_count else []),
                "metadata": self._metadata(
                    horizon=horizon,
                    hierarchy=None,
                    company_count=0,
                    eligible_count=0,
                    ineligible_count=0,
                    technical_unavailable_count=0,
                    fundamental_unavailable_count=0,
                    both_unavailable_count=0,
                    duplicate_count=0,
                    invalid_company_count=0,
                    stale_count=stale_count,
                ),
                "candidates": [],
            }

        self.last_selected_hierarchy = hierarchy
        universe, duplicate_count, invalid_company_count = self._load_company_universe(
            run_id, horizon, hierarchy
        )
        current_ids = {item["company_id"] for item in universe}
        stale_count = self._cleanup_stale_candidates(run_id, horizon, current_ids)

        warnings: List[str] = []
        if not universe:
            warnings.append(W_EMPTY)
        if invalid_company_count:
            warnings.append(W_SYMBOL_MISSING)
        if stale_count:
            warnings.append(W_STALE_REMOVED)

        candidates: List[Dict[str, Any]] = []
        counts = {
            "eligible": 0,
            "ineligible": 0,
            "technical_unavailable": 0,
            "fundamental_unavailable": 0,
            "both_unavailable": 0,
        }
        for item in universe:
            technical = self._technical_diagnostics(
                run_id, horizon, item["company_id"]
            )
            fundamental = self._fundamental_diagnostics(run_id, item["company_id"])
            candidate = self._candidate_result(item, hierarchy, technical, fundamental)
            self._persist_candidate(run_id, horizon, item, candidate)
            candidates.append(
                {
                    "company_id": item["company_id"],
                    "symbol": item["symbol"],
                    "status": candidate["status"],
                    "eligible": candidate["eligible"],
                }
            )
            if candidate["eligible"]:
                counts["eligible"] += 1
            else:
                counts["ineligible"] += 1
            if not technical["eligible"] and not fundamental["eligible"]:
                counts["both_unavailable"] += 1
            elif not technical["eligible"]:
                counts["technical_unavailable"] += 1
            elif not fundamental["eligible"]:
                counts["fundamental_unavailable"] += 1

        self._disc.commit()
        return {
            "warnings": sorted(set(warnings)),
            "metadata": self._metadata(
                horizon=horizon,
                hierarchy=hierarchy,
                company_count=len(universe),
                eligible_count=counts["eligible"],
                ineligible_count=counts["ineligible"],
                technical_unavailable_count=counts["technical_unavailable"],
                fundamental_unavailable_count=counts["fundamental_unavailable"],
                both_unavailable_count=counts["both_unavailable"],
                duplicate_count=duplicate_count,
                invalid_company_count=invalid_company_count,
                stale_count=stale_count,
            ),
            "candidates": candidates,
        }

    def _selected_hierarchy(self, run_id: str, horizon: str) -> Optional[Dict[str, str]]:
        rows = (
            self._disc.query(DiscoverySelection)
            .filter_by(
                run_id=run_id,
                horizon=horizon,
                entity_type=ENTITY_TYPE_BASIC_INDUSTRY,
                selected=True,
            )
            .order_by(DiscoverySelection.rank.asc(), DiscoverySelection.entity_name.asc())
            .all()
        )
        for row in rows:
            sector = (row.parent_sector or "").strip()
            industry = (row.parent_industry or "").strip()
            basic = (row.entity_name or "").strip()
            if sector and industry and basic:
                return {"sector": sector, "industry": industry, "basic_industry": basic}
        return None

    def _load_company_universe(
        self,
        run_id: str,
        horizon: str,
        hierarchy: Dict[str, str],
    ) -> Tuple[List[Dict[str, str]], int, int]:
        raw: List[Dict[str, str]] = []
        for row in (
            self._disc.query(CompanyTechnicalMetric)
            .filter_by(
                run_id=run_id,
                horizon=horizon,
                sector=hierarchy["sector"],
                industry=hierarchy["industry"],
                basic_industry=hierarchy["basic_industry"],
            )
            .all()
        ):
            raw.append(self._company_item(row.source_company_id, row.symbol, row.sector, row.industry, row.basic_industry))

        for row in (
            self._disc.query(CompanyFundamentalMetric)
            .filter_by(
                run_id=run_id,
                sector=hierarchy["sector"],
                industry=hierarchy["industry"],
                basic_industry=hierarchy["basic_industry"],
            )
            .all()
        ):
            raw.append(self._company_item(row.source_company_id, row.symbol, row.sector, row.industry, row.basic_industry))

        for row in (
            self._disc.query(EligibleUniverseSnapshot)
            .filter_by(
                run_id=run_id,
                horizon=horizon,
                sector=hierarchy["sector"],
                industry=hierarchy["industry"],
                basic_industry=hierarchy["basic_industry"],
            )
            .all()
        ):
            raw.append(self._company_item(row.source_company_id, row.symbol, row.sector, row.industry, row.basic_industry))

        invalid_count = 0
        duplicate_count = 0
        by_company: Dict[str, Dict[str, str]] = {}
        for item in raw:
            if not item["company_id"] or not item["symbol"]:
                invalid_count += 1
                continue
            existing = by_company.get(item["company_id"])
            if existing is not None:
                duplicate_count += 1
                if item["symbol"] < existing["symbol"]:
                    by_company[item["company_id"]] = item
                continue
            by_company[item["company_id"]] = item

        return (
            sorted(by_company.values(), key=lambda item: (item["symbol"], item["company_id"])),
            duplicate_count,
            invalid_count,
        )

    def _company_item(
        self,
        company_id: Optional[str],
        symbol: Optional[str],
        sector: Optional[str],
        industry: Optional[str],
        basic_industry: Optional[str],
    ) -> Dict[str, str]:
        return {
            "company_id": (company_id or "").strip(),
            "symbol": (symbol or "").strip(),
            "sector": (sector or "").strip(),
            "industry": (industry or "").strip(),
            "basic_industry": (basic_industry or "").strip(),
        }

    def _technical_record(self, run_id: str, horizon: str, company_id: str) -> Optional[CompanyTechnicalMetric]:
        return (
            self._disc.query(CompanyTechnicalMetric)
            .filter_by(run_id=run_id, horizon=horizon, source_company_id=company_id)
            .order_by(CompanyTechnicalMetric.created_at.desc(), CompanyTechnicalMetric.id.desc())
            .first()
        )

    def _fundamental_record(self, run_id: str, company_id: str) -> Optional[CompanyFundamentalMetric]:
        return (
            self._disc.query(CompanyFundamentalMetric)
            .filter_by(run_id=run_id, source_company_id=company_id)
            .order_by(CompanyFundamentalMetric.created_at.desc(), CompanyFundamentalMetric.id.desc())
            .first()
        )

    def _technical_diagnostics(self, run_id: str, horizon: str, company_id: str) -> Dict[str, Any]:
        row = self._technical_record(run_id, horizon, company_id)
        if row is None:
            return {
                "metric_id": None,
                "record_available": False,
                "coverage_pct": None,
                "eligible": False,
                "warnings": [W_TECHNICAL_METRIC_UNAVAILABLE],
            }
        coverage_pct = _coverage_pct(row.data_coverage)
        calc = row.calculation_details or {}
        eligible = bool(
            not _status_unavailable(calc)
            and row.return_available is True
            and bool(row.benchmark_candle_date)
            and coverage_pct is not None
            and coverage_pct >= TECHNICAL_MIN_COVERAGE
        )
        return {
            "metric_id": row.id,
            "record_available": True,
            "coverage_pct": coverage_pct,
            "eligible": eligible,
            "warnings": [] if eligible else [W_TECHNICAL_INELIGIBLE],
        }

    def _fundamental_diagnostics(self, run_id: str, company_id: str) -> Dict[str, Any]:
        row = self._fundamental_record(run_id, company_id)
        if row is None:
            return {
                "metric_id": None,
                "record_available": False,
                "coverage_pct": None,
                "eligible": False,
                "warnings": [W_FUNDAMENTAL_METRIC_UNAVAILABLE],
            }
        coverage_pct = _coverage_pct(row.data_coverage)
        eligible = bool(
            _is_finite(row.final_fundamental_score)
            and coverage_pct is not None
            and coverage_pct >= FUNDAMENTAL_MIN_COVERAGE
            and row.fundamental_eligible_for_selection is True
        )
        return {
            "metric_id": row.id,
            "record_available": True,
            "coverage_pct": coverage_pct,
            "eligible": eligible,
            "warnings": [] if eligible else [W_FUNDAMENTAL_INELIGIBLE],
        }

    def _candidate_result(
        self,
        item: Dict[str, str],
        hierarchy: Dict[str, str],
        technical: Dict[str, Any],
        fundamental: Dict[str, Any],
    ) -> Dict[str, Any]:
        technical_available = bool(technical["eligible"])
        fundamental_available = bool(fundamental["eligible"])
        eligible = technical_available and fundamental_available
        if eligible:
            status = STATUS_ELIGIBLE
        elif not technical_available and not fundamental_available:
            status = STATUS_BOTH_UNAVAILABLE
        elif not technical_available:
            status = STATUS_TECHNICAL_UNAVAILABLE
        else:
            status = STATUS_FUNDAMENTAL_UNAVAILABLE

        warnings = sorted(set(technical["warnings"] + fundamental["warnings"]))
        return {
            "technical_metric_id": technical["metric_id"],
            "fundamental_metric_id": fundamental["metric_id"],
            "technical_available": technical_available,
            "fundamental_available": fundamental_available,
            "eligible": eligible,
            "status": status,
            "warnings": warnings,
            "calculation_details": {
                "selected_hierarchy": copy.deepcopy(hierarchy),
                "technical": {
                    "record_available": technical["record_available"],
                    "coverage_pct": technical["coverage_pct"],
                    "eligible": technical_available,
                },
                "fundamental": {
                    "record_available": fundamental["record_available"],
                    "coverage_pct": fundamental["coverage_pct"],
                    "eligible": fundamental_available,
                },
                "candidate": {
                    "eligible": eligible,
                    "status": status,
                },
            },
        }

    def _persist_candidate(
        self,
        run_id: str,
        horizon: str,
        item: Dict[str, str],
        candidate: Dict[str, Any],
    ) -> None:
        row = (
            self._disc.query(StockCandidateSnapshot)
            .filter_by(run_id=run_id, horizon=horizon, company_id=item["company_id"])
            .first()
        )
        now = datetime.datetime.utcnow()
        if row is None:
            row = StockCandidateSnapshot(
                id=str(uuid.uuid4()),
                run_id=run_id,
                horizon=horizon,
                company_id=item["company_id"],
                created_at=now,
            )
            self._disc.add(row)

        row.symbol = item["symbol"]
        row.sector = item["sector"]
        row.industry = item["industry"]
        row.basic_industry = item["basic_industry"]
        row.technical_metric_id = candidate["technical_metric_id"]
        row.fundamental_metric_id = candidate["fundamental_metric_id"]
        row.technical_available = candidate["technical_available"]
        row.fundamental_available = candidate["fundamental_available"]
        row.eligible = candidate["eligible"]
        row.status = candidate["status"]
        row.warnings = candidate["warnings"]
        row.calculation_details = candidate["calculation_details"]
        row.updated_at = now

    def _cleanup_stale_candidates(self, run_id: str, horizon: str, current_company_ids: set[str]) -> int:
        rows = (
            self._disc.query(StockCandidateSnapshot)
            .filter_by(run_id=run_id, horizon=horizon)
            .all()
        )
        stale = [row for row in rows if row.company_id not in current_company_ids]
        for row in stale:
            self._disc.delete(row)
        return len(stale)

    def _metadata(
        self,
        horizon: str,
        hierarchy: Optional[Dict[str, str]],
        company_count: int,
        eligible_count: int,
        ineligible_count: int,
        technical_unavailable_count: int,
        fundamental_unavailable_count: int,
        both_unavailable_count: int,
        duplicate_count: int,
        invalid_company_count: int,
        stale_count: int,
    ) -> Dict[str, Any]:
        hierarchy = hierarchy or {}
        return {
            "horizon": horizon,
            "selected_sector": hierarchy.get("sector"),
            "selected_industry": hierarchy.get("industry"),
            "selected_basic_industry": hierarchy.get("basic_industry"),
            "company_count": company_count,
            "eligible_candidate_count": eligible_count,
            "ineligible_candidate_count": ineligible_count,
            "technical_unavailable_count": technical_unavailable_count,
            "fundamental_unavailable_count": fundamental_unavailable_count,
            "both_unavailable_count": both_unavailable_count,
            "duplicate_count": duplicate_count,
            "invalid_company_count": invalid_company_count,
            "stale_candidate_count": stale_count,
        }
