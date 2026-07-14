"""
TechnicalBasicIndustryAggregationService

Aggregates company-level technical metrics into basic-industry-level group scores.
"""
import uuid
import logging
from sqlalchemy import text, func, cast
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert, JSONB

import config
from models.discovery import GroupScore

logger = logging.getLogger(__name__)

def _median(lst):
    if not lst:
        return None
    s = sorted(lst)
    n = len(s)
    if n % 2 == 0:
        return (s[n//2 - 1] + s[n//2]) / 2.0
    return s[n//2]

class TechnicalBasicIndustryAggregationService:
    def __init__(self, discovery_session: Session):
        self._disc = discovery_session

    def aggregate_basic_industries(self, run_id: str, horizon: str) -> None:
        records = self._disc.execute(
            text("""
                SELECT 
                    sector, industry, basic_industry,
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

        groups_map = {}
        for r in records:
            sec = (r.sector or "").strip()
            ind = (r.industry or "").strip()
            bind = (r.basic_industry or "").strip()
            if not sec or not ind or not bind:
                continue
            key = (sec, ind, bind)
            if key not in groups_map:
                groups_map[key] = []
            groups_map[key].append(r)

        values_to_upsert = []

        for (sector, industry, basic_industry), comps in groups_map.items():
            constituent_count = len(comps)
            
            # Return subset
            ret_eligible = [c for c in comps if c.return_available]
            return_eligible_count = len(ret_eligible)

            warnings = []
            if return_eligible_count < config.MIN_BASIC_INDUSTRY_COMPANIES:
                warnings.append("INSUFFICIENT_CONSTITUENTS")

            calc_details = {
                "technical": {
                    "return": {
                        "return_eligible_count": return_eligible_count,
                        "median_company_return": None,
                        "mean_company_return": None,
                        "median_relative_return": None,
                        "mean_relative_return": None
                    },
                    "breadth": {
                        "positive_return_breadth": None,
                        "outperformance_breadth": None
                    },
                    "volume": {
                        "volume_eligible_count": 0,
                        "positive_volume_confirmation_count": 0,
                        "distribution_count": 0,
                        "distribution_percentage": None,
                        "volume_coverage": None
                    },
                    "consistency": {
                        "consistency_eligible_count": 0,
                        "mean_consistency_score": None,
                        "median_consistency_score": None,
                        "consistent_company_percentage": None
                    }
                }
            }

            breadth_score = None
            
            if return_eligible_count > 0:
                c_returns = [c.company_return for c in ret_eligible if c.company_return is not None]
                if c_returns:
                    calc_details["technical"]["return"]["median_company_return"] = _median(c_returns)
                    calc_details["technical"]["return"]["mean_company_return"] = sum(c_returns) / len(c_returns)

                rel_returns = [c.relative_return for c in ret_eligible if c.relative_return is not None]
                if rel_returns:
                    calc_details["technical"]["return"]["median_relative_return"] = _median(rel_returns)
                    calc_details["technical"]["return"]["mean_relative_return"] = sum(rel_returns) / len(rel_returns)

                pos_ret_count = sum(1 for c in ret_eligible if c.company_return is not None and c.company_return > 0)
                outperf_count = sum(1 for c in ret_eligible if c.relative_return is not None and c.relative_return > 0)

                pos_ret_breadth = (pos_ret_count / return_eligible_count) * 100.0
                outperf_breadth = (outperf_count / return_eligible_count) * 100.0
                breadth_score = (pos_ret_breadth * 0.5) + (outperf_breadth * 0.5)

                calc_details["technical"]["breadth"]["positive_return_breadth"] = pos_ret_breadth
                calc_details["technical"]["breadth"]["outperformance_breadth"] = outperf_breadth

            # Volume subset
            vol_eligible = [c for c in comps if c.volume_available]
            vol_eligible_count = len(vol_eligible)
            vol_score = None
            
            vol_coverage = (vol_eligible_count / constituent_count) * 100.0 if constituent_count > 0 else 0
            
            calc_details["technical"]["volume"]["volume_eligible_count"] = vol_eligible_count
            calc_details["technical"]["volume"]["volume_coverage"] = vol_coverage

            if vol_eligible_count > 0:
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
                
                dist_pct = (dist_count / vol_eligible_count) * 100.0
                
                calc_details["technical"]["volume"]["positive_volume_confirmation_count"] = vol_conf_count
                calc_details["technical"]["volume"]["distribution_count"] = dist_count
                calc_details["technical"]["volume"]["distribution_percentage"] = dist_pct

                if vol_coverage < 60.0:
                    warnings.append("LOW_VOLUME_DATA_COVERAGE")
                else:
                    vol_score = (vol_conf_count / vol_eligible_count) * 100.0

                if dist_pct >= 50.0:
                    warnings.append("HIGH_DISTRIBUTION_PARTICIPATION")
            else:
                if vol_coverage < 60.0:
                    warnings.append("LOW_VOLUME_DATA_COVERAGE")

            # Consistency subset
            cons_eligible = [c for c in comps if c.consistency_available and c.company_consistency_score is not None]
            cons_eligible_count = len(cons_eligible)
            cons_score = None

            calc_details["technical"]["consistency"]["consistency_eligible_count"] = cons_eligible_count

            if cons_eligible_count > 0:
                scores = [c.company_consistency_score for c in cons_eligible]
                cons_score = sum(scores) / cons_eligible_count
                
                calc_details["technical"]["consistency"]["mean_consistency_score"] = cons_score
                calc_details["technical"]["consistency"]["median_consistency_score"] = _median(scores)
                gte_60 = sum(1 for s in scores if s >= 60.0)
                calc_details["technical"]["consistency"]["consistent_company_percentage"] = (gte_60 / cons_eligible_count) * 100.0

            values_to_upsert.append({
                "id": str(uuid.uuid4()),
                "run_id": run_id,
                "entity_type": "BASIC_INDUSTRY",
                "entity_name": basic_industry,
                "parent_sector": sector,
                "parent_industry": industry,
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

        stmt = insert(GroupScore).values(values_to_upsert)
        
        update_dict = {
            "constituent_count": stmt.excluded.constituent_count,
            "eligible_constituent_count": stmt.excluded.eligible_constituent_count,
            "technical_breadth_score": stmt.excluded.technical_breadth_score,
            "technical_volume_score": stmt.excluded.technical_volume_score,
            "technical_consistency_score": stmt.excluded.technical_consistency_score,
            "warnings": stmt.excluded.warnings,
            "calculation_details": func.coalesce(
                cast(GroupScore.calculation_details, JSONB), text("'{}'::jsonb")
            ).op('||')(
                cast(stmt.excluded.calculation_details, JSONB)
            ),
        }

        stmt = stmt.on_conflict_do_update(
            index_elements=['run_id', 'entity_type', 'entity_name', 'parent_sector', 'parent_industry', 'horizon'],
            set_=update_dict
        )

        self._disc.execute(stmt)
        self._disc.commit()
