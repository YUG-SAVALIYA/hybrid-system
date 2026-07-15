"""Strict per-horizon selection stages for the discovery pipeline."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from models.discovery import DiscoverySelection
from services.fundamental.fundamental_basic_industry_metric_normalization import (
    FundamentalBasicIndustryMetricNormalizationService,
)
from services.fundamental.fundamental_basic_industry_pillar_score import (
    FundamentalBasicIndustryPillarScoreService,
)
from services.fundamental.fundamental_basic_industry_transition_score import (
    FundamentalBasicIndustryTransitionScoreService,
)
from services.fundamental.fundamental_industry_metric_normalization import (
    FundamentalIndustryMetricNormalizationService,
)
from services.fundamental.fundamental_industry_pillar_score import (
    FundamentalIndustryPillarScoreService,
)
from services.fundamental.fundamental_industry_transition_score import (
    FundamentalIndustryTransitionScoreService,
)
from services.fundamental.fundamental_sector_metric_normalization import (
    FundamentalSectorMetricNormalizationService,
)
from services.fundamental.fundamental_sector_pillar_score import FundamentalSectorPillarScoreService
from services.fundamental.fundamental_sector_transition_score import (
    FundamentalSectorTransitionScoreService,
)
from services.macro.macro_basic_industry_impact import MacroBasicIndustryImpactService
from services.macro.macro_basic_industry_score import MacroBasicIndustryScoreService
from services.macro.macro_industry_impact import MacroIndustryImpactService
from services.macro.macro_industry_score import MacroIndustryScoreService
from services.macro.macro_sector_impact import MacroSectorImpactService
from services.macro.macro_sector_score import MacroSectorScoreService
from services.ranking.basic_industry_discovery_ranking import (
    BasicIndustryDiscoveryRankingService,
)
from services.ranking.industry_discovery_ranking import IndustryDiscoveryRankingService
from services.ranking.sector_discovery_ranking import SectorDiscoveryRankingService
from services.stock.stock_candidate_score import StockCandidateScoreService
from services.stock.stock_candidate_universe import StockCandidateUniverseService
from services.stock.stock_discovery_ranking import StockDiscoveryRankingService
from services.technical.technical_basic_industry_aggregation import (
    TechnicalBasicIndustryAggregationService,
)
from services.technical.technical_basic_industry_score import TechnicalBasicIndustryScoreService
from services.technical.technical_industry_aggregation import TechnicalIndustryAggregationService
from services.technical.technical_industry_score import TechnicalIndustryScoreService
from services.technical.technical_sector_aggregation import TechnicalSectorAggregationService
from services.technical.technical_sector_score import TechnicalSectorScoreService
from services.fundamental.fundamental_group_aggregation import FundamentalGroupAggregationService
from services.fundamental.fundamental_group_score import FundamentalGroupScoreService


STAGE_FAILED = "FAILED"
W_SELECTED_SECTOR_UNAVAILABLE = "SELECTED_SECTOR_UNAVAILABLE"
W_SELECTED_INDUSTRY_UNAVAILABLE = "SELECTED_INDUSTRY_UNAVAILABLE"


class _StageRunner:
    def __init__(self, discovery_session: Session):
        self._disc = discovery_session

    def _selected(
        self,
        run_id: str,
        horizon: str,
        entity_type: str,
    ) -> Optional[DiscoverySelection]:
        return (
            self._disc.query(DiscoverySelection)
            .filter_by(
                run_id=run_id,
                horizon=horizon,
                entity_type=entity_type,
                selected=True,
            )
            .order_by(DiscoverySelection.rank.asc(), DiscoverySelection.entity_name.asc())
            .first()
        )

    def _run_steps(self, steps: List[Tuple[str, Any]]) -> Dict[str, Any]:
        metadata: Dict[str, Any] = {"steps": {}}
        warnings: List[str] = []
        for name, func in steps:
            output = func()
            if isinstance(output, dict):
                metadata["steps"][name] = {
                    key: value
                    for key, value in output.items()
                    if key not in {"warnings", "status"}
                }
                warnings.extend(str(item) for item in output.get("warnings") or [] if item)
                if output.get("status") == STAGE_FAILED:
                    return {
                        "status": STAGE_FAILED,
                        "warnings": sorted(set(warnings)),
                        "metadata": metadata,
                    }
            else:
                metadata["steps"][name] = output
        return {"warnings": sorted(set(warnings)), "metadata": metadata}


class SectorSelectionStage(_StageRunner):
    def run(self, run_id: str, horizon: str) -> Dict[str, Any]:
        return self._run_steps(
            [
                ("technical_sector_aggregation", lambda: TechnicalSectorAggregationService(self._disc).aggregate_sectors(run_id, horizon)),
                ("technical_sector_score", lambda: TechnicalSectorScoreService(self._disc).calculate_sector_scores(run_id, horizon)),
                ("fundamental_sector_aggregation", lambda: FundamentalGroupAggregationService(self._disc).aggregate_groups(run_id, horizon, entity_type="SECTOR")),
                ("fundamental_sector_normalization", lambda: FundamentalSectorMetricNormalizationService(self._disc).normalize_metrics(run_id, horizon)),
                ("fundamental_sector_transition", lambda: FundamentalSectorTransitionScoreService(self._disc).calculate_transition_scores(run_id, horizon)),
                ("fundamental_sector_pillar", lambda: FundamentalSectorPillarScoreService(self._disc).calculate_pillar_scores(run_id, horizon)),
                ("fundamental_sector_score", lambda: FundamentalGroupScoreService(self._disc).calculate_final_scores(run_id, horizon, entity_type="SECTOR")),
                ("macro_sector_impact", lambda: MacroSectorImpactService(self._disc).generate_sector_impacts(run_id, horizon)),
                ("macro_sector_score", lambda: MacroSectorScoreService(self._disc).calculate_sector_scores(run_id, horizon)),
                ("sector_ranking", lambda: SectorDiscoveryRankingService(self._disc).rank_and_select(run_id, horizon)),
            ]
        )


class IndustrySelectionStage(_StageRunner):
    def run(self, run_id: str, horizon: str) -> Dict[str, Any]:
        sector = self._selected(run_id, horizon, "SECTOR")
        if sector is None or not sector.entity_name:
            return {"status": STAGE_FAILED, "warnings": [W_SELECTED_SECTOR_UNAVAILABLE], "metadata": {}}
        parent_sector = sector.entity_name
        return self._run_steps(
            [
                ("technical_industry_aggregation", lambda: TechnicalIndustryAggregationService(self._disc).aggregate_industries(run_id, horizon, parent_sector=parent_sector)),
                ("technical_industry_score", lambda: TechnicalIndustryScoreService(self._disc).calculate_industry_scores(run_id, horizon, parent_sector=parent_sector)),
                ("fundamental_industry_aggregation", lambda: FundamentalGroupAggregationService(self._disc).aggregate_groups(run_id, horizon, entity_type="INDUSTRY", parent_sector=parent_sector)),
                ("fundamental_industry_normalization", lambda: FundamentalIndustryMetricNormalizationService(self._disc).normalize_industry_metrics(run_id, horizon, parent_sector=parent_sector)),
                ("fundamental_industry_transition", lambda: FundamentalIndustryTransitionScoreService(self._disc).calculate_transition_scores(run_id, horizon, parent_sector=parent_sector)),
                ("fundamental_industry_pillar", lambda: FundamentalIndustryPillarScoreService(self._disc).calculate_pillar_scores(run_id, horizon, parent_sector=parent_sector)),
                ("fundamental_industry_score", lambda: FundamentalGroupScoreService(self._disc).calculate_final_scores(run_id, horizon, entity_type="INDUSTRY", parent_sector=parent_sector)),
                ("macro_industry_impact", lambda: MacroIndustryImpactService(self._disc).generate_industry_impacts(run_id, horizon)),
                ("macro_industry_score", lambda: MacroIndustryScoreService(self._disc).calculate_industry_scores(run_id, horizon)),
                ("industry_ranking", lambda: IndustryDiscoveryRankingService(self._disc).rank_and_select(run_id, horizon)),
            ]
        )


class BasicIndustrySelectionStage(_StageRunner):
    def run(self, run_id: str, horizon: str) -> Dict[str, Any]:
        industry = self._selected(run_id, horizon, "INDUSTRY")
        if industry is None or not industry.entity_name or not industry.parent_sector:
            return {"status": STAGE_FAILED, "warnings": [W_SELECTED_INDUSTRY_UNAVAILABLE], "metadata": {}}
        parent_sector = industry.parent_sector
        parent_industry = industry.entity_name
        return self._run_steps(
            [
                ("technical_basic_industry_aggregation", lambda: TechnicalBasicIndustryAggregationService(self._disc).aggregate_basic_industries(run_id, horizon, parent_sector=parent_sector, parent_industry=parent_industry)),
                ("technical_basic_industry_score", lambda: TechnicalBasicIndustryScoreService(self._disc).calculate_basic_industry_scores(run_id, horizon, parent_sector=parent_sector, parent_industry=parent_industry)),
                ("fundamental_basic_industry_aggregation", lambda: FundamentalGroupAggregationService(self._disc).aggregate_groups(run_id, horizon, entity_type="BASIC_INDUSTRY", parent_sector=parent_sector, parent_industry=parent_industry)),
                ("fundamental_basic_industry_normalization", lambda: FundamentalBasicIndustryMetricNormalizationService(self._disc).normalize_basic_industry_metrics(run_id, horizon, parent_sector=parent_sector, parent_industry=parent_industry)),
                ("fundamental_basic_industry_transition", lambda: FundamentalBasicIndustryTransitionScoreService(self._disc).calculate_basic_industry_transitions(run_id, horizon, parent_sector=parent_sector, parent_industry=parent_industry)),
                ("fundamental_basic_industry_pillar", lambda: FundamentalBasicIndustryPillarScoreService(self._disc).calculate_pillar_scores(run_id, horizon, parent_sector=parent_sector, parent_industry=parent_industry)),
                ("fundamental_basic_industry_score", lambda: FundamentalGroupScoreService(self._disc).calculate_final_scores(run_id, horizon, entity_type="BASIC_INDUSTRY", parent_sector=parent_sector, parent_industry=parent_industry)),
                ("macro_basic_industry_impact", lambda: MacroBasicIndustryImpactService(self._disc).generate_basic_industry_impacts(run_id, horizon)),
                ("macro_basic_industry_score", lambda: MacroBasicIndustryScoreService(self._disc).calculate_basic_industry_scores(run_id, horizon)),
                ("basic_industry_ranking", lambda: BasicIndustryDiscoveryRankingService(self._disc).rank_and_select(run_id, horizon)),
            ]
        )


class StockSelectionStage(_StageRunner):
    def run(self, run_id: str, horizon: str) -> Dict[str, Any]:
        return self._run_steps(
            [
                ("stock_candidate_universe", lambda: StockCandidateUniverseService(self._disc).build_candidates(run_id, horizon)),
                ("stock_candidate_score", lambda: StockCandidateScoreService(self._disc).score_candidates(run_id, horizon)),
                ("stock_ranking", lambda: StockDiscoveryRankingService(self._disc).rank_and_select(run_id, horizon)),
            ]
        )
