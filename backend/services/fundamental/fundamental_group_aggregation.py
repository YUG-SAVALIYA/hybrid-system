"""
FundamentalGroupAggregationService

Hierarchy-aware raw fundamental aggregation for sectors, industries, and
basic industries.
"""
from __future__ import annotations

import copy
import logging
import uuid
from typing import Any, Dict, List

import config
from sqlalchemy.orm import Session

from models.discovery import CompanyFundamentalMetric, GroupScore

logger = logging.getLogger(__name__)

MIN_SECTOR_FUNDAMENTAL_COMPANIES = getattr(config, "MIN_SECTOR_FUNDAMENTAL_COMPANIES", 5)
MIN_INDUSTRY_FUNDAMENTAL_COMPANIES = getattr(config, "MIN_INDUSTRY_FUNDAMENTAL_COMPANIES", 3)
MIN_BASIC_INDUSTRY_FUNDAMENTAL_COMPANIES = getattr(
    config, "MIN_BASIC_INDUSTRY_FUNDAMENTAL_COMPANIES", 2
)

NORMAL_METRICS = [
    ("sales_growth_pct", "growth"),
    ("net_profit_growth_pct", "growth"),
    ("latest_operating_margin_pct", "profitability"),
    ("operating_margin_change_pp", "profitability"),
    ("latest_ocf_to_pat", "earnings_quality", "cash_conversion"),
    ("ocf_to_pat_change", "earnings_quality", "cash_conversion"),
    ("positive_pat_period_ratio", "earnings_quality", "profit_stability"),
    ("pat_growth_volatility_pct", "earnings_quality", "profit_stability"),
]

DEBT_METRICS = [
    ("debt_to_equity", "financial_strength"),
    ("borrowing_change_pct", "financial_strength"),
]


def _is_finite(val: float | None) -> bool:
    if val is None:
        return False
    if isinstance(val, (int, float)):
        return val == val and val != float("inf") and val != float("-inf")
    return False


def _median(sorted_values: List[float]) -> float | None:
    n = len(sorted_values)
    if n == 0:
        return None
    mid = n // 2
    if n % 2 != 0:
        return sorted_values[mid]
    return (sorted_values[mid - 1] + sorted_values[mid]) / 2.0


def _aggregate_metric(values: List[float], applicable_count: int, round_coverage: bool) -> Dict[str, Any]:
    valid_count = len(values)
    median = _median(sorted(values))
    coverage = None
    reason = None
    if applicable_count == 0:
        reason = "N_A_NO_STANDARD_DEBT_RULE_COMPANIES"
    else:
        coverage_value = (valid_count / applicable_count) * 100.0
        coverage = round(coverage_value, 2) if round_coverage else coverage_value

    return {
        "median": median,
        "valid_count": valid_count,
        "applicable_count": applicable_count,
        "coverage_pct": coverage,
        "reason": reason,
    }


def _aggregate_distribution(values: List[str]) -> Dict[str, Any]:
    valid_status_count = len(values)
    counts: Dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1

    percentages: Dict[str, float] = {}
    if valid_status_count > 0:
        for key, count in counts.items():
            percentages[key] = round((count / valid_status_count) * 100.0, 2)

    return {
        "valid_status_count": valid_status_count,
        "counts": counts,
        "percentages": percentages,
    }


