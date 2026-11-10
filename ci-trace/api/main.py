from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from api.ci_diagnoses import router

app = FastAPI(title="CI Trace")
app.include_router(router)


@app.exception_handler(RequestValidationError)
async def invalid_request(_request: Request, _error: RequestValidationError) -> JSONResponse:
    # Validation errors can contain the original input, including model keys.
    return JSONResponse({"detail": "Invalid request"}, status_code=422)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
