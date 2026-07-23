"""Orchestrate deterministic upstream discovery preparation."""
from __future__ import annotations

import datetime
import logging
import re
import uuid
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

from database import SourceSessionLocal
from models.discovery import (
    CompanyFundamentalMetric,
    CompanyTechnicalMetric,
    DiscoveryRun,
    EligibleUniverseSnapshot,
)
from services.fundamental.company_fundamental_score import CompanyFundamentalScoreService
from services.fundamental.fundamental_cash_conversion import FundamentalCashConversionService
from services.fundamental.fundamental_earnings_quality_score import FundamentalEarningsQualityScoreService
from services.fundamental.fundamental_financial_strength import FundamentalFinancialStrengthService
from services.fundamental.fundamental_financial_strength_score import (
    FundamentalFinancialStrengthScoreService,
)
from services.fundamental.fundamental_growth import FundamentalGrowthService
from services.fundamental.fundamental_growth_score import FundamentalGrowthScoreService
from services.fundamental.fundamental_peer_median import FundamentalPeerMedianService
from services.fundamental.fundamental_period_selection import FundamentalPeriodSelectionService
from services.fundamental.fundamental_profit_stability import FundamentalProfitStabilityService
from services.fundamental.fundamental_profitability import FundamentalProfitabilityService
from services.fundamental.fundamental_profitability_score import FundamentalProfitabilityScoreService
from services.technical.company_technical_score import CompanyTechnicalScoreService
from services.technical.technical_consistency import TechnicalConsistencyService
from services.technical.technical_date_alignment import TechnicalDateAlignmentService
from services.technical.technical_return import TechnicalReturnService
from services.technical.technical_volume import TechnicalVolumeService
from services.universe.universe_builder import UniverseBuilder


logger = logging.getLogger(__name__)

HORIZONS: Tuple[str, str, str] = ("SHORT", "MID", "LONG")

PREP_PENDING = "PENDING"
PREP_RUNNING = "RUNNING"
PREP_COMPLETED = "COMPLETED"
PREP_COMPLETED_WITH_WARNINGS = "COMPLETED_WITH_WARNINGS"
PREP_FAILED = "FAILED"

STAGE_RUNNING = "RUNNING"
STAGE_COMPLETED = "COMPLETED"
STAGE_COMPLETED_WITH_WARNINGS = "COMPLETED_WITH_WARNINGS"
STAGE_FAILED = "FAILED"
STAGE_SKIPPED = "SKIPPED"
STAGE_TERMINAL_SUCCESS = {STAGE_COMPLETED, STAGE_COMPLETED_WITH_WARNINGS}

E_RUN_NOT_FOUND = "DISCOVERY_RUN_NOT_FOUND"
E_ALREADY_RUNNING = "DISCOVERY_RUN_ALREADY_RUNNING"
E_AS_OF_UNAVAILABLE = "DISCOVERY_RUN_AS_OF_DATE_UNAVAILABLE"
E_SERVICE_UNAVAILABLE = "DISCOVERY_PREPARATION_SERVICE_UNAVAILABLE"
E_STAGE_FAILED = "DISCOVERY_PREPARATION_STAGE_FAILED"
E_VALIDATION_FAILED = "DISCOVERY_UPSTREAM_VALIDATION_FAILED"
E_STAGE_EXCEPTION = "DISCOVERY_PREPARATION_STAGE_EXCEPTION"
BENCHMARK_DATA_UNAVAILABLE = "BENCHMARK_DATA_UNAVAILABLE"
BENCHMARK_DATA_UNAVAILABLE_MESSAGE = (
    "NIFTY500 benchmark data is unavailable. Import genuine NIFTY500 "
    "benchmark candles before running discovery."
)

UNIVERSE_SNAPSHOT = "UNIVERSE_SNAPSHOT"
COMPANY_TECHNICAL = "COMPANY_TECHNICAL"
COMPANY_FUNDAMENTAL = "COMPANY_FUNDAMENTAL"
UPSTREAM_VALIDATION = "UPSTREAM_VALIDATION"

STAGE_ORDER: Tuple[str, ...] = (
    UNIVERSE_SNAPSHOT,
    COMPANY_TECHNICAL,
    COMPANY_FUNDAMENTAL,
    UPSTREAM_VALIDATION,
)
TECHNICAL_HORIZON_STAGES = {
    COMPANY_TECHNICAL,
}


class DiscoveryPreparationError(RuntimeError):
    def __init__(self, error_code: str, message: str):
        super().__init__(message)
        self.error_code = error_code


