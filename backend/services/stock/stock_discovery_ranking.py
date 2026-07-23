"""Deterministic stock ranking and final selection."""
from __future__ import annotations

import copy
import datetime
import math
import uuid
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

import config
from models.discovery import DiscoverySelection, StockCandidateSnapshot


ENTITY_TYPE_SECTOR = "SECTOR"
ENTITY_TYPE_INDUSTRY = "INDUSTRY"
ENTITY_TYPE_BASIC_INDUSTRY = "BASIC_INDUSTRY"
ENTITY_TYPE_STOCK = "STOCK"
SELECTION_REASON = "Top deterministic stock candidate for the selected hierarchy and horizon."
STOCK_SELECTION_COUNT = int(getattr(config, "STOCK_SELECTION_COUNT", 5))

W_SELECTED_UNAVAILABLE = "SELECTED_BASIC_INDUSTRY_UNAVAILABLE"
W_NO_ELIGIBLE = "NO_ELIGIBLE_STOCK"
W_RANK_STALE = "STOCK_RANK_STALE"
W_STALE_SELECTION_REMOVED = "STALE_STOCK_SELECTION_REMOVED"
W_HIERARCHY_MISMATCH = "STOCK_SELECTION_HIERARCHY_MISMATCH"


def _is_finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def _sort_score(value: Any) -> float:
    return float(value) if _is_finite(value) else float("-inf")


