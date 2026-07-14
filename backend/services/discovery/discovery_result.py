"""Read-only discovery result assembly."""
from __future__ import annotations

import datetime
import re
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy.orm import Session

from models.discovery import (
    DiscoveryRun,
    DiscoverySelection,
    GroupScore,
    StockCandidateSnapshot,
)


HORIZONS: Tuple[str, str, str] = ("SHORT", "MID", "LONG")
ENTITY_SECTOR = "SECTOR"
ENTITY_INDUSTRY = "INDUSTRY"
ENTITY_BASIC_INDUSTRY = "BASIC_INDUSTRY"
ENTITY_STOCK = "STOCK"

W_RUN_NOT_FOUND = "DISCOVERY_RUN_NOT_FOUND"
W_GROUP_SCORE_UNAVAILABLE = "SELECTION_GROUP_SCORE_UNAVAILABLE"
W_HIERARCHY_MISMATCH = "SELECTION_HIERARCHY_MISMATCH"
W_STOCK_SNAPSHOT_UNAVAILABLE = "SELECTED_STOCK_SNAPSHOT_UNAVAILABLE"
W_DUPLICATE_SELECTION = "DUPLICATE_ACTIVE_SELECTION"

DISPLAY_STAGE_BY_HORIZON = {
    "SHORT": (
        "SECTOR_RANKING",
        "INDUSTRY_RANKING",
        "BASIC_INDUSTRY_RANKING",
        "STOCK_CANDIDATE_UNIVERSE",
        "STOCK_CANDIDATE_SCORE",
        "STOCK_RANKING",
    ),
    "MID": (
        "SECTOR_RANKING",
        "INDUSTRY_RANKING",
        "BASIC_INDUSTRY_RANKING",
        "STOCK_CANDIDATE_UNIVERSE",
        "STOCK_CANDIDATE_SCORE",
        "STOCK_RANKING",
    ),
    "LONG": (
        "SECTOR_RANKING",
        "INDUSTRY_RANKING",
        "BASIC_INDUSTRY_RANKING",
        "STOCK_CANDIDATE_UNIVERSE",
        "STOCK_CANDIDATE_SCORE",
        "STOCK_RANKING",
    ),
}


