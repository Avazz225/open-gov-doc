"""CMIS service exceptions (5.2.10 "Error Handling and Return Codes") - the
JSON error body MUST carry `{"exception": ..., "message": ...}` at the root,
not wrapped in FastAPI's standard `{"detail": ...}` envelope (hence a
dedicated exception handler instead of `HTTPException.detail`)."""

from dms_connector_sdk import StructureRuleError
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

    # Filing structure (P91-S2a, ADR 0255): a refused create or move is a
    # CMIS `constraint` violation, with the reason as the message.
    @app.exception_handler(StructureRuleError)
    async def _handle_structure_error(_request: Request, exc: StructureRuleError) -> JSONResponse:
        return JSONResponse(
            status_code=_STATUS_CODES["constraint"],
            content={"exception": "constraint", "message": str(exc)},
        )