class DiscoveryUpstreamPreparationService:
    def __init__(
        self,
        discovery_session: Session,
        source_session: Optional[Session] = None,
        services: Optional[Dict[str, Any]] = None,
        horizons: Sequence[str] = HORIZONS,
        lock_enabled: bool = True,
    ):
        self._disc = discovery_session
        self._src = source_session
        self._owns_source_session = False
        self._services = services or {}
        self._horizons = tuple(horizons)
        self._lock_enabled = lock_enabled

    def prepare(
        self,
        run_id: str,
        resume: bool = True,
        force_restart: bool = False,
    ) -> Dict[str, Any]:
        logger.info("PREP | action=START | resume=%s | force_restart=%s", resume, force_restart)
        locked = self._acquire_lock(run_id)
        if not locked:
            logger.warning("PREP | action=BLOCKED | code=%s", E_ALREADY_RUNNING)
            return self._missing_result(run_id, E_ALREADY_RUNNING, "Discovery preparation is already running.")

        try:
            run = self._get_run(run_id)
            if run is None:
                logger.warning(
                    "PREP | action=FAIL | code=%s | message=\"Discovery run not found.\"",
                    E_RUN_NOT_FOUND,
                )
                return self._missing_result(run_id, E_RUN_NOT_FOUND, "Discovery run not found.")

            if self._macro_pipeline_running(run):
                logger.warning("PREP | action=BLOCKED | code=%s", E_ALREADY_RUNNING)
                return self._fail_validation(run, E_ALREADY_RUNNING, "Discovery run is already processing.")

            as_of_date = self._as_of_date(run)
            if as_of_date is None:
                logger.warning(
                    "PREP | action=FAIL | code=%s | message=\"Discovery run source data date is unavailable.\"",
                    E_AS_OF_UNAVAILABLE,
                )
                return self._fail_validation(
                    run,
                    E_AS_OF_UNAVAILABLE,
                    "Discovery run source data date is unavailable.",
                )

            if force_restart:
                self._reset_preparation(run)
            elif resume and self._is_resuming(run):
                run.preparation_resume_count = (run.preparation_resume_count or 0) + 1
                self._disc.commit()

            self._start_preparation(run)

            active_horizons: Set[str] = set(self._horizons)
            for stage in STAGE_ORDER:
                logger.info("PREP | stage=%s | status=START", stage)
                if stage == UNIVERSE_SNAPSHOT:
                    status = self._run_universe_stage(run, as_of_date)
                elif stage == COMPANY_TECHNICAL:
                    status, active_horizons = self._run_technical_company_stage(run)
                elif stage == UPSTREAM_VALIDATION:
                    status = self._run_validation_stage(run)
                else:
                    status = self._run_single_stage(run, stage)

                if status == STAGE_FAILED:
                    stage_error = (run.preparation_stage_results or {}).get(stage) or {}
                    code, message, horizons = _stage_failure_details(stage_error)
                    logger.warning(
                        "PREP | stage=%s | status=FAIL | code=%s | horizons=%s | message=\"%s\"",
                        stage,
                        code,
                        horizons,
                        message,
                    )
                    self._fail_preparation(
                        run,
                        code,
                        message,
                    )
                    self._mark_downstream_skipped(run, stage)
                    self._disc.commit()
                    return self._result(run)
                logger.info("PREP | stage=%s | status=%s", stage, status)

            self._complete_preparation(run)
            self._disc.commit()
            logger.info("PREP | status=DONE | result=%s", run.preparation_status)
            return self._result(run)
        finally:
            self._release_lock(run_id)
            self._close_owned_source_session()

    def _get_run(self, run_id: str) -> Optional[DiscoveryRun]:
        return (
            self._disc.query(DiscoveryRun)
            .filter(DiscoveryRun.id == run_id)
            .with_for_update()
            .first()
        )

    def _macro_pipeline_running(self, run: DiscoveryRun) -> bool:
        return run.status == PREP_RUNNING

    def _as_of_date(self, run: DiscoveryRun) -> Optional[datetime.date]:
        value = run.source_data_as_of or run.run_date
        if not value:
            return None
        if isinstance(value, datetime.date):
            return value
        try:
            return datetime.date.fromisoformat(str(value)[:10])
        except ValueError:
            return None

    def _reset_preparation(self, run: DiscoveryRun) -> None:
        run.preparation_status = PREP_PENDING
        run.preparation_current_stage = None
        run.preparation_last_completed_stage = None
        run.preparation_stage_results = {}
        run.preparation_warnings = []
        run.preparation_error_code = None
        run.preparation_error_message = None
        run.preparation_started_at = None
        run.preparation_completed_at = None
        run.preparation_resume_count = 0
        run.updated_at = datetime.datetime.utcnow()
        self._disc.commit()

    def _is_resuming(self, run: DiscoveryRun) -> bool:
        results = run.preparation_stage_results or {}
        return (
            run.preparation_status in {PREP_RUNNING, PREP_FAILED}
            or self._has_running_stage(results)
        )

    def _start_preparation(self, run: DiscoveryRun) -> None:
        now = datetime.datetime.utcnow()
        run.preparation_status = PREP_RUNNING
        run.preparation_started_at = run.preparation_started_at or now
        run.preparation_completed_at = None
        run.preparation_error_code = None
        run.preparation_error_message = None
        run.preparation_stage_results = run.preparation_stage_results or {}
        run.preparation_warnings = run.preparation_warnings or []
        run.updated_at = now
        self._disc.commit()

    def _run_universe_stage(self, run: DiscoveryRun, as_of_date: datetime.date) -> str:
        existing = (run.preparation_stage_results or {}).get(UNIVERSE_SNAPSHOT) or {}
        if existing.get("status") in STAGE_TERMINAL_SUCCESS:
            run.preparation_last_completed_stage = UNIVERSE_SNAPSHOT
            self._disc.commit()
            return existing["status"]

        self._set_stage_running(run, UNIVERSE_SNAPSHOT)
        try:
            builder = self._service(UNIVERSE_SNAPSHOT)
            counts: Dict[str, int] = {}
            for horizon in self._horizons:
                entries = self._call_builder(builder, horizon, as_of_date)
                counts[horizon] = self._persist_universe(run.id, horizon, as_of_date, entries)
            self._set_stage_finished(
                run,
                UNIVERSE_SNAPSHOT,
                {
                    "status": STAGE_COMPLETED,
                    "metadata": {"snapshots": counts},
                    "warnings": [],
                },
            )
            return STAGE_COMPLETED
        except Exception as exc:
            logger.error(
                "PREP | stage=%s | status=EXCEPTION | message=\"%s\"",
                UNIVERSE_SNAPSHOT,
                _safe_message(exc),
            )
            self._set_stage_exception(run, UNIVERSE_SNAPSHOT, exc)
            return STAGE_FAILED

    def _run_technical_company_stage(self, run: DiscoveryRun) -> Tuple[str, Set[str]]:
        return self._run_horizon_stage(
            run,
            COMPANY_TECHNICAL,
            set(self._horizons),
            lambda horizon: self._execute_technical_company_horizon(run.id, horizon),
        )

    def _run_horizon_stage(
        self,
        run: DiscoveryRun,
        stage: str,
        required_horizons: Set[str],
        executor: Callable[[str], Dict[str, Any]],
    ) -> Tuple[str, Set[str]]:
        results = dict(run.preparation_stage_results or {})
        parent = dict(results.get(stage) or {})
        horizons = dict(parent.get("horizons") or {})
        parent["started_at"] = parent.get("started_at") or _utc_now()
        parent["horizons"] = horizons
        parent["status"] = STAGE_RUNNING
        results[stage] = parent
        run.preparation_stage_results = results
        run.preparation_current_stage = stage
        self._disc.commit()

        success_horizons: Set[str] = set()
        for horizon in self._horizons:
            existing = horizons.get(horizon) or {}
            if horizon not in required_horizons:
                if existing.get("status") not in STAGE_TERMINAL_SUCCESS:
                    horizons[horizon] = self._skipped_result(
                        f"{stage}.{horizon} skipped because its prerequisite horizon failed."
                    )
                    self._save_horizon_parent(run, stage, parent)
                continue

            if existing.get("status") in STAGE_TERMINAL_SUCCESS:
                success_horizons.add(horizon)
                continue

            horizons[horizon] = {
                "status": STAGE_RUNNING,
                "started_at": _utc_now(),
                "completed_at": None,
                "warnings": [],
                "metadata": {},
            }
            self._save_horizon_parent(run, stage, parent)
            try:
                result = self._normalize_output(executor(horizon))
            except Exception as exc:
                result = self._exception_result(exc)
                logger.error(
                    "PREP | stage=%s | horizon=%s | status=EXCEPTION | code=%s | message=\"%s\"",
                    stage,
                    horizon,
                    result.get("error_code") or E_STAGE_EXCEPTION,
                    _safe_message(result.get("error_message") or exc),
                )
            horizons[horizon] = result
            if result["status"] in STAGE_TERMINAL_SUCCESS:
                success_horizons.add(horizon)
            self._save_horizon_parent(run, stage, parent)

        parent_status = self._horizon_parent_status(horizons, required_horizons)
        parent["status"] = parent_status
        parent["completed_at"] = _utc_now()
        parent["warnings"] = self._collect_horizon_warnings(horizons)
        parent["metadata"] = {
            "required_horizons": sorted(required_horizons),
            "completed_horizons": sorted(success_horizons),
            "failed_horizons": sorted(
                horizon
                for horizon in required_horizons
                if (horizons.get(horizon) or {}).get("status") == STAGE_FAILED
            ),
            "skipped_horizons": sorted(
                horizon
                for horizon, result in horizons.items()
                if result.get("status") == STAGE_SKIPPED
            ),
        }
        if parent_status == STAGE_FAILED:
            code, message, _ = _horizon_failure_details(stage, horizons, required_horizons)
            parent["error_code"] = code
            parent["error_message"] = message
        self._save_horizon_parent(run, stage, parent)
        if parent_status in STAGE_TERMINAL_SUCCESS:
            run.preparation_last_completed_stage = stage
        self._aggregate_warnings(run)
        self._disc.commit()
        return parent_status, success_horizons

    def _execute_technical_company_horizon(self, run_id: str, horizon: str) -> Dict[str, Any]:
        aligner = self._service("TECHNICAL_DATE_ALIGNMENT")
        alignment = aligner.align(horizon)
        alignment_status = getattr(alignment, "status", None) or (
            alignment.get("status") if isinstance(alignment, dict) else None
        )
        if alignment_status != "READY":
            code = alignment_status or E_STAGE_FAILED
            return {
                "status": STAGE_FAILED,
                "error_code": code,
                "error_message": _error_message_for_code(code),
                "warnings": [code],
            }

        self._service("TECHNICAL_RETURN").calculate_and_save_returns(run_id, alignment)
        self._service("TECHNICAL_VOLUME").calculate_and_save_volumes(run_id, horizon)
        self._service("TECHNICAL_CONSISTENCY").calculate_and_save_consistency(run_id, horizon)
        self._service("COMPANY_TECHNICAL_SCORE").score_companies(run_id, horizon)
        return {"status": STAGE_COMPLETED, "metadata": {"horizon": horizon}}

    def _run_single_stage(self, run: DiscoveryRun, stage: str) -> str:
        existing = (run.preparation_stage_results or {}).get(stage) or {}
        if existing.get("status") in STAGE_TERMINAL_SUCCESS:
            run.preparation_last_completed_stage = stage
            self._disc.commit()
            return existing["status"]

        self._set_stage_running(run, stage)
        try:
            output = self._execute_single_stage(stage, run.id)
            result = self._normalize_output(output)
            self._set_stage_finished(run, stage, result, failed=result["status"] == STAGE_FAILED)
            return result["status"]
        except Exception as exc:
            logger.error("PREP | stage=%s | status=EXCEPTION | message=\"%s\"", stage, _safe_message(exc))
            self._set_stage_exception(run, stage, exc)
            return STAGE_FAILED

    def _execute_single_stage(self, stage: str, run_id: str) -> Dict[str, Any]:
        if stage == COMPANY_FUNDAMENTAL:
            self._service("FUNDAMENTAL_PERIOD_SELECTION").select_periods()
            self._service("FUNDAMENTAL_GROWTH").calculate_growth(run_id)
            self._service("FUNDAMENTAL_PROFITABILITY").calculate_profitability(run_id)
            self._service("FUNDAMENTAL_FINANCIAL_STRENGTH").calculate_financial_strength(run_id)
            self._service("FUNDAMENTAL_CASH_CONVERSION").calculate_cash_conversion(run_id)
            self._service("FUNDAMENTAL_PROFIT_STABILITY").calculate_profit_stability(run_id)
            self._service("FUNDAMENTAL_PEER_MEDIAN").resolve_peer_medians(run_id)
            self._service("FUNDAMENTAL_GROWTH_SCORE").score_growth(run_id)
            self._service("FUNDAMENTAL_PROFITABILITY_SCORE").score_profitability(run_id)
            self._service("FUNDAMENTAL_FINANCIAL_STRENGTH_SCORE").score_financial_strength(run_id)
            self._service("FUNDAMENTAL_EARNINGS_QUALITY_SCORE").score_earnings_quality(run_id)
            self._service("COMPANY_FUNDAMENTAL_SCORE").score_companies(run_id)
        else:
            raise DiscoveryPreparationError(E_STAGE_FAILED, f"Unknown preparation stage {stage}.")
        return {"status": STAGE_COMPLETED, "metadata": {"stage": stage}}

    def _run_validation_stage(self, run: DiscoveryRun) -> str:
        existing = (run.preparation_stage_results or {}).get(UPSTREAM_VALIDATION) or {}
        if existing.get("status") in STAGE_TERMINAL_SUCCESS:
            run.preparation_last_completed_stage = UPSTREAM_VALIDATION
            self._disc.commit()
            return existing["status"]

        self._set_stage_running(run, UPSTREAM_VALIDATION)
        details = self._validation_counts(run.id, self._successful_technical_horizons(run))
        missing = details["missing"]
        if missing:
            self._set_stage_finished(
                run,
                UPSTREAM_VALIDATION,
                {
                    "status": STAGE_FAILED,
                    "warnings": [E_VALIDATION_FAILED],
                    "metadata": details,
                    "error_code": E_VALIDATION_FAILED,
                    "error_message": "Required upstream preparation records are unavailable.",
                },
                failed=True,
            )
            return STAGE_FAILED

        self._set_stage_finished(
            run,
            UPSTREAM_VALIDATION,
            {
                "status": STAGE_COMPLETED,
                "warnings": [],
                "metadata": details,
            },
        )
        return STAGE_COMPLETED

    def _validation_counts(self, run_id: str, horizons: Set[str]) -> Dict[str, Any]:
        missing: List[str] = []
        details: Dict[str, Any] = {"horizons": {}, "missing": missing}
        universe_count = self._disc.query(EligibleUniverseSnapshot).filter_by(
            run_id=run_id
        ).count()
        details["eligible_universe_snapshots"] = {"count": universe_count}
        if universe_count == 0:
            missing.append("eligible_universe_snapshots")

        company_fundamental_count = self._disc.query(CompanyFundamentalMetric).filter_by(
            run_id=run_id
        ).count()
        final_company_fundamental_count = (
            self._disc.query(CompanyFundamentalMetric)
            .filter(
                CompanyFundamentalMetric.run_id == run_id,
                CompanyFundamentalMetric.final_fundamental_score.isnot(None),
            )
            .count()
        )
        details["company_fundamental_metrics"] = {
            "count": company_fundamental_count,
            "final_score_count": final_company_fundamental_count,
        }
        if company_fundamental_count == 0:
            missing.append("company_fundamental_metrics")

        for horizon in sorted(horizons):
            horizon_details: Dict[str, Any] = {}
            technical_count = (
                self._disc.query(CompanyTechnicalMetric)
                .filter_by(run_id=run_id, horizon=horizon)
                .count()
            )
            horizon_details["company_technical_metrics"] = technical_count
            if technical_count == 0:
                missing.append(f"company_technical_metrics.{horizon}")

            technical_final_score_count = (
                self._disc.query(CompanyTechnicalMetric)
                .filter(
                    CompanyTechnicalMetric.run_id == run_id,
                    CompanyTechnicalMetric.horizon == horizon,
                    CompanyTechnicalMetric.final_technical_score.isnot(None),
                )
                .count()
            )
            horizon_details["company_technical_final_scores"] = technical_final_score_count
            details["horizons"][horizon] = horizon_details

        details["missing"] = sorted(set(missing))
        return details

    def _successful_technical_horizons(self, run: DiscoveryRun) -> Set[str]:
        parent = (run.preparation_stage_results or {}).get(COMPANY_TECHNICAL) or {}
        horizons = parent.get("horizons") or {}
        return {
            horizon
            for horizon in self._horizons
            if (horizons.get(horizon) or {}).get("status") in STAGE_TERMINAL_SUCCESS
        }

    def _persist_universe(
        self,
        run_id: str,
        horizon: str,
        as_of_date: datetime.date,
        entries: Iterable[Dict[str, Any]],
    ) -> int:
        from sqlalchemy import text
        # Bulk delete existing to handle resumes safely
        self._disc.execute(
            text("DELETE FROM eligible_universe_snapshots WHERE run_id = :r AND horizon = :h"),
            {"r": run_id, "h": horizon},
        )
        
        insert_data = []
        for entry in entries:
            source_company_id = str(entry.get("source_company_id") or entry.get("company_id") or "")
            if not source_company_id:
                continue
                
            insert_data.append({
                "id": str(uuid.uuid4()),
                "run_id": run_id,
                "horizon": horizon,
                "source_company_id": source_company_id,
                "as_of_date": entry.get("as_of_date") or as_of_date,
                "symbol": entry.get("symbol") or "",
                "sector": entry.get("sector") or "",
                "industry": entry.get("industry") or "",
                "basic_industry": entry.get("basic_industry"),
                "market_cap": entry.get("market_cap"),
                "return_available": bool(entry.get("return_available")),
                "volume_available": bool(entry.get("volume_available")),
                "consistency_available": bool(entry.get("consistency_available")),
                "financial_data_available": bool(entry.get("financial_data_available")),
                "technical_data_coverage": float(entry.get("technical_data_coverage") or 0.0),
                "fundamental_data_coverage": float(entry.get("fundamental_data_coverage") or 0.0),
                "eligible_for_sector": bool(entry.get("eligible_for_sector")),
                "eligible_for_industry": bool(entry.get("eligible_for_industry")),
                "eligible_for_basic_industry": bool(entry.get("eligible_for_basic_industry")),
                "exclusion_reasons": list(entry.get("exclusion_reasons") or [])
            })
            
        if insert_data:
            self._disc.execute(
                EligibleUniverseSnapshot.__table__.insert(),
                insert_data
            )
            
        self._disc.commit()
        return len(insert_data)

    def _set_stage_running(self, run: DiscoveryRun, stage: str) -> None:
        results = dict(run.preparation_stage_results or {})
        results[stage] = {
            "status": STAGE_RUNNING,
            "started_at": _utc_now(),
            "completed_at": None,
            "warnings": [],
            "metadata": {},
        }
        run.preparation_stage_results = results
        run.preparation_current_stage = stage
        run.updated_at = datetime.datetime.utcnow()
        self._disc.commit()

    def _set_stage_finished(
        self,
        run: DiscoveryRun,
        stage: str,
        result: Dict[str, Any],
        failed: bool = False,
    ) -> None:
        results = dict(run.preparation_stage_results or {})
        previous = dict(results.get(stage) or {})
        previous.update(result)
        previous["completed_at"] = previous.get("completed_at") or _utc_now()
        results[stage] = previous
        run.preparation_stage_results = results
        if not failed:
            run.preparation_last_completed_stage = stage
        self._aggregate_warnings(run)
        run.updated_at = datetime.datetime.utcnow()
        self._disc.commit()

    def _set_stage_exception(self, run: DiscoveryRun, stage: str, exc: Exception) -> None:
        results = dict(run.preparation_stage_results or {})
        previous = dict(results.get(stage) or {})
        previous.update(self._exception_result(exc))
        results[stage] = previous
        run.preparation_stage_results = results
        self._aggregate_warnings(run)
        run.updated_at = datetime.datetime.utcnow()
        self._disc.commit()

    def _save_horizon_parent(self, run: DiscoveryRun, stage: str, parent: Dict[str, Any]) -> None:
        results = dict(run.preparation_stage_results or {})
        results[stage] = parent
        run.preparation_stage_results = results
        run.preparation_current_stage = stage
        run.updated_at = datetime.datetime.utcnow()
        self._aggregate_warnings(run)
        self._disc.commit()

    def _mark_downstream_skipped(self, run: DiscoveryRun, failed_stage: str) -> None:
        results = dict(run.preparation_stage_results or {})
        start_skipping = False
        for stage in STAGE_ORDER:
            if stage == failed_stage:
                start_skipping = True
                continue
            if not start_skipping:
                continue
            existing = results.get(stage) or {}
            if existing.get("status") in STAGE_TERMINAL_SUCCESS or existing.get("status") == STAGE_FAILED:
                continue
            if stage in TECHNICAL_HORIZON_STAGES:
                horizons = dict(existing.get("horizons") or {})
                for horizon in self._horizons:
                    result = horizons.get(horizon) or {}
                    if result.get("status") not in STAGE_TERMINAL_SUCCESS:
                        horizons[horizon] = self._skipped_result(
                            f"{stage}.{horizon} skipped after upstream preparation failure."
                        )
                existing = dict(existing)
                existing["status"] = STAGE_SKIPPED
                existing["completed_at"] = _utc_now()
                existing["horizons"] = horizons
                existing["warnings"] = []
                existing["metadata"] = existing.get("metadata") or {}
                results[stage] = existing
            else:
                results[stage] = self._skipped_result(
                    f"{stage} skipped after upstream preparation failure."
                )
        run.preparation_stage_results = results
        self._aggregate_warnings(run)
        run.updated_at = datetime.datetime.utcnow()

    def _fail_preparation(self, run: DiscoveryRun, error_code: str, message: str) -> None:
        run.preparation_status = PREP_FAILED
        run.preparation_current_stage = None
        run.preparation_completed_at = datetime.datetime.utcnow()
        run.preparation_error_code = error_code
        run.preparation_error_message = _safe_message(message)
        self._aggregate_warnings(run)
        run.updated_at = datetime.datetime.utcnow()

    def _fail_validation(self, run: DiscoveryRun, error_code: str, message: str) -> Dict[str, Any]:
        self._start_preparation(run)
        self._fail_preparation(run, error_code, message)
        self._disc.commit()
        return self._result(run)

    def _complete_preparation(self, run: DiscoveryRun) -> None:
        self._aggregate_warnings(run)
        run.preparation_status = (
            PREP_COMPLETED_WITH_WARNINGS if run.preparation_warnings else PREP_COMPLETED
        )
        run.preparation_current_stage = None
        run.preparation_last_completed_stage = UPSTREAM_VALIDATION
        run.preparation_completed_at = datetime.datetime.utcnow()
        run.preparation_error_code = None
        run.preparation_error_message = None
        run.updated_at = datetime.datetime.utcnow()

    def _normalize_output(self, output: Any) -> Dict[str, Any]:
        started_at = _utc_now()
        completed_at = _utc_now()
        if isinstance(output, dict):
            warnings = _clean_warnings(output.get("warnings") or [])
            raw_status = output.get("status")
            if raw_status == STAGE_FAILED:
                status = STAGE_FAILED
            elif raw_status in STAGE_TERMINAL_SUCCESS:
                status = raw_status
            else:
                status = STAGE_COMPLETED_WITH_WARNINGS if warnings else STAGE_COMPLETED
            metadata = output.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {
                    key: value
                    for key, value in output.items()
                    if key not in {"status", "warnings", "error_code", "error_message"}
                    and _is_safe_metadata_value(value)
                }
            result = {
                "status": status,
                "started_at": started_at,
                "completed_at": completed_at,
                "warnings": warnings,
                "metadata": metadata,
            }
            if status == STAGE_FAILED:
                result["error_code"] = output.get("error_code") or E_STAGE_FAILED
                result["error_message"] = _safe_message(
                    output.get("error_message") or "Preparation stage failed."
                )
            return result
        return {
            "status": STAGE_COMPLETED,
            "started_at": started_at,
            "completed_at": completed_at,
            "warnings": [],
            "metadata": {},
        }

    def _exception_result(self, exc: Exception) -> Dict[str, Any]:
        code = getattr(exc, "error_code", E_STAGE_EXCEPTION)
        return {
            "status": STAGE_FAILED,
            "started_at": _utc_now(),
            "completed_at": _utc_now(),
            "warnings": [code],
            "metadata": {},
            "error_code": code,
            "error_message": _safe_message(exc),
        }

    def _skipped_result(self, message: str) -> Dict[str, Any]:
        return {
            "status": STAGE_SKIPPED,
            "started_at": None,
            "completed_at": _utc_now(),
            "warnings": [],
            "metadata": {},
            "error_code": None,
            "error_message": message,
        }

    def _horizon_parent_status(
        self,
        horizons: Dict[str, Dict[str, Any]],
        required_horizons: Set[str],
    ) -> str:
        if not required_horizons:
            return STAGE_SKIPPED
        statuses = [(horizons.get(horizon) or {}).get("status") for horizon in required_horizons]
        successes = [status for status in statuses if status in STAGE_TERMINAL_SUCCESS]
        if not successes:
            return STAGE_FAILED
        if any(status != STAGE_COMPLETED for status in statuses):
            return STAGE_COMPLETED_WITH_WARNINGS
        if self._collect_horizon_warnings(horizons):
            return STAGE_COMPLETED_WITH_WARNINGS
        return STAGE_COMPLETED

    def _collect_horizon_warnings(self, horizons: Dict[str, Dict[str, Any]]) -> List[str]:
        warnings: List[str] = []
        for result in horizons.values():
            warnings.extend(result.get("warnings") or [])
            if result.get("status") == STAGE_FAILED:
                warnings.append(result.get("error_code") or E_STAGE_FAILED)
        return _clean_warnings(warnings)

    def _aggregate_warnings(self, run: DiscoveryRun) -> None:
        warnings: List[str] = []
        for stage_result in (run.preparation_stage_results or {}).values():
            if not isinstance(stage_result, dict):
                continue
            warnings.extend(stage_result.get("warnings") or [])
            for horizon_result in (stage_result.get("horizons") or {}).values():
                warnings.extend(horizon_result.get("warnings") or [])
        run.preparation_warnings = _clean_warnings(warnings)

    def _result(self, run: DiscoveryRun) -> Dict[str, Any]:
        self._disc.refresh(run)
        error = None
        if run.preparation_error_code or run.preparation_error_message:
            error = {
                "code": run.preparation_error_code,
                "message": _safe_message(run.preparation_error_message),
            }
        return {
            "run_id": run.id,
            "status": run.preparation_status,
            "preparation_status": run.preparation_status,
            "last_completed_stage": run.preparation_last_completed_stage,
            "resume_count": run.preparation_resume_count or 0,
            "stage_results": run.preparation_stage_results or {},
            "warnings": run.preparation_warnings or [],
            "error": error,
            "error_code": run.preparation_error_code,
            "error_message": run.preparation_error_message,
        }

    def _missing_result(self, run_id: str, error_code: str, message: str) -> Dict[str, Any]:
        return {
            "run_id": run_id,
            "status": PREP_FAILED,
            "preparation_status": PREP_FAILED,
            "last_completed_stage": None,
            "resume_count": 0,
            "stage_results": {},
            "warnings": [error_code],
            "error": {"code": error_code, "message": message},
            "error_code": error_code,
            "error_message": message,
        }

    def _service(self, name: str) -> Any:
        service = self._services.get(name)
        if service is not None:
            return service

        factory = self._default_service_factories().get(name)
        if factory is None:
            raise DiscoveryPreparationError(
                E_SERVICE_UNAVAILABLE,
                f"Required preparation service is unavailable: {name}.",
            )
        service = factory()
        self._services[name] = service
        return service

    def _default_service_factories(self) -> Dict[str, Callable[[], Any]]:
        return {
            UNIVERSE_SNAPSHOT: lambda: UniverseBuilder(self._source_session()),
            "TECHNICAL_DATE_ALIGNMENT": lambda: TechnicalDateAlignmentService(
                self._source_session(), self._disc
            ),
            "TECHNICAL_RETURN": lambda: TechnicalReturnService(self._source_session(), self._disc),
            "TECHNICAL_VOLUME": lambda: TechnicalVolumeService(self._source_session(), self._disc),
            "TECHNICAL_CONSISTENCY": lambda: TechnicalConsistencyService(
                self._source_session(), self._disc
            ),
            "COMPANY_TECHNICAL_SCORE": lambda: CompanyTechnicalScoreService(self._disc),
            "FUNDAMENTAL_PERIOD_SELECTION": lambda: FundamentalPeriodSelectionService(
                self._source_session()
            ),
            "FUNDAMENTAL_GROWTH": lambda: FundamentalGrowthService(
                self._source_session(), self._disc
            ),
            "FUNDAMENTAL_PROFITABILITY": lambda: FundamentalProfitabilityService(
                self._source_session(), self._disc
            ),
            "FUNDAMENTAL_FINANCIAL_STRENGTH": lambda: FundamentalFinancialStrengthService(
                self._source_session(), self._disc
            ),
            "FUNDAMENTAL_CASH_CONVERSION": lambda: FundamentalCashConversionService(
                self._source_session(), self._disc
            ),
            "FUNDAMENTAL_PROFIT_STABILITY": lambda: FundamentalProfitStabilityService(
                self._source_session(), self._disc
            ),
            "FUNDAMENTAL_PEER_MEDIAN": lambda: FundamentalPeerMedianService(self._disc),
            "FUNDAMENTAL_GROWTH_SCORE": lambda: FundamentalGrowthScoreService(self._disc),
            "FUNDAMENTAL_PROFITABILITY_SCORE": lambda: FundamentalProfitabilityScoreService(
                self._disc
            ),
            "FUNDAMENTAL_FINANCIAL_STRENGTH_SCORE": lambda: FundamentalFinancialStrengthScoreService(
                self._disc
            ),
            "FUNDAMENTAL_EARNINGS_QUALITY_SCORE": lambda: FundamentalEarningsQualityScoreService(
                self._disc
            ),
            "COMPANY_FUNDAMENTAL_SCORE": lambda: CompanyFundamentalScoreService(self._disc),
        }

    def _source_session(self) -> Session:
        if self._src is None:
            self._src = SourceSessionLocal()
            self._owns_source_session = True
        return self._src

    def _close_owned_source_session(self) -> None:
        if self._owns_source_session and self._src is not None:
            self._src.close()
            self._src = None
            self._owns_source_session = False

    def _call_builder(
        self,
        builder: Any,
        horizon: str,
        as_of_date: datetime.date,
    ) -> Iterable[Dict[str, Any]]:
        if hasattr(builder, "build"):
            return builder.build(horizon, as_of_date=as_of_date)
        if callable(builder):
            return builder(horizon, as_of_date)
        raise DiscoveryPreparationError(
            E_SERVICE_UNAVAILABLE,
            "Required preparation service is unavailable: UNIVERSE_SNAPSHOT.",
        )

    def _has_running_stage(self, stage_results: Dict[str, Any]) -> bool:
        for result in stage_results.values():
            if not isinstance(result, dict):
                continue
            if result.get("status") == STAGE_RUNNING:
                return True
            for horizon_result in (result.get("horizons") or {}).values():
                if horizon_result.get("status") == STAGE_RUNNING:
                    return True
        return False

    def _acquire_lock(self, run_id: str) -> bool:
        if not self._lock_enabled:
            return True
        return bool(
            self._disc.execute(
                text("SELECT pg_try_advisory_lock(hashtext(:key))"),
                {"key": self._lock_key(run_id)},
            ).scalar()
        )

    def _release_lock(self, run_id: str) -> None:
        if not self._lock_enabled:
            return
        try:
            self._disc.execute(
                text("SELECT pg_advisory_unlock(hashtext(:key))"),
                {"key": self._lock_key(run_id)},
            )
            self._disc.commit()
        except Exception:
            self._disc.rollback()

    def _lock_key(self, run_id: str) -> str:
        return f"discovery_preparation:{run_id}"


