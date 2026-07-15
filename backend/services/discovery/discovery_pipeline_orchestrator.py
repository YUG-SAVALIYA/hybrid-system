"""Resumable orchestration for the discovery pipeline."""
from __future__ import annotations

import datetime
import re
import logging
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

from sqlalchemy import text
from sqlalchemy.orm import Session

from models.discovery import (
    CompanyFundamentalMetric,
    CompanyTechnicalMetric,
    DiscoveryRun,
    DiscoverySelection,
)


HORIZONS: Tuple[str, str, str] = ("SHORT", "MID", "LONG")

RUN_PENDING = "PENDING"
RUN_RUNNING = "RUNNING"
RUN_COMPLETED = "COMPLETED"
RUN_COMPLETED_WITH_WARNINGS = "COMPLETED_WITH_WARNINGS"
RUN_FAILED = "FAILED"
RUN_STATUSES = {
    RUN_PENDING,
    RUN_RUNNING,
    RUN_COMPLETED,
    RUN_COMPLETED_WITH_WARNINGS,
    RUN_FAILED,
}

STAGE_PENDING = "PENDING"
STAGE_RUNNING = "RUNNING"
STAGE_COMPLETED = "COMPLETED"
STAGE_COMPLETED_WITH_WARNINGS = "COMPLETED_WITH_WARNINGS"
STAGE_FAILED = "FAILED"
STAGE_SKIPPED = "SKIPPED"
STAGE_TERMINAL_SUCCESS = {STAGE_COMPLETED, STAGE_COMPLETED_WITH_WARNINGS}

E_UPSTREAM_UNAVAILABLE = "DISCOVERY_UPSTREAM_DATA_UNAVAILABLE"
E_RUN_NOT_FOUND = "DISCOVERY_RUN_NOT_FOUND"
E_ALREADY_RUNNING = "DISCOVERY_RUN_ALREADY_RUNNING"
E_STAGE_FAILED = "DISCOVERY_STAGE_FAILED"
E_STAGE_EXCEPTION = "DISCOVERY_STAGE_EXCEPTION"
E_SERVICE_NOT_CONFIGURED = "DISCOVERY_SERVICE_NOT_CONFIGURED"

MACRO_SEARCH = "MACRO_SEARCH"
MACRO_FILTER = "MACRO_FILTER"
SECTOR_SELECTION = "SECTOR_SELECTION"
INDUSTRY_SELECTION = "INDUSTRY_SELECTION"
BASIC_INDUSTRY_SELECTION = "BASIC_INDUSTRY_SELECTION"
STOCK_SELECTION = "STOCK_SELECTION"

STAGE_ORDER: Tuple[str, ...] = (
    MACRO_SEARCH,
    MACRO_FILTER,
    SECTOR_SELECTION,
    INDUSTRY_SELECTION,
    BASIC_INDUSTRY_SELECTION,
    STOCK_SELECTION,
)

HORIZON_STAGES = {
    SECTOR_SELECTION,
    INDUSTRY_SELECTION,
    BASIC_INDUSTRY_SELECTION,
    STOCK_SELECTION,
}

METHOD_BY_STAGE = {
    MACRO_SEARCH: "fetch_macro_data",
    MACRO_FILTER: "generate_macro_filter_summary",
    SECTOR_SELECTION: "run",
    INDUSTRY_SELECTION: "run",
    BASIC_INDUSTRY_SELECTION: "run",
    STOCK_SELECTION: "run",
}

SELECTION_BY_STAGE = {
    SECTOR_SELECTION: "SECTOR",
    INDUSTRY_SELECTION: "INDUSTRY",
    BASIC_INDUSTRY_SELECTION: "BASIC_INDUSTRY",
}


class DiscoveryPipelineError(RuntimeError):
    """Raised after an unexpected stage exception has been persisted."""

    def __init__(self, error_code: str, message: str):
        super().__init__(message)
        self.error_code = error_code


