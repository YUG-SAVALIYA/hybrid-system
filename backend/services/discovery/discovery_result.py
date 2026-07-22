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
    EligibleUniverseSnapshot,
    CompanyTechnicalMetric,
    CompanyFundamentalMetric,
    MacroEntityImpact,
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
        "SECTOR_SELECTION",
        "INDUSTRY_SELECTION",
        "BASIC_INDUSTRY_SELECTION",
        "STOCK_SELECTION",
    ),
    "MID": (
        "SECTOR_SELECTION",
        "INDUSTRY_SELECTION",
        "BASIC_INDUSTRY_SELECTION",
        "STOCK_SELECTION",
    ),
    "LONG": (
        "SECTOR_SELECTION",
        "INDUSTRY_SELECTION",
        "BASIC_INDUSTRY_SELECTION",
        "STOCK_SELECTION",
    ),
}

LEGACY_DISPLAY_STAGE_BY_HORIZON = {
    "SHORT": (
        "SECTOR_RANKING",
        "INDUSTRY_RANKING",
        "BASIC_INDUSTRY_RANKING",
        "STOCK_RANKING",
    ),
    "MID": (
        "SECTOR_RANKING",
        "INDUSTRY_RANKING",
        "BASIC_INDUSTRY_RANKING",
        "STOCK_RANKING",
    ),
    "LONG": (
        "SECTOR_RANKING",
        "INDUSTRY_RANKING",
        "BASIC_INDUSTRY_RANKING",
        "STOCK_RANKING",
    ),
}


