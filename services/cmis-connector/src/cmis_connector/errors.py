"""CMIS-Services-Exceptions (5.2.10 "Error Handling and Return Codes") - der
JSON-Fehlerkörper MUSS `{"exception": ..., "message": ...}` an der Wurzel
tragen, nicht in FastAPIs Standard-`{"detail": ...}`-Hülle verpackt (daher
ein eigener Exception-Handler statt `HTTPException.detail`)."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

_STATUS_CODES = {
    "invalidArgument": 400,
    "notSupported": 405,
    "objectNotFound": 404,
    "permissionDenied": 403,
    "runtime": 500,
    "constraint": 409,
    "updateConflict": 409,
    "versioning": 409,
}


class CmisError(Exception):
    def __init__(self, exception: str, message: str) -> None:
        self.exception = exception
        self.message = message
        self.status_code = _STATUS_CODES[exception]
        super().__init__(message)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(CmisError)
    async def _handle_cmis_error(_request: Request, exc: CmisError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"exception": exc.exception, "message": exc.message},
        )
