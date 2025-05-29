
import logging
from fastapi import FastAPI, Request, HTTPException

from app.middleware.logging import LoggingMiddleware
from app.middleware.rate_limit import RateLimitMiddleware
from app.routers.proxy import router as proxy_router
from app.middleware.auth import auth_middleware
from app.config import settings


app = FastAPI(title="Gateway API", version="1.0.0")

# Register middlewares (order matters here)
# Logging should be first to capture all requests
app.add_middleware(LoggingMiddleware)

# Rate limiting
if settings.RATE_LIMIT_ENABLED:
    app.add_middleware(
        RateLimitMiddleware,
        rate_limit_per_minute=settings.RATE_LIMIT_PER_MINUTE
    )

# Auth middleware
app.middleware("http")(auth_middleware)

app.include_router(proxy_router)
