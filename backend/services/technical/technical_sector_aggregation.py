"""
TechnicalSectorAggregationService

Aggregates company-level technical metrics into sector-level group scores.
"""
import uuid
import logging
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert

from models.discovery import GroupScore
from services.technical.technical_consistency import aggregate_group_consistency_periods

logger = logging.getLogger(__name__)

def _median(lst):
    if not lst:
        return None
    s = sorted(lst)
    n = len(s)
    if n % 2 == 0:
        return (s[n//2 - 1] + s[n//2]) / 2.0
    return s[n//2]

class TechnicalSectorAggregationService:
    def __init__(self, discovery_session: Session):
        self._disc = discovery_session

    def aggregate_sectors(self, run_id: str, horizon: str, sectors: list[str] | None = None) -> None:
        # 1. Fetch all company technical metrics for the run and horizon
        records = self._disc.execute(
            text("""
                SELECT 
                    sector, 
                    return_available, company_return, relative_return,
                    volume_available, volume_change,
                    consistency_available, company_consistency_score
                FROM company_technical_metrics
                WHERE run_id = :r AND horizon = :h
            """),
            {"r": run_id, "h": horizon}
        ).fetchall()

        if not records:
            return

        # 2. Group by sector
        sectors_map = {}
        for r in records:
            # Skip invalid/empty sectors
            sec = (r.sector or "").strip()
            if not sec:
                continue
            if sectors is not None and sec not in sectors:
                continue
            if sec not in sectors_map:
                sectors_map[sec] = []
            sectors_map[sec].append(r)

        values_to_upsert = []

        # 3. Calculate metrics per sector
        for sector, comps in sectors_map.items():
            constituent_count = len(comps)
            
            # Return subset
            ret_eligible = [c for c in comps if c.return_available]
            return_eligible_count = len(ret_eligible)

            warnings = []
            if return_eligible_count < 5:
                warnings.append("INSUFFICIENT_CONSTITUENTS")

            calc_details = {}
            breadth_score = None
            
            if return_eligible_count > 0:
                rel_returns = [c.relative_return for c in ret_eligible if c.relative_return is not None]
                if rel_returns:
                    calc_details["median_relative_return"] = _median(rel_returns)
                    calc_details["mean_relative_return"] = sum(rel_returns) / len(rel_returns)

                pos_ret_count = sum(1 for c in ret_eligible if c.company_return is not None and c.company_return > 0)
                outperf_count = sum(1 for c in ret_eligible if c.relative_return is not None and c.relative_return > 0)

                pos_ret_breadth = (pos_ret_count / return_eligible_count) * 100.0
                outperf_breadth = (outperf_count / return_eligible_count) * 100.0
                breadth_score = (pos_ret_breadth * 0.5) + (outperf_breadth * 0.5)

                calc_details["positive_return_breadth"] = pos_ret_breadth
                calc_details["outperformance_breadth"] = outperf_breadth

            # Volume subset
            vol_eligible = [c for c in comps if c.volume_available]
            vol_eligible_count = len(vol_eligible)
            vol_score = None
            
            vol_coverage = (vol_eligible_count / constituent_count) * 100.0 if constituent_count > 0 else 0
            if vol_coverage < 60.0:
                warnings.append("INSUFFICIENT_SECTOR_VOLUME_COVERAGE")
            elif vol_eligible_count > 0:
                vol_conf_count = sum(
                    1 for c in vol_eligible 
                    if c.company_return is not None and c.company_return > 0
                    and c.volume_change is not None and c.volume_change > 0
                )
                dist_count = sum(
                    1 for c in vol_eligible 
                    if c.company_return is not None and c.company_return < 0
                    and c.volume_change is not None and c.volume_change > 0
                )
                
                vol_score = (vol_conf_count / vol_eligible_count) * 100.0
                calc_details["distribution_percentage"] = (dist_count / vol_eligible_count) * 100.0

            # Consistency subset
            cons_eligible = [c for c in comps if c.consistency_available and c.company_consistency_score is not None]
            cons_eligible_count = len(cons_eligible)
            cons_score = None

            if cons_eligible_count > 0:
                scores = [c.company_consistency_score for c in cons_eligible]
                cons_score = sum(scores) / cons_eligible_count
                
                calc_details["median_consistency"] = _median(scores)
                gte_60 = sum(1 for s in scores if s >= 60.0)
                calc_details["percent_consistency_gte_60"] = (gte_60 / cons_eligible_count) * 100.0
                calc_details["consistency_periods"] = aggregate_group_consistency_periods(cons_eligible)
            else:
                warnings.append("INSUFFICIENT_SECTOR_CONSISTENCY_COVERAGE")

            # 4. Prepare row for group_scores
            values_to_upsert.append({
                "id": str(uuid.uuid4()),
                "run_id": run_id,
                "entity_type": "SECTOR",
                "entity_name": sector,
                "parent_sector": "",
                "parent_industry": "",
                "horizon": horizon,
                "constituent_count": constituent_count,
                "eligible_constituent_count": return_eligible_count,
                "technical_breadth_score": breadth_score,
                "technical_volume_score": vol_score,
                "technical_consistency_score": cons_score,
                "warnings": warnings,
                "calculation_details": calc_details
            })

        if not values_to_upsert:
            return

        # 5. UPSERT into group_scores
        stmt = insert(GroupScore).values(values_to_upsert)
        update_dict = {
            "constituent_count": stmt.excluded.constituent_count,
            "eligible_constituent_count": stmt.excluded.eligible_constituent_count,
            "technical_breadth_score": stmt.excluded.technical_breadth_score,
            "technical_volume_score": stmt.excluded.technical_volume_score,
            "technical_consistency_score": stmt.excluded.technical_consistency_score,
            "warnings": stmt.excluded.warnings,
            "calculation_details": stmt.excluded.calculation_details,
        }

        stmt = stmt.on_conflict_do_update(
            index_elements=['run_id', 'entity_type', 'entity_name', 'parent_sector', 'parent_industry', 'horizon'],
            set_=update_dict
        )

        self._disc.execute(stmt)
        self._disc.commit()