class DiscoveryResultService:
    def __init__(self, discovery_session: Session):
        self._disc = discovery_session

    def get_recent_runs_summary(self, limit: int = 5) -> List[Dict[str, Any]]:
        runs = (
            self._disc.query(DiscoveryRun)
            .filter(DiscoveryRun.status != "PENDING")
            .order_by(DiscoveryRun.run_date.desc(), DiscoveryRun.started_at.desc())
            .limit(limit)
            .all()
        )
        if not runs:
            return []

        # We need top 3 selections per entity_type for these runs.
        run_ids = [run.id for run in runs]
        
        # Load selections
        selections = (
            self._disc.query(DiscoverySelection)
            .filter(
                DiscoverySelection.run_id.in_(run_ids),
                DiscoverySelection.selected == True,
                DiscoverySelection.horizon == "SHORT", # Assuming SHORT for dashboard
            )
            .all()
        )
        
        # Group by run_id
        summary_by_run = {}
        for run in runs:
            summary_by_run[run.id] = {
                "run_id": run.id,
                "status": run.status or "UNKNOWN",
                "horizon": run.horizon or "SHORT",
                "run_date": run.run_date,
                "started_at": _format_dt(run.started_at),
                "completed_at": _format_dt(run.completed_at),
                "top_sectors": [],
                "top_industries": [],
                "top_basic_industries": [],
                "top_stocks": [],
            }
            
        for sel in selections:
            run_id = sel.run_id
            if run_id not in summary_by_run:
                continue
            
            # Sort into the correct bucket if rank is <= 3
            if (sel.rank or 9999) <= 3:
                item = {
                    "name": sel.entity_name or sel.symbol,
                    "rank": sel.rank,
                    "final_score": None, # Could join with scores, but let's keep it simple or just leave null
                }
                if sel.entity_type == ENTITY_SECTOR:
                    summary_by_run[run_id]["top_sectors"].append(item)
                elif sel.entity_type == ENTITY_INDUSTRY:
                    summary_by_run[run_id]["top_industries"].append(item)
                elif sel.entity_type == ENTITY_BASIC_INDUSTRY:
                    summary_by_run[run_id]["top_basic_industries"].append(item)
                elif sel.entity_type == ENTITY_STOCK:
                    summary_by_run[run_id]["top_stocks"].append(item)

        # Sort the items in each run by rank
        for run_id in summary_by_run:
            for key in ["top_sectors", "top_industries", "top_basic_industries", "top_stocks"]:
                summary_by_run[run_id][key].sort(key=lambda x: x["rank"] or 9999)

        return [summary_by_run[run.id] for run in runs]

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

        sector_selections = selections.get(ENTITY_SECTOR, [])
        sectors = []
        for sel in sector_selections:
            payload = self._group_payload(horizon, ENTITY_SECTOR, sel, group_scores, warnings)
            if payload:
                sectors.append(payload)

        industry_selections = selections.get(ENTITY_INDUSTRY, [])
        industries = []
        for sel in industry_selections:
            if any((sel.parent_sector or "") == s_sel.entity_name and s_sel.selected for s_sel in sector_selections):
                payload = self._group_payload(horizon, ENTITY_INDUSTRY, sel, group_scores, warnings)
                if payload:
                    industries.append(payload)
            else:
                warnings.append(W_HIERARCHY_MISMATCH)

        basic_selections = selections.get(ENTITY_BASIC_INDUSTRY, [])
        basic_industries = []
        for sel in basic_selections:
            if any((sel.parent_industry or "") == i_sel.entity_name and i_sel.selected for i_sel in industry_selections):
                payload = self._group_payload(horizon, ENTITY_BASIC_INDUSTRY, sel, group_scores, warnings)
                if payload:
                    basic_industries.append(payload)
            else:
                warnings.append(W_HIERARCHY_MISMATCH)

        stocks: List[Dict[str, Any]] = []
        stock_rows = sorted(
            selections.get(ENTITY_STOCK, []),
            key=lambda row: (
                row.rank if row.rank is not None else 10**9,
                row.symbol or row.entity_name or "",
            ),
        )
        for stock_selection in stock_rows:
            # Note: We just check if it matches ANY selected basic industry
            if any((stock_selection.basic_industry or "") == b_sel.entity_name and b_sel.selected for b_sel in basic_selections):
                snapshot = stock_snapshots.get((horizon, stock_selection.company_id or ""))
                if snapshot is None:
                    warnings.append(W_STOCK_SNAPSHOT_UNAVAILABLE)
                    continue
                stocks.append(self._stock_payload(stock_selection, snapshot))
            else:
                warnings.append(W_HIERARCHY_MISMATCH)

        return (
            {
                "status": status,
                "sectors": sectors,
                "industries": industries,
                "basic_industries": basic_industries,
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
        calc = group.calculation_details or {}
        payload = {
            "name": group.entity_name,
            "rank": group.rank,
            "selected": selection.selected,
            "final_score": group.final_score,
            "technical_score": group.technical_score,
            "fundamental_score": group.fundamental_score,
            "macro_score": group.macro_score,
            "status": details.get("status"),
            "coverage_pct": details.get("coverage_pct"),
            "warnings": list(group.warnings or []),
            "tech_details": {
                "median_relative_return": calc.get("median_relative_return"),
                "outperformance_breadth": calc.get("outperformance_breadth"),
                "percent_consistency_gte_60": calc.get("percent_consistency_gte_60"),
                "positive_return_breadth": calc.get("positive_return_breadth"),
                "scores": {
                    "return_score": getattr(group, "technical_return_score", None),
                    "breadth": getattr(group, "technical_breadth_score", None),
                    "volume": getattr(group, "technical_volume_score", None),
                    "consistency": getattr(group, "technical_consistency_score", None)
                }
            },
            "fund_details": {
                "pillar_scores": calc.get("fundamental", {}).get("pillar_scores", {}),
                "metrics": calc.get("fundamental", {}).get("raw_aggregation", {}).get("metrics", {})
            },
            "macro_details": calc.get("macro", {}).get("sector_score", {}) if entity_type == ENTITY_SECTOR else (calc.get("macro", {}).get("industry_score", {}) if entity_type == ENTITY_INDUSTRY else calc.get("macro", {}).get("basic_industry_score", {})),
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
        stages = DISPLAY_STAGE_BY_HORIZON[horizon]
        legacy_stages = LEGACY_DISPLAY_STAGE_BY_HORIZON[horizon]
        for stage in stages:
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
        if status == "PENDING":
            for stage in legacy_stages:
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

    def get_group_constituents(self, run_id: str, horizon: str, entity_type: str, entity_name: str, parent_sector: str = "", parent_industry: str = "") -> List[Dict[str, Any]]:
        """Fetch all constituent companies for a specific group (Sector, Industry, Basic Industry)."""
        query = self._disc.query(
            EligibleUniverseSnapshot,
            CompanyTechnicalMetric,
            CompanyFundamentalMetric,
            StockCandidateSnapshot
        ).join(
            CompanyTechnicalMetric,
            (EligibleUniverseSnapshot.source_company_id == CompanyTechnicalMetric.source_company_id) &
            (EligibleUniverseSnapshot.run_id == CompanyTechnicalMetric.run_id) &
            (CompanyTechnicalMetric.horizon == horizon),
            isouter=True
        ).join(
            CompanyFundamentalMetric,
            (EligibleUniverseSnapshot.source_company_id == CompanyFundamentalMetric.source_company_id) &
            (EligibleUniverseSnapshot.run_id == CompanyFundamentalMetric.run_id),
            isouter=True
        ).join(
            StockCandidateSnapshot,
            (EligibleUniverseSnapshot.symbol == StockCandidateSnapshot.symbol) &
            (EligibleUniverseSnapshot.run_id == StockCandidateSnapshot.run_id) &
            (StockCandidateSnapshot.horizon == horizon),
            isouter=True
        ).filter(
            EligibleUniverseSnapshot.run_id == run_id,
            EligibleUniverseSnapshot.horizon == horizon
        )

        if entity_type == ENTITY_SECTOR:
            query = query.filter(EligibleUniverseSnapshot.sector == entity_name)
        elif entity_type == ENTITY_INDUSTRY:
            query = query.filter(EligibleUniverseSnapshot.industry == entity_name)
            if parent_sector:
                query = query.filter(EligibleUniverseSnapshot.sector == parent_sector)
        elif entity_type == ENTITY_BASIC_INDUSTRY:
            query = query.filter(EligibleUniverseSnapshot.basic_industry == entity_name)
            if parent_sector:
                query = query.filter(EligibleUniverseSnapshot.sector == parent_sector)
            if parent_industry:
                query = query.filter(EligibleUniverseSnapshot.industry == parent_industry)
        elif entity_type == ENTITY_STOCK:
            query = query.filter(EligibleUniverseSnapshot.symbol == entity_name)
        else:
            return []

        results = query.all()
        constituents = []
        macro_cache = {}
        for uni, tech, fund, stock_cand in results:
            macro_impact = None
            
            hierarchy = []
            if stock_cand and stock_cand.score_details:
                macro_comp = stock_cand.score_details.get("components", {}).get("macro", {})
                st = macro_comp.get("source_entity_type")
                sn = macro_comp.get("source_entity_name")
                if st and sn:
                    hierarchy.append((st, sn))
                    
            if uni.basic_industry:
                hierarchy.append((ENTITY_BASIC_INDUSTRY, uni.basic_industry))
            if uni.industry:
                hierarchy.append((ENTITY_INDUSTRY, uni.industry))
            if uni.sector:
                hierarchy.append((ENTITY_SECTOR, uni.sector))
                
            # Remove duplicates while preserving order
            seen = set()
            unique_hierarchy = []
            for item in hierarchy:
                if item not in seen:
                    seen.add(item)
                    unique_hierarchy.append(item)

            for h_type, h_name in unique_hierarchy:
                cache_key = (h_type, h_name)
                if cache_key not in macro_cache:
                    impact_q = self._disc.query(MacroEntityImpact).filter_by(
                        run_id=run_id,
                        horizon=horizon,
                        entity_type=h_type,
                        entity_name=h_name
                    )
                    if h_type == ENTITY_INDUSTRY:
                        impact_q = impact_q.filter_by(parent_sector=uni.sector)
                    elif h_type == ENTITY_BASIC_INDUSTRY:
                        impact_q = impact_q.filter_by(parent_sector=uni.sector, parent_industry=uni.industry)
                    
                    impact = impact_q.first()
                    if impact:
                        macro_cache[cache_key] = {
                            "category_impacts": impact.category_impacts,
                            "overall_impact": impact.overall_impact,
                            "reason": impact.reason,
                        }
                    else:
                        macro_cache[cache_key] = None
                        
                macro_impact = macro_cache[cache_key]
                if macro_impact:
                    break

            constituents.append({
                "symbol": uni.symbol,
                "name": uni.source_company_id, # Can map to name if available, but usually symbol is used
                "sector": uni.sector,
                "industry": uni.industry,
                "basic_industry": uni.basic_industry,
                "technical_score": tech.final_technical_score if tech else None,
                "technical_status": tech.technical_status if tech else None,
                "company_return": tech.company_return if tech else None,
                "benchmark_return": tech.benchmark_return if tech else None,
                "tech_details": tech.calculation_details if tech else None,
                "fundamental_score": fund.final_fundamental_score if fund else None,
                "fundamental_status": fund.fundamental_status if fund else None,
                "fund_details": fund.calculation_details if fund else None,
                "inherited_macro_score": stock_cand.inherited_macro_score if stock_cand else None,
                "macro_impact": macro_impact,
                "market_cap": uni.market_cap
            })

        # Sort by market cap descending or symbol
        constituents.sort(key=lambda x: (x["market_cap"] or 0), reverse=True)
        return constituents



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
