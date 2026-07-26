from collections.abc import Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from dms_auth_client.jwt import InvalidTokenError, TokenValidator


def make_current_user_dependency(validator: TokenValidator) -> Callable[..., dict]:
    """Baut eine FastAPI-Dependency, die den Bearer-Token gegen ``validator`` prüft
    und die Claims zurückgibt, oder 401 wirft. Ein Aufruf je Service-Startup.
    """
    scheme = HTTPBearer(auto_error=True)

    async def get_current_user(
        credentials: HTTPAuthorizationCredentials = Depends(scheme),
    ) -> dict:
        try:
            return validator.validate(credentials.credentials)
        except InvalidTokenError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(exc),
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc

    return get_current_user