class StockDiscoveryRankingService:
    def __init__(self, discovery_session: Session):
        self._disc = discovery_session

    def rank_and_select(self, run_id: str, horizon: str) -> Dict[str, Any]:
        hierarchy = self._selected_hierarchy(run_id, horizon)
        candidates = (
            self._disc.query(StockCandidateSnapshot)
            .filter_by(run_id=run_id, horizon=horizon)
            .order_by(StockCandidateSnapshot.symbol.asc(), StockCandidateSnapshot.company_id.asc())
            .all()
        )

        if hierarchy is None:
            stale_rank_count = self._clear_candidate_ranks(candidates)
            stale_selection_count = self._deactivate_stock_selections(run_id, horizon, set())
            self._disc.commit()
            warnings = [W_SELECTED_UNAVAILABLE]
            if stale_rank_count:
                warnings.append(W_RANK_STALE)
            if stale_selection_count:
                warnings.append(W_STALE_SELECTION_REMOVED)
            return {
                "warnings": sorted(set(warnings)),
                "metadata": self._metadata(
                    horizon,
                    None,
                    candidates,
                    [],
                    [],
                    stale_rank_count,
                    stale_selection_count,
                    0,
                ),
                "ranked_candidates": [],
                "selected_symbols": [],
            }

        matched: List[StockCandidateSnapshot] = []
        mismatched: List[StockCandidateSnapshot] = []
        for candidate in candidates:
            if self._matches_hierarchy(candidate, hierarchy):
                matched.append(candidate)
            else:
                mismatched.append(candidate)

        stale_rank_count = self._clear_candidate_ranks(mismatched)
        hierarchy_mismatch_count = len(
            [
                candidate for candidate in candidates
                if not self._matches_hierarchy(candidate, hierarchy)
            ]
        )

        eligible = [
            candidate for candidate in matched
            if _is_finite(candidate.final_score)
        ]
        if not eligible and matched:
            eligible = list(matched)
        eligible.sort(
            key=lambda candidate: (
                -_sort_score(candidate.final_score),
                -_sort_score(candidate.technical_score),
                -_sort_score(candidate.fundamental_score),
                candidate.symbol or "",
                candidate.company_id or "",
            )
        )

        for rank, candidate in enumerate(eligible, start=1):
            candidate.rank = rank

        eligible_ids = {candidate.id for candidate in eligible}
        stale_rank_count += self._clear_candidate_ranks(
            [candidate for candidate in matched if candidate.id not in eligible_ids]
        )

        selected = eligible[:STOCK_SELECTION_COUNT]
        selected_ids = {candidate.id for candidate in selected}
        now = datetime.datetime.utcnow()
        for candidate in eligible:
            candidate.selected = candidate.id in selected_ids
            candidate.selection_reason = SELECTION_REASON if candidate.selected else None
            candidate.selected_at = now if candidate.selected else None

        stale_selection_count = self._clear_unselected_candidates(
            [candidate for candidate in candidates if candidate.id not in selected_ids]
        )
        selected_keys = {
            (candidate.company_id, candidate.symbol, candidate.sector, candidate.industry, candidate.basic_industry)
            for candidate in selected
        }
        stale_selection_count += self._deactivate_stock_selections(
            run_id, horizon, selected_keys
        )
        self._persist_stock_selections(run_id, horizon, hierarchy, selected)
        self._disc.commit()

        warnings: List[str] = []
        if not eligible:
            warnings.append(W_NO_ELIGIBLE)
        if stale_rank_count:
            warnings.append(W_RANK_STALE)
        if stale_selection_count:
            warnings.append(W_STALE_SELECTION_REMOVED)
        if hierarchy_mismatch_count:
            warnings.append(W_HIERARCHY_MISMATCH)

        return {
            "warnings": sorted(set(warnings)),
            "metadata": self._metadata(
                horizon,
                hierarchy,
                candidates,
                eligible,
                selected,
                stale_rank_count,
                stale_selection_count,
                hierarchy_mismatch_count,
            ),
            "ranked_candidates": [
                {
                    "company_id": candidate.company_id,
                    "symbol": candidate.symbol,
                    "rank": candidate.rank,
                    "final_score": candidate.final_score,
                }
                for candidate in eligible
            ],
            "selected_symbols": [candidate.symbol for candidate in selected],
        }

    def _selected_hierarchy(self, run_id: str, horizon: str) -> Optional[Dict[str, Any]]:
        # 1. Try Basic Industry
        rows = (
            self._disc.query(DiscoverySelection)
            .filter_by(run_id=run_id, horizon=horizon, entity_type=ENTITY_TYPE_BASIC_INDUSTRY, selected=True)
            .order_by(DiscoverySelection.rank.asc(), DiscoverySelection.entity_name.asc())
            .all()
        )
        for row in rows:
            sector = (row.parent_sector or "").strip()
            industry = (row.parent_industry or "").strip()
            basic = (row.entity_name or "").strip()
            if sector and industry and basic:
                return {"sector": sector, "industry": industry, "basic_industry": basic}

        # 2. Fall back to Industry
        rows = (
            self._disc.query(DiscoverySelection)
            .filter_by(run_id=run_id, horizon=horizon, entity_type=ENTITY_TYPE_INDUSTRY, selected=True)
            .order_by(DiscoverySelection.rank.asc(), DiscoverySelection.entity_name.asc())
            .all()
        )
        for row in rows:
            sector = (row.parent_sector or "").strip()
            industry = (row.entity_name or "").strip()
            if sector and industry:
                return {"sector": sector, "industry": industry, "basic_industry": None}

        # 3. Fall back to Sector
        rows = (
            self._disc.query(DiscoverySelection)
            .filter_by(run_id=run_id, horizon=horizon, entity_type=ENTITY_TYPE_SECTOR, selected=True)
            .order_by(DiscoverySelection.rank.asc(), DiscoverySelection.entity_name.asc())
            .all()
        )
        for row in rows:
            sector = (row.entity_name or "").strip()
            if sector:
                return {"sector": sector, "industry": None, "basic_industry": None}

        return None

    def _matches_hierarchy(
        self,
        candidate: StockCandidateSnapshot,
        hierarchy: Dict[str, Any],
    ) -> bool:
        if candidate.sector != hierarchy["sector"]:
            return False
        if hierarchy.get("industry") and candidate.industry != hierarchy["industry"]:
            return False
        if hierarchy.get("basic_industry") and candidate.basic_industry != hierarchy["basic_industry"]:
            return False
        return True

    def _clear_candidate_ranks(self, candidates: List[StockCandidateSnapshot]) -> int:
        count = 0
        for candidate in candidates:
            had_stale = (
                candidate.rank is not None
                or candidate.selected is True
                or candidate.selection_reason is not None
                or candidate.selected_at is not None
            )
            if had_stale:
                count += 1
            candidate.rank = None
            candidate.selected = False
            candidate.selection_reason = None
            candidate.selected_at = None
        return count

    def _clear_unselected_candidates(self, candidates: List[StockCandidateSnapshot]) -> int:
        count = 0
        for candidate in candidates:
            if candidate.selected is True or candidate.selection_reason is not None or candidate.selected_at is not None:
                count += 1
            candidate.selected = False
            candidate.selection_reason = None
            candidate.selected_at = None
        return count

    def _deactivate_stock_selections(
        self,
        run_id: str,
        horizon: str,
        selected_keys: set[Tuple[str, str, str, str, str]],
    ) -> int:
        rows = (
            self._disc.query(DiscoverySelection)
            .filter_by(run_id=run_id, horizon=horizon, entity_type=ENTITY_TYPE_STOCK)
            .all()
        )
        stale = 0
        now = datetime.datetime.utcnow()
        for row in rows:
            key = (
                row.company_id or "",
                row.symbol or row.entity_name or "",
                row.parent_sector or "",
                row.parent_industry or "",
                row.basic_industry or "",
            )
            if key in selected_keys:
                continue
            if row.selected:
                stale += 1
            row.selected = False
            row.updated_at = now
        return stale

    def _persist_stock_selections(
        self,
        run_id: str,
        horizon: str,
        hierarchy: Dict[str, str],
        selected: List[StockCandidateSnapshot],
    ) -> None:
        now = datetime.datetime.utcnow()
        for candidate in selected:
            row = (
                self._disc.query(DiscoverySelection)
                .filter_by(
                    run_id=run_id,
                    horizon=horizon,
                    entity_type=ENTITY_TYPE_STOCK,
                    entity_name=candidate.symbol,
                    parent_sector=candidate.sector,
                    parent_industry=candidate.industry,
                )
                .first()
            )
            if row is None:
                row = DiscoverySelection(
                    id=str(uuid.uuid4()),
                    run_id=run_id,
                    horizon=horizon,
                    entity_type=ENTITY_TYPE_STOCK,
                    entity_name=candidate.symbol,
                    parent_sector=candidate.sector,
                    parent_industry=candidate.industry,
                    created_at=now,
                )
                self._disc.add(row)
            row.company_id = candidate.company_id
            row.symbol = candidate.symbol
            row.basic_industry = candidate.basic_industry
            row.rank = candidate.rank
            row.final_score = candidate.final_score
            row.technical_score = candidate.technical_score
            row.fundamental_score = candidate.fundamental_score
            row.macro_score = candidate.inherited_macro_score
            row.selected = True
            row.selection_reason = SELECTION_REASON
            row.calculation_details = {
                "selected_hierarchy": copy.deepcopy(hierarchy),
                "ranking": {
                    "rank": candidate.rank,
                    "final_score": candidate.final_score,
                    "technical_score": candidate.technical_score,
                    "fundamental_score": candidate.fundamental_score,
                    "inherited_macro_score": candidate.inherited_macro_score,
                    "score_coverage_pct": candidate.score_coverage_pct,
                    "selected": True,
                },
            }
            row.updated_at = now

    def _metadata(
        self,
        horizon: str,
        hierarchy: Optional[Dict[str, str]],
        candidates: List[StockCandidateSnapshot],
        ranked: List[StockCandidateSnapshot],
        selected: List[StockCandidateSnapshot],
        stale_rank_count: int,
        stale_selection_count: int,
        hierarchy_mismatch_count: int,
    ) -> Dict[str, Any]:
        hierarchy = hierarchy or {}
        return {
            "horizon": horizon,
            "selected_sector": hierarchy.get("sector"),
            "selected_industry": hierarchy.get("industry"),
            "selected_basic_industry": hierarchy.get("basic_industry"),
            "candidate_count": len(candidates),
            "score_eligible_count": sum(
                1
                for candidate in candidates
                if candidate.eligible is True
                and candidate.score_eligible is True
                and _is_finite(candidate.final_score)
            ),
            "ranked_candidate_count": len(ranked),
            "selected_stock_count": len(selected),
            "selected_symbols": [candidate.symbol for candidate in selected],
            "stale_rank_count": stale_rank_count,
            "stale_selection_count": stale_selection_count,
            "hierarchy_mismatch_count": hierarchy_mismatch_count,
        }