class DiscoveryResultService:
    def __init__(self, discovery_session: Session):
        self._disc = discovery_session

    def get_result(self, run_id: str) -> Dict[str, Any]:
        run = self._disc.query(DiscoveryRun).filter_by(id=run_id).first()
        if run is None:
            return {
                "run_id": run_id,
                "status": None,
                "current_stage": None,
                "last_completed_stage": None,
                "started_at": None,
                "completed_at": None,
                "resume_count": 0,
                "warnings": [W_RUN_NOT_FOUND],
                "error": {
                    "code": W_RUN_NOT_FOUND,
                    "message": "Discovery run not found.",
                },
                "stage_results": {},
                "horizons": {horizon: self._empty_horizon("PENDING", [W_RUN_NOT_FOUND]) for horizon in HORIZONS},
            }

        warnings: List[str] = list(run.warnings or [])
        selections_by_horizon = self._load_active_selections(run_id)
        group_scores = self._load_group_scores(run_id, selections_by_horizon)
        stock_snapshots = self._load_stock_snapshots(run_id, selections_by_horizon)

        horizons: Dict[str, Dict[str, Any]] = {}
        for horizon in HORIZONS:
            horizon_result, horizon_warnings = self._build_horizon(
                run,
                horizon,
                selections_by_horizon.get(horizon, {}),
                group_scores,
                stock_snapshots,
            )
            horizons[horizon] = horizon_result
            warnings.extend(horizon_warnings)

        error = None
        if run.error_code or run.error_message:
            error = {
                "code": run.error_code,
                "message": _safe_message(run.error_message),
            }

        return {
            "run_id": run.id,
            "status": run.status,
            "current_stage": run.current_stage,
            "last_completed_stage": run.last_completed_stage,
            "started_at": _format_dt(run.started_at),
            "completed_at": _format_dt(run.completed_at),
            "resume_count": run.resume_count or 0,
            "warnings": _clean_warnings(warnings),
            "error": error,
            "stage_results": _sanitize_stage_results(run.stage_results or {}),
            "horizons": horizons,
        }

    def _load_active_selections(
        self,
        run_id: str,
    ) -> Dict[str, Dict[str, List[DiscoverySelection]]]:
        rows = (
            self._disc.query(DiscoverySelection)
            .filter(
                DiscoverySelection.run_id == run_id,
                DiscoverySelection.selected.is_(True),
            )
            .order_by(
                DiscoverySelection.horizon.asc(),
                DiscoverySelection.entity_type.asc(),
                DiscoverySelection.rank.asc(),
                DiscoverySelection.symbol.asc(),
                DiscoverySelection.entity_name.asc(),
            )
            .all()
        )
        by_horizon: Dict[str, Dict[str, List[DiscoverySelection]]] = {
            horizon: defaultdict(list) for horizon in HORIZONS
        }
        for row in rows:
            if row.horizon in by_horizon:
                by_horizon[row.horizon][row.entity_type].append(row)
        return by_horizon

    def _load_group_scores(
        self,
        run_id: str,
        selections_by_horizon: Dict[str, Dict[str, List[DiscoverySelection]]],
    ) -> Dict[Tuple[str, str, str, str, str], GroupScore]:
        keys = set()
        for horizon, selections in selections_by_horizon.items():
            for entity_type in (ENTITY_SECTOR, ENTITY_INDUSTRY, ENTITY_BASIC_INDUSTRY):
                for row in selections.get(entity_type, []):
                    keys.add((
                        horizon,
                        entity_type,
                        row.entity_name,
                        row.parent_sector or "",
                        row.parent_industry or "",
                    ))
        if not keys:
            return {}

        rows = (
            self._disc.query(GroupScore)
            .filter(
                GroupScore.run_id == run_id,
                GroupScore.horizon.in_({key[0] for key in keys}),
                GroupScore.entity_type.in_({key[1] for key in keys}),
            )
            .all()
        )
        return {
            (
                row.horizon,
                row.entity_type,
                row.entity_name,
                row.parent_sector or "",
                row.parent_industry or "",
            ): row
            for row in rows
        }

    def _load_stock_snapshots(
        self,
        run_id: str,
        selections_by_horizon: Dict[str, Dict[str, List[DiscoverySelection]]],
    ) -> Dict[Tuple[str, str], StockCandidateSnapshot]:
        company_ids = {
            row.company_id
            for selections in selections_by_horizon.values()
            for row in selections.get(ENTITY_STOCK, [])
            if row.company_id
        }
        if not company_ids:
            return {}
        rows = (
            self._disc.query(StockCandidateSnapshot)
            .filter(
                StockCandidateSnapshot.run_id == run_id,
                StockCandidateSnapshot.company_id.in_(company_ids),
            )
            .all()
        )
        return {(row.horizon, row.company_id): row for row in rows}

    def _build_horizon(
        self,
        run: DiscoveryRun,
        horizon: str,
        selections: Dict[str, List[DiscoverySelection]],
        group_scores: Dict[Tuple[str, str, str, str, str], GroupScore],
        stock_snapshots: Dict[Tuple[str, str], StockCandidateSnapshot],
    ) -> Tuple[Dict[str, Any], List[str]]:
        warnings: List[str] = []
        status = self._horizon_status(run.stage_results or {}, horizon)

        sector_selection, sector_warnings = self._single_selection(
            selections.get(ENTITY_SECTOR, [])
        )
        warnings.extend(sector_warnings)
        sector = None
        if sector_selection is not None:
            sector = self._group_payload(
                horizon,
                ENTITY_SECTOR,
                sector_selection,
                group_scores,
                warnings,
            )

        industry_selection, industry_warnings = self._single_selection(
            selections.get(ENTITY_INDUSTRY, [])
        )
        warnings.extend(industry_warnings)
        industry = None
        if industry_selection is not None and sector_selection is not None:
            if (industry_selection.parent_sector or "") != sector_selection.entity_name:
                warnings.append(W_HIERARCHY_MISMATCH)
            else:
                industry = self._group_payload(
                    horizon,
                    ENTITY_INDUSTRY,
                    industry_selection,
                    group_scores,
                    warnings,
                )
        elif industry_selection is not None:
            warnings.append(W_HIERARCHY_MISMATCH)

        basic_selection, basic_warnings = self._single_selection(
            selections.get(ENTITY_BASIC_INDUSTRY, [])
        )
        warnings.extend(basic_warnings)
        basic_industry = None
        if basic_selection is not None and sector_selection is not None and industry_selection is not None and industry is not None:
            if (
                (basic_selection.parent_sector or "") != sector_selection.entity_name
                or (basic_selection.parent_industry or "") != industry_selection.entity_name
            ):
                warnings.append(W_HIERARCHY_MISMATCH)
            else:
                basic_industry = self._group_payload(
                    horizon,
                    ENTITY_BASIC_INDUSTRY,
                    basic_selection,
                    group_scores,
                    warnings,
                )
        elif basic_selection is not None:
            warnings.append(W_HIERARCHY_MISMATCH)

        stocks: List[Dict[str, Any]] = []
        if sector_selection is not None and industry is not None and basic_industry is not None:
            stock_rows = sorted(
                selections.get(ENTITY_STOCK, []),
                key=lambda row: (
                    row.rank if row.rank is not None else 10**9,
                    row.symbol or row.entity_name or "",
                ),
            )
            for stock_selection in stock_rows:
                if not self._stock_matches(stock_selection, sector_selection, industry_selection, basic_selection):
                    warnings.append(W_HIERARCHY_MISMATCH)
                    continue
                snapshot = stock_snapshots.get((horizon, stock_selection.company_id or ""))
                if snapshot is None:
                    warnings.append(W_STOCK_SNAPSHOT_UNAVAILABLE)
                    continue
                stocks.append(self._stock_payload(stock_selection, snapshot))
        elif selections.get(ENTITY_STOCK):
            warnings.append(W_HIERARCHY_MISMATCH)

        return (
            {
                "status": status,
                "sector": sector,
                "industry": industry,
                "basic_industry": basic_industry,
                "stocks": stocks,
                "warnings": _clean_warnings(warnings),
            },
            warnings,
        )

    def _single_selection(
        self,
        rows: List[DiscoverySelection],
    ) -> Tuple[Optional[DiscoverySelection], List[str]]:
        if not rows:
            return None, []
        warnings = [W_DUPLICATE_SELECTION] if len(rows) > 1 else []
        ordered = sorted(
            rows,
            key=lambda row: (
                row.rank if row.rank is not None else 10**9,
                row.entity_name or "",
                row.id or "",
            ),
        )
        return ordered[0], warnings

    def _group_payload(
        self,
        horizon: str,
        entity_type: str,
        selection: DiscoverySelection,
        group_scores: Dict[Tuple[str, str, str, str, str], GroupScore],
        warnings: List[str],
    ) -> Optional[Dict[str, Any]]:
        key = (
            horizon,
            entity_type,
            selection.entity_name,
            selection.parent_sector or "",
            selection.parent_industry or "",
        )
        group = group_scores.get(key)
        if group is None:
            warnings.append(W_GROUP_SCORE_UNAVAILABLE)
            return None

        details = _final_discovery_details(entity_type, group.calculation_details)
        payload = {
            "name": group.entity_name,
            "rank": group.rank,
            "final_score": group.final_score,
            "technical_score": group.technical_score,
            "fundamental_score": group.fundamental_score,
            "macro_score": group.macro_score,
            "status": details.get("status"),
            "coverage_pct": details.get("coverage_pct"),
            "warnings": list(group.warnings or []),
        }
        if entity_type in {ENTITY_INDUSTRY, ENTITY_BASIC_INDUSTRY}:
            payload["parent_sector"] = group.parent_sector or ""
        if entity_type == ENTITY_BASIC_INDUSTRY:
            payload["parent_industry"] = group.parent_industry or ""
        return payload

    def _stock_matches(
        self,
        stock: DiscoverySelection,
        sector: DiscoverySelection,
        industry: DiscoverySelection,
        basic: DiscoverySelection,
    ) -> bool:
        return (
            (stock.parent_sector or "") == sector.entity_name
            and (stock.parent_industry or "") == industry.entity_name
            and (stock.basic_industry or "") == basic.entity_name
        )

    def _stock_payload(
        self,
        selection: DiscoverySelection,
        snapshot: StockCandidateSnapshot,
    ) -> Dict[str, Any]:
        return {
            "company_id": snapshot.company_id,
            "symbol": snapshot.symbol,
            "rank": snapshot.rank,
            "selected": bool(snapshot.selected),
            "final_score": snapshot.final_score,
            "technical_score": snapshot.technical_score,
            "fundamental_score": snapshot.fundamental_score,
            "inherited_macro_score": snapshot.inherited_macro_score,
            "score_status": snapshot.score_status,
            "score_coverage_pct": snapshot.score_coverage_pct,
            "warnings": _clean_warnings((snapshot.warnings or []) + (snapshot.score_warnings or [])),
        }

    def _horizon_status(self, stage_results: Dict[str, Any], horizon: str) -> str:
        status = "PENDING"
        saw_failed = False
        for stage in DISPLAY_STAGE_BY_HORIZON[horizon]:
            result = stage_results.get(stage) or {}
            horizon_result = (result.get("horizons") or {}).get(horizon)
            candidate = None
            if isinstance(horizon_result, dict):
                candidate = horizon_result.get("status")
            elif isinstance(result, dict):
                candidate = result.get("status")
            if candidate == "FAILED":
                saw_failed = True
            if candidate:
                status = candidate
        if saw_failed:
            return "FAILED"
        return status

    def _empty_horizon(self, status: str, warnings: List[str]) -> Dict[str, Any]:
        return {
            "status": status,
            "sector": None,
            "industry": None,
            "basic_industry": None,
            "stocks": [],
            "warnings": _clean_warnings(warnings),
        }


