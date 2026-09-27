from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class AnalysisFailure(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.message = message


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AnalysisFailure)
    async def analysis_failure(request: Request, exc: AnalysisFailure) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message},
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Default validation details include the source input. Never echo care records or API keys.
        return JSONResponse(
            status_code=422,
            content={"code": "INVALID_REQUEST", "message": "요청 형식과 필수 항목을 확인해주세요."},
            headers={"Cache-Control": "no-store"},
        )