class DiscoveryPipelineOrchestrator:
    def __init__(
        self,
        discovery_session: Session,
        services: Optional[Dict[str, Any]] = None,
        horizons: Sequence[str] = HORIZONS,
        lock_enabled: bool = True,
    ):
        self._disc = discovery_session
        self._services = services or {}
        self._horizons = tuple(horizons)
        self._lock_enabled = lock_enabled

    def execute(
        self,
        run_id: str,
        resume: bool = True,
        force_restart: bool = False,
        target_horizon: Optional[str] = None,
    ) -> Dict[str, Any]:
        locked = self._acquire_lock(run_id)
        if not locked:
            return {
                "run_id": run_id,
                "status": RUN_FAILED,
                "error_code": E_ALREADY_RUNNING,
                "error_message": "Discovery run is already processing.",
                "warnings": [E_ALREADY_RUNNING],
            }

        try:
            run = self._get_run(run_id)
            if run is None:
                return {
                    "run_id": run_id,
                    "status": RUN_FAILED,
                    "last_completed_stage": None,
                    "resume_count": 0,
                    "horizons": {horizon: {} for horizon in self._horizons},
                    "stage_results": {},
                    "warnings": [E_RUN_NOT_FOUND],
                    "error": {
                        "code": E_RUN_NOT_FOUND,
                        "message": "Discovery run not found.",
                    },
                    "error_code": E_RUN_NOT_FOUND,
                    "error_message": "Discovery run not found.",
                }

            if run.status == RUN_COMPLETED and not force_restart:
                return self._result(run)

            if force_restart:
                self._reset_run(run)
            elif resume and self._is_resuming(run):
                run.resume_count = (run.resume_count or 0) + 1

            self._start_run(run)
            if not self._validate_prerequisites(run):
                self._fail_run(
                    run,
                    E_UPSTREAM_UNAVAILABLE,
                    "Required upstream discovery data is unavailable.",
                )
                self._mark_downstream_skipped(run, None)
                self._disc.commit()
                return self._result(run)

            if target_horizon and target_horizon in self._horizons:
                active_horizons: Set[str] = {target_horizon}
            else:
                active_horizons: Set[str] = set(self._horizons)
                
            success_horizons: Set[str] = set()

            for stage in STAGE_ORDER:
                if stage in HORIZON_STAGES:
                    required = self._required_horizons(stage, active_horizons)
                    parent_status, success_horizons = self._run_horizon_stage(
                        run, stage, required
                    )
                    if parent_status == STAGE_FAILED:
                        self._fail_run(
                            run,
                            E_STAGE_FAILED,
                            f"{stage} failed for every required horizon.",
                        )
                        self._mark_downstream_skipped(run, stage)
                        self._disc.commit()
                        return self._result(run)
                    active_horizons = self._next_active_horizons(run.id, stage, success_horizons)
                    if stage != STOCK_SELECTION and not active_horizons:
                        self._fail_run(
                            run,
                            E_STAGE_FAILED,
                            f"{stage} produced no usable downstream horizons.",
                        )
                        self._mark_downstream_skipped(run, stage)
                        self._disc.commit()
                        return self._result(run)
                    continue

                status = self._run_single_stage(run, stage)
                if status == STAGE_FAILED:
                    self._fail_run(run, E_STAGE_FAILED, f"{stage} failed.")
                    self._mark_downstream_skipped(run, stage)
                    self._disc.commit()
                    return self._result(run)

            self._complete_run(run)
            self._disc.commit()
            return self._result(run)
        finally:
            self._release_lock(run_id)

    def _get_run(self, run_id: str) -> Optional[DiscoveryRun]:
        return (
            self._disc.query(DiscoveryRun)
            .filter(DiscoveryRun.id == run_id)
            .with_for_update()
            .first()
        )

    def _is_resuming(self, run: DiscoveryRun) -> bool:
        return run.status in {RUN_RUNNING, RUN_FAILED} or self._has_incomplete_running_stage(
            run.stage_results or {}
        )

    def _reset_run(self, run: DiscoveryRun) -> None:
        run.status = RUN_PENDING
        run.current_stage = None
        run.last_completed_stage = None
        run.stage_results = {}
        run.warnings = []
        run.error_code = None
        run.error_message = None
        run.started_at = None
        run.completed_at = None
        run.resume_count = 0
        run.updated_at = datetime.datetime.utcnow()
        self._disc.commit()

    def _start_run(self, run: DiscoveryRun) -> None:
        now = datetime.datetime.utcnow()
        run.status = RUN_RUNNING
        run.started_at = run.started_at or now
        run.completed_at = None
        run.error_code = None
        run.error_message = None
        run.stage_results = run.stage_results or {}
        run.warnings = run.warnings or []
        run.updated_at = now
        self._disc.commit()

    def _validate_prerequisites(self, run: DiscoveryRun) -> bool:
        started = _utc_now()
        missing, details = self._missing_prerequisites(run.id)
        status = STAGE_FAILED if missing else STAGE_COMPLETED
        results = dict(run.stage_results or {})
        results["PREREQUISITE_VALIDATION"] = {
            "status": status,
            "started_at": started,
            "completed_at": _utc_now(),
            "warnings": [E_UPSTREAM_UNAVAILABLE] if missing else [],
            "metadata": {
                "missing_prerequisites": missing,
                "details": details,
            },
        }
        run.stage_results = results
        run.current_stage = None if not missing else "PREREQUISITE_VALIDATION"
        run.updated_at = datetime.datetime.utcnow()
        self._disc.commit()
        return not missing

    def _missing_prerequisites(self, run_id: str) -> Tuple[List[str], Dict[str, Any]]:
        missing: List[str] = []
        details: Dict[str, Any] = {"horizons": {}}

        run_exists = self._disc.query(DiscoveryRun).filter_by(id=run_id).count()
        details["discovery_run_count"] = run_exists
        if run_exists == 0:
            missing.append("discovery_run")

        fundamental_count = (
            self._disc.query(CompanyFundamentalMetric)
            .filter_by(run_id=run_id)
            .count()
        )
        scored_fundamental_count = (
            self._disc.query(CompanyFundamentalMetric)
            .filter(
                CompanyFundamentalMetric.run_id == run_id,
                CompanyFundamentalMetric.final_fundamental_score.isnot(None),
            )
            .count()
        )
        details["company_fundamental_metrics"] = {
            "count": fundamental_count,
            "scored_count": scored_fundamental_count,
        }
        if fundamental_count == 0:
            missing.append("company_fundamental_metrics")
        if scored_fundamental_count == 0:
            missing.append("company_fundamental_scores")

        for horizon in self._horizons:
            horizon_details: Dict[str, Any] = {}
            technical_count = (
                self._disc.query(CompanyTechnicalMetric)
                .filter_by(run_id=run_id, horizon=horizon)
                .count()
            )
            scored_technical_count = (
                self._disc.query(CompanyTechnicalMetric)
                .filter(
                    CompanyTechnicalMetric.run_id == run_id,
                    CompanyTechnicalMetric.horizon == horizon,
                    CompanyTechnicalMetric.final_technical_score.isnot(None),
                )
                .count()
            )
            horizon_details["company_technical_metrics"] = technical_count
            horizon_details["company_technical_scores"] = scored_technical_count
            if technical_count == 0:
                missing.append(f"company_technical_metrics.{horizon}")
            if scored_technical_count == 0:
                missing.append(f"company_technical_scores.{horizon}")

            details["horizons"][horizon] = horizon_details

        return sorted(set(missing)), details

    def _run_single_stage(self, run: DiscoveryRun, stage: str) -> str:
        existing = (run.stage_results or {}).get(stage) or {}
        if existing.get("status") in STAGE_TERMINAL_SUCCESS:
            logger.info(f"Run {run.id}: Stage {stage} is already completed. Skipping.")
            run.last_completed_stage = stage
            self._disc.commit()
            return existing["status"]

        logger.info(f"Run {run.id}: Starting stage {stage}")
        self._set_stage_running(run, stage)
        try:
            output = self._call_service(stage, run.id)
            result = self._normalize_output(output)
            if result["status"] == STAGE_FAILED:
                logger.error(f"Run {run.id}: Stage {stage} returned FAILED.")
                self._set_stage_finished(run, stage, result, failed=True)
                return STAGE_FAILED
            logger.info(f"Run {run.id}: Stage {stage} COMPLETED successfully.")
            self._set_stage_finished(run, stage, result)
            return result["status"]
        except Exception as exc:
            logger.exception(f"Run {run.id}: Exception occurred in stage {stage}")
            self._set_stage_exception(run, stage, exc)
            self._mark_downstream_skipped(run, stage)
            self._fail_run(run, E_STAGE_EXCEPTION, _safe_message(exc))
            self._disc.commit()
            raise

    def _run_horizon_stage(
        self,
        run: DiscoveryRun,
        stage: str,
        required_horizons: Iterable[str],
    ) -> Tuple[str, Set[str]]:
        required = set(required_horizons)
        results = dict(run.stage_results or {})
        parent = dict(results.get(stage) or {})
        horizons = dict(parent.get("horizons") or {})
        parent["started_at"] = parent.get("started_at") or _utc_now()
        parent["horizons"] = horizons
        results[stage] = parent
        run.stage_results = results
        run.current_stage = stage
        self._disc.commit()

        logger.info(f"Run {run.id}: Starting horizon stage {stage} for horizons {required_horizons}")

        success_horizons: Set[str] = set()
        failed_horizons: Set[str] = set()
        
        for horizon in self._horizons:
            existing = horizons.get(horizon) or {}
            if horizon not in required:
                if existing.get("status") not in STAGE_TERMINAL_SUCCESS:
                    horizons[horizon] = self._skipped_result(
                        f"{stage}.{horizon} skipped because the prerequisite horizon did not complete."
                    )
                    self._save_parent(run, stage, parent)
                continue

            if existing.get("status") in STAGE_TERMINAL_SUCCESS:
                logger.info(f"Run {run.id}: Horizon {horizon} in stage {stage} is already completed. Skipping.")
                success_horizons.add(horizon)
                continue

            horizons[horizon] = {
                "status": STAGE_RUNNING,
                "started_at": _utc_now(),
                "completed_at": None,
                "warnings": [],
                "metadata": {},
            }
            self._save_parent(run, stage, parent)
            try:
                logger.info(f"Run {run.id}: Calling service for {stage} ({horizon})")
                output = self._call_service(stage, run.id, horizon)
                result = self._normalize_output(output)
                horizons[horizon] = result
                if result["status"] == STAGE_FAILED:
                    logger.error(f"Run {run.id}: Horizon {horizon} in stage {stage} returned FAILED.")
                    failed_horizons.add(horizon)
                else:
                    logger.info(f"Run {run.id}: Horizon {horizon} in stage {stage} COMPLETED successfully.")
                    success_horizons.add(horizon)
                self._save_parent(run, stage, parent)
            except Exception as exc:
                logger.exception(f"Run {run.id}: Exception occurred in {stage} ({horizon})")
                horizons[horizon] = self._exception_result(exc)
                failed_horizons.add(horizon)
                self._save_parent(run, stage, parent)

        parent_status = self._parent_horizon_status(horizons, required)
        parent["status"] = parent_status
        parent["completed_at"] = _utc_now()
        parent["warnings"] = self._collect_horizon_warnings(horizons)
        parent["metadata"] = {
            "required_horizons": sorted(required),
            "completed_horizons": sorted(success_horizons),
            "failed_horizons": sorted(
                horizon
                for horizon in required
                if (horizons.get(horizon) or {}).get("status") == STAGE_FAILED
            ),
            "skipped_horizons": sorted(
                horizon
                for horizon, result in horizons.items()
                if result.get("status") == STAGE_SKIPPED
            ),
        }
        self._save_parent(run, stage, parent)
        if parent_status in STAGE_TERMINAL_SUCCESS:
            run.last_completed_stage = stage
        self._aggregate_run_warnings(run)
        self._disc.commit()
        return parent_status, success_horizons

    def _set_stage_running(self, run: DiscoveryRun, stage: str) -> None:
        results = dict(run.stage_results or {})
        results[stage] = {
            "status": STAGE_RUNNING,
            "started_at": _utc_now(),
            "completed_at": None,
            "warnings": [],
            "metadata": {},
        }
        run.stage_results = results
        run.current_stage = stage
        run.updated_at = datetime.datetime.utcnow()
        self._disc.commit()

    def _set_stage_finished(
        self,
        run: DiscoveryRun,
        stage: str,
        result: Dict[str, Any],
        failed: bool = False,
    ) -> None:
        results = dict(run.stage_results or {})
        previous = dict(results.get(stage) or {})
        previous.update(result)
        previous["completed_at"] = previous.get("completed_at") or _utc_now()
        results[stage] = previous
        run.stage_results = results
        if not failed:
            run.last_completed_stage = stage
        self._aggregate_run_warnings(run)
        run.updated_at = datetime.datetime.utcnow()
        self._disc.commit()

    def _set_stage_exception(self, run: DiscoveryRun, stage: str, exc: Exception) -> None:
        results = dict(run.stage_results or {})
        previous = dict(results.get(stage) or {})
        previous.update(self._exception_result(exc))
        results[stage] = previous
        run.stage_results = results
        self._aggregate_run_warnings(run)
        run.updated_at = datetime.datetime.utcnow()
        self._disc.commit()

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
                    if key not in {"status", "warnings"}
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
                    output.get("error_message") or f"Stage returned {STAGE_FAILED}."
                )
            return result

        metadata: Dict[str, Any] = {}
        if isinstance(output, str):
            metadata["result_id"] = output
        elif output is not None and _is_safe_metadata_value(output):
            metadata["result"] = output
        return {
            "status": STAGE_COMPLETED,
            "started_at": started_at,
            "completed_at": completed_at,
            "warnings": [],
            "metadata": metadata,
        }

    def _call_service(self, stage: str, run_id: str, horizon: Optional[str] = None) -> Any:
        provider = self._services.get(stage)
        if provider is None:
            raise DiscoveryPipelineError(
                E_SERVICE_NOT_CONFIGURED,
                f"No service configured for {stage}.",
            )

        method_name = METHOD_BY_STAGE[stage]
        service = provider
        if not hasattr(service, method_name) and callable(provider):
            try:
                if horizon is None:
                    return provider(run_id)
                return provider(run_id, horizon)
            except TypeError:
                service = self._make_service(provider)

        if hasattr(service, method_name):
            method = getattr(service, method_name)
            if horizon is None:
                return method(run_id)
            return method(run_id, horizon)

        if callable(service):
            try:
                if horizon is None:
                    return service(run_id)
                return service(run_id, horizon)
            except TypeError:
                if horizon is None:
                    return service(self._disc, run_id)
                return service(self._disc, run_id, horizon)

        raise DiscoveryPipelineError(
            E_SERVICE_NOT_CONFIGURED,
            f"Configured service for {stage} is not callable.",
        )

    def _make_service(self, factory: Callable[..., Any]) -> Any:
        try:
            return factory(self._disc)
        except TypeError:
            return factory()

    def _required_horizons(self, stage: str, active_horizons: Set[str]) -> Set[str]:
        return set(active_horizons)

    def _next_active_horizons(
        self,
        run_id: str,
        stage: str,
        success_horizons: Set[str],
    ) -> Set[str]:
        if stage in SELECTION_BY_STAGE:
            entity_type = SELECTION_BY_STAGE[stage]
            return {
                horizon
                for horizon in success_horizons
                if self._has_active_selection(run_id, horizon, entity_type)
            }
        return set(success_horizons)

    def _has_active_selection(self, run_id: str, horizon: str, entity_type: str) -> bool:
        return (
            self._disc.query(DiscoverySelection)
            .filter_by(
                run_id=run_id,
                horizon=horizon,
                entity_type=entity_type,
                selected=True,
            )
            .count()
            > 0
        )

    def _parent_horizon_status(
        self,
        horizons: Dict[str, Dict[str, Any]],
        required: Set[str],
    ) -> str:
        if not required:
            return STAGE_SKIPPED
        required_statuses = [
            (horizons.get(horizon) or {}).get("status") for horizon in required
        ]
        successes = [status for status in required_statuses if status in STAGE_TERMINAL_SUCCESS]
        if not successes:
            return STAGE_FAILED
        if any(status != STAGE_COMPLETED for status in required_statuses):
            return STAGE_COMPLETED_WITH_WARNINGS
        if self._collect_horizon_warnings(horizons):
            return STAGE_COMPLETED_WITH_WARNINGS
        return STAGE_COMPLETED

    def _collect_horizon_warnings(self, horizons: Dict[str, Dict[str, Any]]) -> List[str]:
        warnings: List[str] = []
        for result in horizons.values():
            warnings.extend(result.get("warnings") or [])
            if result.get("status") == STAGE_FAILED:
                code = result.get("error_code") or E_STAGE_FAILED
                warnings.append(code)
        return _clean_warnings(warnings)

    def _save_parent(
        self,
        run: DiscoveryRun,
        stage: str,
        parent: Dict[str, Any],
    ) -> None:
        results = dict(run.stage_results or {})
        results[stage] = parent
        run.stage_results = results
        run.current_stage = stage
        run.updated_at = datetime.datetime.utcnow()
        self._aggregate_run_warnings(run)
        self._disc.commit()

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

    def _mark_downstream_skipped(
        self,
        run: DiscoveryRun,
        failed_stage: Optional[str],
    ) -> None:
        results = dict(run.stage_results or {})
        start_skipping = failed_stage is None
        for stage in STAGE_ORDER:
            if failed_stage == stage:
                start_skipping = True
                continue
            if not start_skipping:
                continue
            existing = results.get(stage) or {}
            if existing.get("status") in STAGE_TERMINAL_SUCCESS or existing.get("status") == STAGE_FAILED:
                continue
            if stage in HORIZON_STAGES:
                horizons = dict(existing.get("horizons") or {})
                for horizon in self._horizons:
                    horizon_result = horizons.get(horizon) or {}
                    if horizon_result.get("status") not in STAGE_TERMINAL_SUCCESS:
                        horizons[horizon] = self._skipped_result(
                            f"{stage}.{horizon} skipped after upstream failure."
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
                    f"{stage} skipped after upstream failure."
                )
        run.stage_results = results
        self._aggregate_run_warnings(run)
        run.updated_at = datetime.datetime.utcnow()

    def _fail_run(self, run: DiscoveryRun, error_code: str, message: str) -> None:
        run.status = RUN_FAILED
        run.current_stage = None
        run.completed_at = datetime.datetime.utcnow()
        run.error_code = error_code
        run.error_message = _safe_message(message)
        self._aggregate_run_warnings(run)
        run.updated_at = datetime.datetime.utcnow()

    def _complete_run(self, run: DiscoveryRun) -> None:
        self._aggregate_run_warnings(run)
        run.status = RUN_COMPLETED_WITH_WARNINGS if run.warnings else RUN_COMPLETED
        run.current_stage = None
        run.last_completed_stage = STOCK_SELECTION
        run.completed_at = datetime.datetime.utcnow()
        run.error_code = None
        run.error_message = None
        run.updated_at = datetime.datetime.utcnow()

    def _aggregate_run_warnings(self, run: DiscoveryRun) -> None:
        warnings: List[str] = []
        for stage_result in (run.stage_results or {}).values():
            if not isinstance(stage_result, dict):
                continue
            warnings.extend(stage_result.get("warnings") or [])
            horizons = stage_result.get("horizons") or {}
            for horizon_result in horizons.values():
                warnings.extend(horizon_result.get("warnings") or [])
        run.warnings = _clean_warnings(warnings)

    def _result(self, run: DiscoveryRun) -> Dict[str, Any]:
        self._disc.refresh(run)
        error = None
        if run.error_code or run.error_message:
            error = {
                "code": run.error_code,
                "message": _safe_message(run.error_message),
            }
        return {
            "run_id": run.id,
            "status": run.status,
            "last_completed_stage": run.last_completed_stage,
            "resume_count": run.resume_count or 0,
            "horizons": self._build_horizon_results(run.id),
            "stage_results": run.stage_results or {},
            "warnings": run.warnings or [],
            "error": error,
            "error_code": run.error_code,
            "error_message": run.error_message,
        }

    def _build_horizon_results(self, run_id: str) -> Dict[str, Dict[str, Any]]:
        output: Dict[str, Dict[str, Any]] = {}
        for horizon in self._horizons:
            item: Dict[str, Any] = {}
            sector = self._selected(run_id, horizon, "SECTOR")
            industry = self._selected(run_id, horizon, "INDUSTRY")
            basic = self._selected(run_id, horizon, "BASIC_INDUSTRY")
            stocks = self._selected_stocks(run_id, horizon)
            if sector is not None:
                item["sector"] = sector.entity_name
            if industry is not None:
                item["industry"] = industry.entity_name
            if basic is not None:
                item["basic_industry"] = basic.entity_name
            if stocks:
                item["selected_stocks"] = [row.symbol or row.entity_name for row in stocks]
            output[horizon] = item
        return output

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

    def _selected_stocks(
        self,
        run_id: str,
        horizon: str,
    ) -> List[DiscoverySelection]:
        return (
            self._disc.query(DiscoverySelection)
            .filter_by(
                run_id=run_id,
                horizon=horizon,
                entity_type="STOCK",
                selected=True,
            )
            .order_by(DiscoverySelection.rank.asc(), DiscoverySelection.symbol.asc())
            .all()
        )

    def _has_incomplete_running_stage(self, stage_results: Dict[str, Any]) -> bool:
        for result in stage_results.values():
            if not isinstance(result, dict):
                continue
            if result.get("status") == STAGE_RUNNING:
                return True
            if any(
                horizon_result.get("status") == STAGE_RUNNING
                for horizon_result in (result.get("horizons") or {}).values()
            ):
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
        return f"discovery_pipeline:{run_id}"


def _utc_now() -> str:
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _clean_warnings(warnings: Iterable[Any]) -> List[str]:
    return sorted({str(warning) for warning in warnings if warning})


def _safe_message(value: Any) -> str:
    text_value = str(value or "")
    first_line = text_value.splitlines()[0] if text_value.splitlines() else text_value
    first_line = re.sub(r"(?i)(authorization|api[-_ ]?key|token|secret)\s*[:=]\s*(?:bearer\s+)?\S+", r"\1: [REDACTED]", first_line)
    first_line = re.sub(r"(?i)bearer\s+[a-z0-9._\-]+", "Bearer [REDACTED]", first_line)
    return first_line[:300]


def _is_safe_metadata_value(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool, list, tuple, dict))