def _utc_now() -> str:
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _clean_warnings(warnings: Iterable[Any]) -> List[str]:
    return sorted({str(warning) for warning in warnings if warning})


def _safe_message(value: Any) -> str:
    text_value = str(value or "")
    first_line = text_value.splitlines()[0] if text_value.splitlines() else text_value
    first_line = re.sub(
        r"(?i)(authorization|api[-_ ]?key|token|secret)\s*[:=]\s*(?:bearer\s+)?\S+",
        r"\1: [REDACTED]",
        first_line,
    )
    first_line = re.sub(r"(?i)bearer\s+[a-z0-9._\-]+", "Bearer [REDACTED]", first_line)
    first_line = re.sub(r"(?i)(postgresql|mysql|sqlite)://\S+", "[REDACTED_DSN]", first_line)
    return first_line[:300]


def _error_message_for_code(code: str) -> str:
    if code == BENCHMARK_DATA_UNAVAILABLE:
        return BENCHMARK_DATA_UNAVAILABLE_MESSAGE
    return code


def _horizon_failure_details(
    stage: str,
    horizons: Dict[str, Dict[str, Any]],
    required_horizons: Set[str],
) -> Tuple[str, str, str]:
    failed = [
        horizons.get(horizon) or {}
        for horizon in sorted(required_horizons)
        if (horizons.get(horizon) or {}).get("status") == STAGE_FAILED
    ]
    codes = sorted({str(result.get("error_code") or E_STAGE_FAILED) for result in failed})
    messages = sorted(
        {
            _safe_message(result.get("error_message") or _error_message_for_code(result.get("error_code") or E_STAGE_FAILED))
            for result in failed
        }
    )
    failed_horizons = ",".join(
        horizon
        for horizon in sorted(required_horizons)
        if (horizons.get(horizon) or {}).get("status") == STAGE_FAILED
    )
    if len(codes) == 1:
        return codes[0], messages[0] if messages else _error_message_for_code(codes[0]), failed_horizons
    return E_STAGE_FAILED, f"{stage} failed for every required horizon.", failed_horizons


def _stage_failure_details(stage_result: Dict[str, Any]) -> Tuple[str, str, str]:
    horizons = stage_result.get("horizons") or {}
    failed_horizons = ",".join(
        horizon
        for horizon, result in sorted(horizons.items())
        if isinstance(result, dict) and result.get("status") == STAGE_FAILED
    )
    code = stage_result.get("error_code") or E_STAGE_FAILED
    message = _safe_message(stage_result.get("error_message") or _error_message_for_code(code))
    return code, message, failed_horizons or "-"


def _is_safe_metadata_value(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool, list, tuple, dict))