def _final_discovery_details(entity_type: str, calculation_details: Any) -> Dict[str, Any]:
    if not isinstance(calculation_details, dict):
        return {}
    discovery = calculation_details.get("discovery")
    if not isinstance(discovery, dict):
        return {}
    key = {
        ENTITY_SECTOR: "final_sector_score",
        ENTITY_INDUSTRY: "final_industry_score",
        ENTITY_BASIC_INDUSTRY: "final_basic_industry_score",
    }.get(entity_type)
    details = discovery.get(key)
    return details if isinstance(details, dict) else {}


def _format_dt(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.replace(microsecond=0).isoformat() + "Z"
    return str(value)


def _clean_warnings(warnings: Iterable[Any]) -> List[str]:
    return sorted({str(warning) for warning in warnings if warning})


def _safe_message(value: Any) -> Optional[str]:
    if value is None:
        return None
    text_value = str(value)
    first_line = text_value.splitlines()[0] if text_value.splitlines() else text_value
    first_line = re.sub(r"(?i)(authorization|api[-_ ]?key|token|secret)\s*[:=]\s*(?:bearer\s+)?\S+", r"\1: [REDACTED]", first_line)
    first_line = re.sub(r"(?i)bearer\s+[a-z0-9._\-]+", "Bearer [REDACTED]", first_line)
    return first_line[:300]


def _sanitize_stage_results(value: Any) -> Any:
    blocked_keys = {
        "api_key",
        "authorization",
        "headers",
        "prompt",
        "raw_prompt",
        "raw_provider_response",
        "raw_response",
        "stack_trace",
        "traceback",
    }
    if isinstance(value, dict):
        clean: Dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized in blocked_keys or "secret" in normalized or "token" in normalized:
                continue
            if normalized in {"error_message", "message"}:
                clean[key] = _safe_message(item)
            else:
                clean[key] = _sanitize_stage_results(item)
        return clean
    if isinstance(value, list):
        return [_sanitize_stage_results(item) for item in value]
    if isinstance(value, str):
        return _safe_message(value)
    return value
