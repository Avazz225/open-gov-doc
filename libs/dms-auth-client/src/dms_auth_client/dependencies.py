from collections.abc import Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from dms_auth_client.jwt import InvalidTokenError, TokenValidator


def make_current_user_dependency(validator: TokenValidator) -> Callable[..., dict]:
    """Builds a FastAPI dependency that validates the bearer token against
    ``validator`` and returns the claims, or raises 401. One call per service startup.
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