class FundamentalGroupAggregationService:
    def __init__(self, discovery_session: Session):
        self._disc = discovery_session

    def aggregate_groups(
        self,
        run_id: str,
        horizon: str | None = None,
        entity_type: str = "SECTOR",
        parent_sector: str | None = None,
        parent_industry: str | None = None,
    ) -> None:
        entity_type = (entity_type or "").upper().strip()
        if entity_type == "SECTOR":
            self._aggregate_sector(run_id, horizon)
        elif entity_type == "INDUSTRY":
            self._aggregate_industry(run_id, horizon, parent_sector)
        elif entity_type == "BASIC_INDUSTRY":
            self._aggregate_basic_industry(run_id, horizon, parent_sector, parent_industry)
        else:
            raise ValueError(f"Unsupported entity_type: {entity_type}")

    def _aggregate_sector(self, run_id: str, horizon: str | None = None) -> None:
        query = self._disc.query(CompanyFundamentalMetric).filter_by(run_id=run_id)
        companies = query.all()
        if not companies:
            return

        sectors_map: Dict[str, List[CompanyFundamentalMetric]] = {}
        for company in companies:
            if not company.sector:
                continue
            sectors_map.setdefault(company.sector, []).append(company)

        for sector_name, mems in sectors_map.items():
            constituent_count = len(mems)
            fundamental_score_available_count = 0
            fundamental_selection_eligible_count = 0
            standard_debt_rule_applicable_count = 0
            standard_debt_rule_not_applicable_count = 0

            raw_metrics = {
                "sales_growth_pct": [],
                "net_profit_growth_pct": [],
                "latest_operating_margin_pct": [],
                "operating_margin_change_pp": [],
                "debt_to_equity": [],
                "borrowing_change_pct": [],
                "latest_ocf_to_pat": [],
                "ocf_to_pat_change": [],
                "positive_pat_period_ratio": [],
                "pat_growth_volatility_pct": [],
            }

            transitions = {
                "net_profit": [],
                "borrowing": [],
                "cash_conversion": [],
            }

            for metric in mems:
                calc = metric.calculation_details or {}

                if metric.final_fundamental_score is not None and _is_finite(metric.final_fundamental_score):
                    fundamental_score_available_count += 1
                if metric.fundamental_eligible_for_selection:
                    fundamental_selection_eligible_count += 1

                fs_dict = calc.get("financial_strength", {})
                std_debt_applicable = fs_dict.get("standard_debt_rule_applicable", True)
                if std_debt_applicable:
                    standard_debt_rule_applicable_count += 1
                else:
                    standard_debt_rule_not_applicable_count += 1

                growth = calc.get("growth", {})
                if growth.get("sales_growth_pct_available") and _is_finite(growth.get("sales_growth_pct")):
                    raw_metrics["sales_growth_pct"].append(growth["sales_growth_pct"])
                if growth.get("net_profit_growth_pct_available") and _is_finite(growth.get("net_profit_growth_pct")):
                    raw_metrics["net_profit_growth_pct"].append(growth["net_profit_growth_pct"])
                np_trans = growth.get("net_profit_transition")
                if np_trans:
                    transitions["net_profit"].append(np_trans)

                prof = calc.get("profitability", {})
                if prof.get("latest_operating_margin_pct_available") and _is_finite(prof.get("latest_operating_margin_pct")):
                    raw_metrics["latest_operating_margin_pct"].append(prof["latest_operating_margin_pct"])
                if prof.get("operating_margin_change_pp_available") and _is_finite(prof.get("operating_margin_change_pp")):
                    raw_metrics["operating_margin_change_pp"].append(prof["operating_margin_change_pp"])

                if std_debt_applicable:
                    if fs_dict.get("debt_to_equity_available") and _is_finite(fs_dict.get("debt_to_equity")):
                        raw_metrics["debt_to_equity"].append(fs_dict["debt_to_equity"])
                    if fs_dict.get("borrowing_trend_available") and _is_finite(fs_dict.get("borrowing_change_pct")):
                        raw_metrics["borrowing_change_pct"].append(fs_dict["borrowing_change_pct"])
                    b_trans = fs_dict.get("borrowing_transition")
                    if b_trans:
                        transitions["borrowing"].append(b_trans)

                eq = calc.get("earnings_quality", {})
                cc = eq.get("cash_conversion", {})
                if cc.get("latest_ocf_to_pat_available") and _is_finite(cc.get("latest_ocf_to_pat")):
                    raw_metrics["latest_ocf_to_pat"].append(cc["latest_ocf_to_pat"])
                if cc.get("ocf_to_pat_change_available") and _is_finite(cc.get("ocf_to_pat_change")):
                    raw_metrics["ocf_to_pat_change"].append(cc["ocf_to_pat_change"])
                cc_status = cc.get("latest_cash_conversion_status")
                if cc_status:
                    transitions["cash_conversion"].append(cc_status)

                ps = eq.get("profit_stability", {})
                if ps.get("profit_stability_available") and _is_finite(ps.get("positive_pat_period_ratio")):
                    raw_metrics["positive_pat_period_ratio"].append(ps["positive_pat_period_ratio"])
                if ps.get("pat_growth_volatility_available") and _is_finite(ps.get("pat_growth_volatility_pct")):
                    raw_metrics["pat_growth_volatility_pct"].append(ps["pat_growth_volatility_pct"])

            aggregated_metrics: Dict[str, Any] = {}
            for metric_name in [
                "sales_growth_pct",
                "net_profit_growth_pct",
                "latest_operating_margin_pct",
                "operating_margin_change_pp",
                "latest_ocf_to_pat",
                "ocf_to_pat_change",
                "positive_pat_period_ratio",
                "pat_growth_volatility_pct",
            ]:
                aggregated_metrics[metric_name] = _aggregate_metric(
                    raw_metrics[metric_name],
                    constituent_count,
                    round_coverage=False,
                )

            for metric_name in ["debt_to_equity", "borrowing_change_pct"]:
                aggregated_metrics[metric_name] = _aggregate_metric(
                    raw_metrics[metric_name],
                    standard_debt_rule_applicable_count,
                    round_coverage=False,
                )

            aggregated_transitions = {
                "net_profit": _aggregate_distribution(transitions["net_profit"]),
                "borrowing": _aggregate_distribution(transitions["borrowing"]),
                "cash_conversion": _aggregate_distribution(transitions["cash_conversion"]),
            }

            group_score = (
                self._disc.query(GroupScore)
                .filter_by(
                    run_id=run_id,
                    entity_type="SECTOR",
                    entity_name=sector_name,
                    parent_sector="",
                    parent_industry="",
                )
            )
            if horizon is not None:
                group_score = group_score.filter_by(horizon=horizon)
            group_score = group_score.first()
            if not group_score:
                group_score = GroupScore(
                    id=str(uuid.uuid4()),
                    run_id=run_id,
                    entity_type="SECTOR",
                    entity_name=sector_name,
                    parent_sector="",
                    parent_industry="",
                    horizon=horizon,
                )
                self._disc.add(group_score)

            warnings_set = set(group_score.warnings or [])
            warnings_set.discard("INSUFFICIENT_FUNDAMENTAL_CONSTITUENTS")
            if fundamental_score_available_count < MIN_SECTOR_FUNDAMENTAL_COMPANIES:
                warnings_set.add("INSUFFICIENT_FUNDAMENTAL_CONSTITUENTS")

            calc_details = copy.deepcopy(group_score.calculation_details or {})
            if "fundamental" not in calc_details:
                calc_details["fundamental"] = {}

            calc_details["fundamental"]["raw_aggregation"] = {
                "constituent_count": constituent_count,
                "fundamental_score_available_count": fundamental_score_available_count,
                "fundamental_selection_eligible_count": fundamental_selection_eligible_count,
                "standard_debt_rule_applicable_count": standard_debt_rule_applicable_count,
                "standard_debt_rule_not_applicable_count": standard_debt_rule_not_applicable_count,
                "metrics": aggregated_metrics,
                "transitions": aggregated_transitions,
            }

            group_score.constituent_count = constituent_count
            group_score.warnings = sorted(warnings_set)
            group_score.calculation_details = calc_details

        self._disc.commit()

    def _aggregate_industry(
        self,
        run_id: str,
        horizon: str | None = None,
        parent_sector: str | None = None,
    ) -> None:
        query = self._disc.query(CompanyFundamentalMetric).filter_by(run_id=run_id)
        if parent_sector is not None:
            query = query.filter(CompanyFundamentalMetric.sector == parent_sector)
        companies = query.all()
        if not companies:
            return

        industries_map: Dict[tuple, List[CompanyFundamentalMetric]] = {}
        for company in companies:
            if not company.sector or not company.industry:
                continue
            key = (company.sector, company.industry)
            industries_map.setdefault(key, []).append(company)

        for (sector_name, industry_name), mems in industries_map.items():
            constituent_count = len(mems)
            fundamental_score_available_count = 0
            fundamental_selection_eligible_count = 0
            standard_debt_rule_applicable_count = 0
            standard_debt_rule_not_applicable_count = 0

            raw_metrics = {
                "sales_growth_pct": [],
                "net_profit_growth_pct": [],
                "latest_operating_margin_pct": [],
                "operating_margin_change_pp": [],
                "debt_to_equity": [],
                "borrowing_change_pct": [],
                "latest_ocf_to_pat": [],
                "ocf_to_pat_change": [],
                "positive_pat_period_ratio": [],
                "pat_growth_volatility_pct": [],
            }

            transitions = {
                "net_profit": [],
                "borrowing": [],
                "cash_conversion": [],
            }

            for metric in mems:
                calc = metric.calculation_details or {}

                if metric.final_fundamental_score is not None and _is_finite(metric.final_fundamental_score):
                    fundamental_score_available_count += 1
                if metric.fundamental_eligible_for_selection:
                    fundamental_selection_eligible_count += 1

                fs_dict = calc.get("financial_strength", {})
                std_debt_applicable = fs_dict.get("standard_debt_rule_applicable", True)
                if std_debt_applicable:
                    standard_debt_rule_applicable_count += 1
                else:
                    standard_debt_rule_not_applicable_count += 1

                growth = calc.get("growth", {})
                if _is_finite(growth.get("sales_growth_pct")):
                    raw_metrics["sales_growth_pct"].append(growth["sales_growth_pct"])
                if _is_finite(growth.get("net_profit_growth_pct")):
                    raw_metrics["net_profit_growth_pct"].append(growth["net_profit_growth_pct"])
                np_trans = growth.get("net_profit_transition")
                if np_trans:
                    transitions["net_profit"].append(np_trans)

                prof = calc.get("profitability", {})
                if _is_finite(prof.get("latest_operating_margin_pct")):
                    raw_metrics["latest_operating_margin_pct"].append(prof["latest_operating_margin_pct"])
                if _is_finite(prof.get("operating_margin_change_pp")):
                    raw_metrics["operating_margin_change_pp"].append(prof["operating_margin_change_pp"])

                if std_debt_applicable:
                    if _is_finite(fs_dict.get("debt_to_equity")):
                        raw_metrics["debt_to_equity"].append(fs_dict["debt_to_equity"])
                    if _is_finite(fs_dict.get("borrowing_change_pct")):
                        raw_metrics["borrowing_change_pct"].append(fs_dict["borrowing_change_pct"])
                    b_trans = fs_dict.get("borrowing_transition")
                    if b_trans:
                        transitions["borrowing"].append(b_trans)

                eq = calc.get("earnings_quality", {})
                cc = eq.get("cash_conversion", {})
                if _is_finite(cc.get("latest_ocf_to_pat")):
                    raw_metrics["latest_ocf_to_pat"].append(cc["latest_ocf_to_pat"])
                if _is_finite(cc.get("ocf_to_pat_change")):
                    raw_metrics["ocf_to_pat_change"].append(cc["ocf_to_pat_change"])
                cc_status = cc.get("latest_cash_conversion_status")
                if cc_status:
                    transitions["cash_conversion"].append(cc_status)

                ps = eq.get("profit_stability", {})
                if _is_finite(ps.get("positive_pat_period_ratio")):
                    raw_metrics["positive_pat_period_ratio"].append(ps["positive_pat_period_ratio"])
                if _is_finite(ps.get("pat_growth_volatility_pct")):
                    raw_metrics["pat_growth_volatility_pct"].append(ps["pat_growth_volatility_pct"])

            aggregated_metrics: Dict[str, Any] = {}
            for metric_name in [
                "sales_growth_pct",
                "net_profit_growth_pct",
                "latest_operating_margin_pct",
                "operating_margin_change_pp",
                "latest_ocf_to_pat",
                "ocf_to_pat_change",
                "positive_pat_period_ratio",
                "pat_growth_volatility_pct",
            ]:
                aggregated_metrics[metric_name] = _aggregate_metric(
                    raw_metrics[metric_name],
                    constituent_count,
                    round_coverage=True,
                )

            for metric_name in ["debt_to_equity", "borrowing_change_pct"]:
                aggregated_metrics[metric_name] = _aggregate_metric(
                    raw_metrics[metric_name],
                    standard_debt_rule_applicable_count,
                    round_coverage=True,
                )

            aggregated_transitions = {
                "net_profit": _aggregate_distribution(transitions["net_profit"]),
                "borrowing": _aggregate_distribution(transitions["borrowing"]),
                "cash_conversion": _aggregate_distribution(transitions["cash_conversion"]),
            }

            group_score = (
                self._disc.query(GroupScore)
                .filter_by(
                    run_id=run_id,
                    entity_type="INDUSTRY",
                    entity_name=industry_name,
                    parent_sector=sector_name,
                    parent_industry="",
                )
            )
            if horizon is not None:
                group_score = group_score.filter_by(horizon=horizon)
            group_score = group_score.first()
            if not group_score:
                group_score = GroupScore(
                    id=str(uuid.uuid4()),
                    run_id=run_id,
                    entity_type="INDUSTRY",
                    entity_name=industry_name,
                    parent_sector=sector_name,
                    parent_industry="",
                )
                if horizon is not None:
                    group_score.horizon = horizon
                self._disc.add(group_score)

            warnings_set = set(group_score.warnings or [])
            warnings_set.discard("INSUFFICIENT_FUNDAMENTAL_CONSTITUENTS")
            if fundamental_score_available_count < MIN_INDUSTRY_FUNDAMENTAL_COMPANIES:
                warnings_set.add("INSUFFICIENT_FUNDAMENTAL_CONSTITUENTS")

            calc_details = copy.deepcopy(group_score.calculation_details or {})
            if "fundamental" not in calc_details:
                calc_details["fundamental"] = {}

            calc_details["fundamental"]["raw_aggregation"] = {
                "constituent_count": constituent_count,
                "fundamental_score_available_count": fundamental_score_available_count,
                "fundamental_selection_eligible_count": fundamental_selection_eligible_count,
                "standard_debt_rule_applicable_count": standard_debt_rule_applicable_count,
                "standard_debt_rule_not_applicable_count": standard_debt_rule_not_applicable_count,
                "metrics": aggregated_metrics,
                "transitions": aggregated_transitions,
            }

            group_score.warnings = sorted(warnings_set)
            group_score.calculation_details = calc_details

        self._disc.commit()

    def _aggregate_basic_industry(
        self,
        run_id: str,
        horizon: str | None = None,
        parent_sector: str | None = None,
        parent_industry: str | None = None,
    ) -> None:
        query = self._disc.query(CompanyFundamentalMetric).filter_by(run_id=run_id)
        if parent_sector is not None:
            query = query.filter(CompanyFundamentalMetric.sector == parent_sector)
        if parent_industry is not None:
            query = query.filter(CompanyFundamentalMetric.industry == parent_industry)
        companies = query.all()
        if not companies:
            return

        basic_industries_map: Dict[tuple, List[CompanyFundamentalMetric]] = {}
        for company in companies:
            sec = company.sector
            ind = company.industry
            bi = company.basic_industry
            if not sec or not ind or not bi:
                continue
            key = (sec, ind, bi)
            basic_industries_map.setdefault(key, []).append(company)

        if not basic_industries_map:
            return

        group_records = self._disc.query(GroupScore).filter_by(
            run_id=run_id,
            entity_type="BASIC_INDUSTRY",
        )
        if horizon is not None:
            group_records = group_records.filter_by(horizon=horizon)
        existing = {}
        for group in group_records.all():
            existing[(group.parent_sector, group.parent_industry, group.entity_name)] = group

        for (sec, ind, bi), comps in basic_industries_map.items():
            constituent_count = len(comps)
            fundamental_score_available_count = 0
            fundamental_selection_eligible_count = 0
            standard_debt_rule_applicable_count = 0
            standard_debt_rule_not_applicable_count = 0

            metric_lists = {metric[0]: [] for metric in NORMAL_METRICS + DEBT_METRICS}
            net_profit_transitions: Dict[str, int] = {}
            borrowing_transitions: Dict[str, int] = {}
            cash_conversion_transitions: Dict[str, int] = {}

            for company in comps:
                if company.final_fundamental_score is not None:
                    fundamental_score_available_count += 1
                if company.fundamental_eligible_for_selection:
                    fundamental_selection_eligible_count += 1

                calc = company.calculation_details or {}
                fs_calc = calc.get("financial_strength", {})
                std_debt = fs_calc.get("standard_debt_rule_applicable", True)

                if std_debt:
                    standard_debt_rule_applicable_count += 1
                else:
                    standard_debt_rule_not_applicable_count += 1

                for metric_name, category, *sub_category in NORMAL_METRICS:
                    data = calc.get(category, {}).get(sub_category[0], {}) if sub_category else calc.get(category, {})
                    value = data.get(metric_name)
                    if _is_finite(value):
                        metric_lists[metric_name].append(value)

                if std_debt:
                    for metric_name, category in DEBT_METRICS:
                        data = calc.get(category, {})
                        value = data.get(metric_name)
                        if _is_finite(value):
                            metric_lists[metric_name].append(value)

                npt = calc.get("growth", {}).get("net_profit_transition")
                if npt:
                    net_profit_transitions[npt] = net_profit_transitions.get(npt, 0) + 1

                if std_debt:
                    bt = fs_calc.get("borrowing_transition")
                    if bt:
                        borrowing_transitions[bt] = borrowing_transitions.get(bt, 0) + 1

                cct = calc.get("earnings_quality", {}).get("cash_conversion", {}).get("latest_cash_conversion_status")
                if cct:
                    cash_conversion_transitions[cct] = cash_conversion_transitions.get(cct, 0) + 1

            metrics_res: Dict[str, Any] = {}
            for metric_name, *_ in NORMAL_METRICS:
                values = sorted(metric_lists[metric_name])
                valid_count = len(values)
                applicable_count = constituent_count
                coverage = 0.0
                if applicable_count > 0:
                    coverage = round((valid_count / applicable_count) * 100.0, 2)
                metrics_res[metric_name] = {
                    "median": _median(values),
                    "valid_count": valid_count,
                    "applicable_count": applicable_count,
                    "coverage_pct": coverage,
                    "reason": None,
                }

            for metric_name, *_ in DEBT_METRICS:
                values = sorted(metric_lists[metric_name])
                valid_count = len(values)
                applicable_count = standard_debt_rule_applicable_count
                if standard_debt_rule_applicable_count == 0:
                    metrics_res[metric_name] = {
                        "median": None,
                        "valid_count": 0,
                        "applicable_count": 0,
                        "coverage_pct": None,
                        "reason": "N_A_NO_STANDARD_DEBT_RULE_COMPANIES",
                    }
                else:
                    metrics_res[metric_name] = {
                        "median": _median(values),
                        "valid_count": valid_count,
                        "applicable_count": applicable_count,
                        "coverage_pct": round((valid_count / applicable_count) * 100.0, 2),
                        "reason": None,
                    }

            def _transition_result(counts_dict: Dict[str, int]) -> Dict[str, Any]:
                total = sum(counts_dict.values())
                percentages: Dict[str, float] = {}
                if total > 0:
                    for key, count in counts_dict.items():
                        percentages[key] = round((count / total) * 100.0, 2)
                return {
                    "valid_status_count": total,
                    "counts": counts_dict,
                    "percentages": percentages,
                }

            transitions_res = {
                "net_profit": _transition_result(net_profit_transitions),
                "borrowing": _transition_result(borrowing_transitions),
                "cash_conversion": _transition_result(cash_conversion_transitions),
            }

            group_score = existing.get((sec, ind, bi))
            if not group_score:
                group_score = GroupScore(
                    id=str(uuid.uuid4()),
                    run_id=run_id,
                    entity_type="BASIC_INDUSTRY",
                    entity_name=bi,
                    parent_sector=sec,
                    parent_industry=ind,
                )
                if horizon is not None:
                    group_score.horizon = horizon
                self._disc.add(group_score)

            calc_details = copy.deepcopy(group_score.calculation_details or {})
            if "fundamental" not in calc_details:
                calc_details["fundamental"] = {}

            calc_details["fundamental"]["raw_aggregation"] = {
                "constituent_count": constituent_count,
                "fundamental_score_available_count": fundamental_score_available_count,
                "fundamental_selection_eligible_count": fundamental_selection_eligible_count,
                "standard_debt_rule_applicable_count": standard_debt_rule_applicable_count,
                "standard_debt_rule_not_applicable_count": standard_debt_rule_not_applicable_count,
                "metrics": metrics_res,
                "transitions": transitions_res,
            }

            group_score.calculation_details = calc_details

            warnings_set = set(group_score.warnings or [])
            warnings_set.discard("INSUFFICIENT_FUNDAMENTAL_CONSTITUENTS")
            if fundamental_score_available_count < MIN_BASIC_INDUSTRY_FUNDAMENTAL_COMPANIES:
                warnings_set.add("INSUFFICIENT_FUNDAMENTAL_CONSTITUENTS")

            group_score.warnings = sorted(warnings_set)

        self._disc.commit()
