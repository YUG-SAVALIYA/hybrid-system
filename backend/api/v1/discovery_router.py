import logging
import re
import datetime
from typing import Generator

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from api.v1.discovery_models import (
    DiscoveryApiError,
    DiscoveryExecuteErrorResponse,
    DiscoveryExecuteRequest,
    DiscoveryExecuteResponse,
    DiscoveryExecuteSuccessResponse,
    DiscoveryPrepareErrorResponse,
    DiscoveryPrepareRequest,
    DiscoveryPrepareResponse,
    DiscoveryPrepareSuccessResponse,
    DiscoveryResultErrorResponse,
    DiscoveryResultResponse,
    DiscoveryResultSuccessResponse,
    DiscoveryRunCreateRequest,
    DiscoveryRunCreateResponse,
    DiscoveryRunCreateSuccessResponse,
)
from database import DiscoverySessionLocal
from services.discovery.discovery_pipeline_orchestrator import (
    BASIC_INDUSTRY_SELECTION,
    E_ALREADY_RUNNING,
    E_RUN_NOT_FOUND,
    E_UPSTREAM_UNAVAILABLE,
    INDUSTRY_SELECTION,
    MACRO_FILTER,
    MACRO_SEARCH,
    SECTOR_SELECTION,
    STOCK_SELECTION,
    DiscoveryPipelineOrchestrator,
)
from services.discovery.discovery_selection_stages import (
    BasicIndustrySelectionStage,
    IndustrySelectionStage,
    SectorSelectionStage,
    StockSelectionStage,
)
from services.discovery.discovery_run_creation import (
    E_INVALID_AS_OF_DATE,
    E_RUN_ALREADY_EXISTS,
    DiscoveryRunAlreadyExistsError,
    DiscoveryRunCreationService,
    InvalidDiscoveryAsOfDateError,
)
from services.discovery.discovery_result import DiscoveryResultService, W_RUN_NOT_FOUND
from services.discovery.discovery_upstream_preparation import (
    E_ALREADY_RUNNING as E_PREP_ALREADY_RUNNING,
    E_AS_OF_UNAVAILABLE,
    E_RUN_NOT_FOUND as E_PREP_RUN_NOT_FOUND,
    E_SERVICE_UNAVAILABLE,
    DiscoveryUpstreamPreparationService,
)
from services.macro.macro_filter_summary import MacroFilterSummaryService
from services.macro.parallel_macro_search import ParallelMacroSearchProvider


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/discovery", tags=["discovery"])

INVALID_RUN_ID = "INVALID_DISCOVERY_RUN_ID"
RESULT_UNAVAILABLE = "DISCOVERY_RESULT_UNAVAILABLE"
EXECUTION_FAILED = "DISCOVERY_PIPELINE_EXECUTION_FAILED"
CREATION_FAILED = "DISCOVERY_RUN_CREATION_FAILED"
INVALID_REQUEST = "INVALID_DISCOVERY_REQUEST"
PREPARATION_FAILED = "DISCOVERY_PREPARATION_FAILED"
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def get_discovery_session() -> Generator[Session, None, None]:
    session = DiscoverySessionLocal()
    try:
        yield session
    finally:
        session.close()


def get_discovery_result_service(
    session: Session = Depends(get_discovery_session),
) -> DiscoveryResultService:
    return DiscoveryResultService(session)


def get_discovery_run_creation_service(
    session: Session = Depends(get_discovery_session),
) -> DiscoveryRunCreationService:
    return DiscoveryRunCreationService(session)


def get_discovery_upstream_preparation_service(
    session: Session = Depends(get_discovery_session),
) -> DiscoveryUpstreamPreparationService:
    return DiscoveryUpstreamPreparationService(session)


def _default_pipeline_services(session: Session):
    return {
        MACRO_SEARCH: ParallelMacroSearchProvider(session),
        MACRO_FILTER: MacroFilterSummaryService(session),
        SECTOR_SELECTION: SectorSelectionStage(session),
        INDUSTRY_SELECTION: IndustrySelectionStage(session),
        BASIC_INDUSTRY_SELECTION: BasicIndustrySelectionStage(session),
        STOCK_SELECTION: StockSelectionStage(session),
    }


def get_discovery_pipeline_orchestrator(
    session: Session = Depends(get_discovery_session),
) -> DiscoveryPipelineOrchestrator:
    return DiscoveryPipelineOrchestrator(
        session,
        services=_default_pipeline_services(session),
    )


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    body = DiscoveryResultErrorResponse(
        success=False,
        error=DiscoveryApiError(code=code, message=message),
    )
    return JSONResponse(status_code=status_code, content=body.model_dump())


