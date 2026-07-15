from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr


RunStatus = Literal[
    "PENDING",
    "RUNNING",
    "FAILED",
    "COMPLETED_WITH_WARNINGS",
    "COMPLETED",
]

HorizonStatus = Literal[
    "PENDING",
    "RUNNING",
    "COMPLETED",
    "COMPLETED_WITH_WARNINGS",
    "FAILED",
    "SKIPPED",
]


class DiscoveryApiError(BaseModel):
    code: str
    message: str


class DiscoveryGroupResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    rank: Optional[int] = None
    final_score: Optional[float] = None
    technical_score: Optional[float] = None
    fundamental_score: Optional[float] = None
    macro_score: Optional[float] = None
    status: Optional[str] = None
    coverage_pct: Optional[float] = None
    warnings: List[str] = Field(default_factory=list)
    parent_sector: Optional[str] = None
    parent_industry: Optional[str] = None


class DiscoveryStockResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    company_id: str
    symbol: str
    rank: Optional[int] = None
    selected: bool
    final_score: Optional[float] = None
    technical_score: Optional[float] = None
    fundamental_score: Optional[float] = None
    inherited_macro_score: Optional[float] = None
    score_status: Optional[str] = None
    score_coverage_pct: Optional[float] = None
    warnings: List[str] = Field(default_factory=list)


class DiscoveryHorizonResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: HorizonStatus
    sectors: List[DiscoveryGroupResult] = Field(default_factory=list)
    industries: List[DiscoveryGroupResult] = Field(default_factory=list)
    basic_industries: List[DiscoveryGroupResult] = Field(default_factory=list)
    stocks: List[DiscoveryStockResult] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class DiscoveryHorizonsResult(BaseModel):
    SHORT: DiscoveryHorizonResult
    MID: DiscoveryHorizonResult
    LONG: DiscoveryHorizonResult


class DiscoveryResultData(BaseModel):
    model_config = ConfigDict(extra="allow")

    run_id: str
    status: RunStatus
    current_stage: Optional[str] = None
    last_completed_stage: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    resume_count: int
    warnings: List[str] = Field(default_factory=list)
    error: Optional[DiscoveryApiError] = None
    stage_results: Dict[str, Any] = Field(default_factory=dict)
    horizons: DiscoveryHorizonsResult


class DiscoveryResultSuccessResponse(BaseModel):
    success: Literal[True]
    data: DiscoveryResultData


class DiscoveryResultErrorResponse(BaseModel):
    success: Literal[False]
    error: DiscoveryApiError


DiscoveryResultResponse = Union[
    DiscoveryResultSuccessResponse,
    DiscoveryResultErrorResponse,
]


class DiscoveryExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resume: StrictBool = True
    force_restart: StrictBool = False
    target_horizon: Optional[str] = None


class DiscoveryExecuteData(BaseModel):
    model_config = ConfigDict(extra="allow")

    run_id: str
    status: RunStatus
    last_completed_stage: Optional[str] = None
    resume_count: int
    horizons: Dict[str, Any] = Field(default_factory=dict)
    stage_results: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    error: Optional[DiscoveryApiError] = None


class DiscoveryExecuteSuccessResponse(BaseModel):
    success: Literal[True]
    data: DiscoveryExecuteData


class DiscoveryExecuteErrorResponse(BaseModel):
    success: Literal[False]
    error: DiscoveryApiError


DiscoveryExecuteResponse = Union[
    DiscoveryExecuteSuccessResponse,
    DiscoveryExecuteErrorResponse,
]


class DiscoveryPrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resume: StrictBool = True
    force_restart: StrictBool = False


class DiscoveryPrepareData(BaseModel):
    model_config = ConfigDict(extra="allow")

    run_id: str
    status: RunStatus
    preparation_status: Optional[RunStatus] = None
    last_completed_stage: Optional[str] = None
    resume_count: int
    stage_results: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    error: Optional[DiscoveryApiError] = None


class DiscoveryPrepareSuccessResponse(BaseModel):
    success: Literal[True]
    data: DiscoveryPrepareData


class DiscoveryPrepareErrorResponse(BaseModel):
    success: Literal[False]
    error: DiscoveryApiError


DiscoveryPrepareResponse = Union[
    DiscoveryPrepareSuccessResponse,
    DiscoveryPrepareErrorResponse,
]


class DiscoveryRunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: Optional[StrictStr] = None
    as_of_date: Optional[StrictStr] = None


class DiscoveryRunCreateData(BaseModel):
    run_id: str
    as_of_date: str
    status: Literal["PENDING"]
    created_at: str


class DiscoveryRunCreateSuccessResponse(BaseModel):
    success: Literal[True]
    data: DiscoveryRunCreateData


DiscoveryRunCreateResponse = Union[
    DiscoveryRunCreateSuccessResponse,
    DiscoveryResultErrorResponse,
]
