from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from api.v1.discovery_models import DiscoveryApiError, DiscoveryResultErrorResponse
from api.v1.discovery_router import router as discovery_router


app = FastAPI(title="Sector Discovery API")
app.include_router(discovery_router, prefix="/api/v1")


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    code = "INVALID_DISCOVERY_PREPARATION_REQUEST" if request.url.path.endswith("/prepare") else "INVALID_DISCOVERY_REQUEST"
    message = "Discovery preparation request is invalid." if request.url.path.endswith("/prepare") else "Discovery request is invalid."
    body = DiscoveryResultErrorResponse(
        success=False,
        error=DiscoveryApiError(
            code=code,
            message=message,
        ),
    )
    return JSONResponse(status_code=422, content=body.model_dump())
