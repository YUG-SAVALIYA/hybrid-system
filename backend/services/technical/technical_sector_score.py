"""
TechnicalSectorScoreService

Normalizes median relative returns into percentile return scores,
aggregates available component scores using configured weights,
calculates data coverage, and assigns technical status.
"""
from __future__ import annotations

import logging
from sqlalchemy import text, update
from sqlalchemy.orm import Session

import config
from models.discovery import GroupScore

logger = logging.getLogger(__name__)


def _get_status(score: float) -> str:
    if score >= 80.0: return "VERY_STRONG"
    if score >= 65.0: return "STRONG"
    if score >= 50.0: return "NEUTRAL"
    if score >= 35.0: return "WEAK"
    return "VERY_WEAK"


class TechnicalSectorScoreService:
    def __init__(self, discovery_session: Session):
        self._disc = discovery_session

    def calculate_sector_scores(self, run_id: str, horizon: str) -> None:
        records = self._disc.execute(
            text("""
                SELECT 
                    id, entity_name, technical_breadth_score, technical_volume_score, technical_consistency_score,
                    warnings, calculation_details
                FROM group_scores
                WHERE run_id = :r AND horizon = :h AND entity_type = 'SECTOR'
            """),
            {"r": run_id, "h": horizon}
        ).fetchall()

        if not records:
            return

        valid_medians = []
        parsed_records = []
        for r in records:
            calc_details = dict(r.calculation_details) if r.calculation_details else {}
            # Fallback handling to grab the raw sector value wherever it was stored
            med_ret = None
            if "technical" in calc_details and "return" in calc_details["technical"]:
                med_ret = calc_details["technical"]["return"].get("median_relative_return")
            if med_ret is None:
                med_ret = calc_details.get("median_relative_return")
                
            parsed_records.append({
                "id": r.id,
                "entity_name": r.entity_name,
                "breadth_score": r.technical_breadth_score,
                "volume_score": r.technical_volume_score,
                "consistency_score": r.technical_consistency_score,
                "warnings": list(r.warnings) if r.warnings else [],
                "calc_details": calc_details,
                "med_ret": med_ret
            })
            if med_ret is not None:
                valid_medians.append(med_ret)

        valid_medians.sort()
        num_valid_sectors = len(valid_medians)

        values_to_update = []
        for pr in parsed_records:
            med_ret = pr["med_ret"]
            tech_return_score = None
            ret_rank = None
            percentile = None

            if med_ret is not None:
                if num_valid_sectors == 1:
                    tech_return_score = 50.0
                    ret_rank = 1.0
                    percentile = 50.0
                    if "SINGLE_SECTOR_COMPARISON" not in pr["warnings"]:
                        pr["warnings"].append("SINGLE_SECTOR_COMPARISON")
                else:
                    indices = [i for i, v in enumerate(valid_medians) if abs(v - med_ret) < 1e-9]
                    ret_rank = (sum(indices) / len(indices)) + 1
                    percentile = ((ret_rank - 1.0) / (num_valid_sectors - 1.0)) * 100.0
                    tech_return_score = percentile

            tech_details = pr["calc_details"].get("technical", {})
            if "return" not in tech_details:
                tech_details["return"] = {}
                
            if med_ret is not None:
                tech_details["return"].update({
                    "median_relative_return": med_ret,
                    "comparison_set_size": num_valid_sectors,
                    "rank": ret_rank,
                    "percentile_rank": percentile
                })

            available_weight = 0.0
            weighted_sum = 0.0
            
            w_ret = config.TECHNICAL_SCORE_WEIGHTS["return"]
            if tech_return_score is not None:
                available_weight += w_ret
                weighted_sum += tech_return_score * w_ret
                
            w_brd = config.TECHNICAL_SCORE_WEIGHTS["breadth"]
            if pr["breadth_score"] is not None:
                available_weight += w_brd
                weighted_sum += pr["breadth_score"] * w_brd
                
            w_vol = config.TECHNICAL_SCORE_WEIGHTS["volume"]
            if pr["volume_score"] is not None:
                available_weight += w_vol
                weighted_sum += pr["volume_score"] * w_vol
                
            w_cons = config.TECHNICAL_SCORE_WEIGHTS["consistency"]
            if pr["consistency_score"] is not None:
                available_weight += w_cons
                weighted_sum += pr["consistency_score"] * w_cons

            tech_score = None
            if available_weight > 0:
                tech_score = weighted_sum / available_weight

            total_configured_weight = sum(config.TECHNICAL_SCORE_WEIGHTS.values())
            data_coverage = (available_weight / total_configured_weight) * 100.0

            if data_coverage < config.MIN_GROUP_TECHNICAL_COVERAGE:
                if "LOW_TECHNICAL_DATA_COVERAGE" not in pr["warnings"]:
                    pr["warnings"].append("LOW_TECHNICAL_DATA_COVERAGE")

            if tech_score is not None:
                tech_details["status"] = _get_status(tech_score)

            pr["calc_details"]["technical"] = tech_details

            # We DO NOT modify fundamental_score, macro_score, final_score, rank.
            # Using UPDATE cleanly preserves all other fields.
            values_to_update.append({
                "id": pr["id"],
                "technical_return_score": tech_return_score,
                "technical_score": tech_score,
                "data_coverage": data_coverage,
                "warnings": pr["warnings"],
                "calculation_details": pr["calc_details"]
            })

        if values_to_update:
            self._disc.execute(
                update(GroupScore),
                values_to_update
            )
            self._disc.commit()