def validate_run_id(run_id: str) -> str:
    value = run_id.strip()
    if not value or len(value) > 128 or RUN_ID_PATTERN.fullmatch(value) is None:
        raise ValueError(INVALID_RUN_ID)
    return value


def _parse_as_of_date(value: str | None) -> datetime.date | None:
    if value is None:
        return None
    try:
        parsed = datetime.date.fromisoformat(value)
    except ValueError as exc:
        raise InvalidDiscoveryAsOfDateError("Discovery as_of_date is invalid.") from exc
    if parsed > datetime.datetime.now(datetime.timezone.utc).date():
        raise InvalidDiscoveryAsOfDateError("Discovery as_of_date cannot be in the future.")
    return parsed


def _format_utc(value) -> str:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.timezone.utc)
    return value.astimezone(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _creation_response(row) -> DiscoveryRunCreateSuccessResponse:
    return DiscoveryRunCreateSuccessResponse(
        success=True,
        data={
            "run_id": row.id,
            "as_of_date": row.run_date or row.source_data_as_of,
            "status": row.status,
            "created_at": _format_utc(row.created_at),
        },
    )


@router.post(
    "/runs",
    status_code=201,
    response_model=DiscoveryRunCreateResponse,
)
def create_discovery_run(
    request: DiscoveryRunCreateRequest,
    service: DiscoveryRunCreationService = Depends(get_discovery_run_creation_service),
):
    try:
        clean_run_id = validate_run_id(request.run_id) if request.run_id is not None else None
    except ValueError:
        return _error_response(
            422,
            INVALID_RUN_ID,
            "Discovery run ID is invalid.",
        )

    try:
        as_of_date = _parse_as_of_date(request.as_of_date)
    except InvalidDiscoveryAsOfDateError:
        return _error_response(
            422,
            E_INVALID_AS_OF_DATE,
            "Discovery as_of_date is invalid.",
        )

    try:
        row = service.create_run(run_id=clean_run_id, as_of_date=as_of_date)
    except DiscoveryRunAlreadyExistsError:
        return _error_response(
            409,
            E_RUN_ALREADY_EXISTS,
            "Discovery run already exists.",
        )
    except InvalidDiscoveryAsOfDateError:
        return _error_response(
            422,
            E_INVALID_AS_OF_DATE,
            "Discovery as_of_date is invalid.",
        )
    except Exception:
        logger.exception("Failed to create discovery run")
        return _error_response(
            500,
            CREATION_FAILED,
            "The discovery run could not be created.",
        )

    return _creation_response(row)


@router.get(
    "/runs/{run_id}/result",
    response_model=DiscoveryResultResponse,
)
def get_discovery_result(
    run_id: str,
    service: DiscoveryResultService = Depends(get_discovery_result_service),
):
    try:
        clean_run_id = validate_run_id(run_id)
    except ValueError:
        return _error_response(
            422,
            INVALID_RUN_ID,
            "Discovery run ID is invalid.",
        )

    try:
        result = service.get_result(clean_run_id)
    except Exception:
        logger.exception("Failed to load discovery result")
        return _error_response(
            500,
            RESULT_UNAVAILABLE,
            "The discovery result could not be loaded.",
        )

    if (result.get("error") or {}).get("code") == W_RUN_NOT_FOUND:
        return _error_response(
            404,
            W_RUN_NOT_FOUND,
            "Discovery run was not found.",
        )

    body = DiscoveryResultSuccessResponse(success=True, data=result)
    return body


@router.get(
    "/runs/{run_id}/constituents",
)
def get_discovery_constituents(
    run_id: str,
    horizon: str,
    entity_type: str,
    entity_name: str,
    parent_sector: str = "",
    parent_industry: str = "",
    service: DiscoveryResultService = Depends(get_discovery_result_service),
):
    try:
        clean_run_id = validate_run_id(run_id)
    except ValueError:
        return _error_response(
            422,
            INVALID_RUN_ID,
            "Discovery run ID is invalid.",
        )

    try:
        result = service.get_group_constituents(clean_run_id, horizon, entity_type, entity_name, parent_sector, parent_industry)
    except Exception:
        logger.exception("Failed to load discovery constituents")
        return _error_response(
            500,
            RESULT_UNAVAILABLE,
            "The discovery constituents could not be loaded.",
        )

    return {"success": True, "data": result}



@router.post(
    "/runs/{run_id}/execute",
    response_model=DiscoveryExecuteResponse,
)
def execute_discovery_run(
    run_id: str,
    request: DiscoveryExecuteRequest = DiscoveryExecuteRequest(),
    orchestrator: DiscoveryPipelineOrchestrator = Depends(get_discovery_pipeline_orchestrator),
):
    try:
        clean_run_id = validate_run_id(run_id)
    except ValueError:
        return _error_response(
            422,
            INVALID_RUN_ID,
            "Discovery run ID is invalid.",
        )

    try:
        result = orchestrator.execute(
            clean_run_id,
            resume=request.resume,
            force_restart=request.force_restart,
            target_horizon=request.target_horizon,
        )
    except Exception:
        logger.exception("Failed to execute discovery pipeline")
        body = DiscoveryExecuteErrorResponse(
            success=False,
            error=DiscoveryApiError(
                code=EXECUTION_FAILED,
                message="The discovery pipeline could not be executed.",
            ),
        )
        return JSONResponse(status_code=500, content=body.model_dump())

    error_code = (result.get("error") or {}).get("code") or result.get("error_code")
    if error_code in {E_RUN_NOT_FOUND, W_RUN_NOT_FOUND}:
        body = DiscoveryExecuteErrorResponse(
            success=False,
            error=DiscoveryApiError(
                code=W_RUN_NOT_FOUND,
                message="Discovery run was not found.",
            ),
        )
        return JSONResponse(status_code=404, content=body.model_dump())
    if error_code == E_ALREADY_RUNNING:
        body = DiscoveryExecuteErrorResponse(
            success=False,
            error=DiscoveryApiError(
                code=E_ALREADY_RUNNING,
                message="This discovery run is already being processed.",
            ),
        )
        return JSONResponse(status_code=409, content=body.model_dump())
    if error_code == E_UPSTREAM_UNAVAILABLE:
        body = DiscoveryExecuteErrorResponse(
            success=False,
            error=DiscoveryApiError(
                code=E_UPSTREAM_UNAVAILABLE,
                message="Required upstream discovery data is unavailable.",
            ),
        )
        return JSONResponse(status_code=422, content=body.model_dump())

    body = DiscoveryExecuteSuccessResponse(success=True, data=result)
    return body


@router.post(
    "/runs/{run_id}/prepare",
    response_model=DiscoveryPrepareResponse,
)
def prepare_discovery_run(
    run_id: str,
    request: DiscoveryPrepareRequest = DiscoveryPrepareRequest(),
    service: DiscoveryUpstreamPreparationService = Depends(
        get_discovery_upstream_preparation_service
    ),
):
    try:
        clean_run_id = validate_run_id(run_id)
    except ValueError:
        return _error_response(
            422,
            INVALID_RUN_ID,
            "Discovery run ID is invalid.",
        )

    try:
        result = service.prepare(
            clean_run_id,
            resume=request.resume,
            force_restart=request.force_restart,
        )
    except Exception:
        logger.exception("Failed to prepare discovery upstream data")
        body = DiscoveryPrepareErrorResponse(
            success=False,
            error=DiscoveryApiError(
                code=PREPARATION_FAILED,
                message="Discovery preparation could not be completed.",
            ),
        )
        return JSONResponse(status_code=500, content=body.model_dump())

    error_code = (result.get("error") or {}).get("code") or result.get("error_code")
    if error_code in {E_PREP_RUN_NOT_FOUND, W_RUN_NOT_FOUND}:
        body = DiscoveryPrepareErrorResponse(
            success=False,
            error=DiscoveryApiError(
                code=E_PREP_RUN_NOT_FOUND,
                message="Discovery run was not found.",
            ),
        )
        return JSONResponse(status_code=404, content=body.model_dump())
    if error_code == E_PREP_ALREADY_RUNNING:
        body = DiscoveryPrepareErrorResponse(
            success=False,
            error=DiscoveryApiError(
                code=E_PREP_ALREADY_RUNNING,
                message="This discovery run is already being processed.",
            ),
        )
        return JSONResponse(status_code=409, content=body.model_dump())
    if error_code == E_AS_OF_UNAVAILABLE:
        body = DiscoveryPrepareErrorResponse(
            success=False,
            error=DiscoveryApiError(
                code=E_AS_OF_UNAVAILABLE,
                message="Discovery run source data date is unavailable.",
            ),
        )
        return JSONResponse(status_code=422, content=body.model_dump())
    if error_code == E_SERVICE_UNAVAILABLE:
        body = DiscoveryPrepareErrorResponse(
            success=False,
            error=DiscoveryApiError(
                code=E_SERVICE_UNAVAILABLE,
                message="A required discovery preparation service is unavailable.",
            ),
        )
        return JSONResponse(status_code=422, content=body.model_dump())

    body = DiscoveryPrepareSuccessResponse(success=True, data=result)
    return body
