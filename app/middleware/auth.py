import jwt
from fastapi import Request
from starlette.responses import JSONResponse
from ..config import settings

async def auth_middleware(request: Request, call_next):
    # allow unauthenticated health check
    if request.url.path == "/health":
        return await call_next(request)

    auth = request.headers.get("Authorization")
    if not auth or not auth.startswith("Bearer "):
        return JSONResponse(
            {"detail": "Missing or invalid Authorization header"},
            status_code=401
        )

    token = auth.removeprefix("Bearer ").strip()
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
        )
        request.state.user = payload
    except jwt.ExpiredSignatureError:
        return JSONResponse({"detail": "Token expired"}, status_code=401)
    except jwt.PyJWTError:
        return JSONResponse({"detail": "Token invalid"}, status_code=401)
    except Exception as e:
        return JSONResponse(
            {"detail": f"Auth error: {str(e)}"},
            status_code=500
        )

    return await call_next(request)