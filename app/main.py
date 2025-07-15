from fastapi import FastAPI

from app.middleware.logging import LoggingMiddleware
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.cache import CacheMiddleware
from app.routers.proxy import router as proxy_router
from app.middleware.auth import auth_middleware
from app.config import settings


app = FastAPI(title="Gateway API", version="1.0.0")

# Logging should be first to capture all requests
app.add_middleware(LoggingMiddleware)

# Response caching
if settings.CACHE_ENABLED:
    app.add_middleware(
        CacheMiddleware,
        cache_ttl=settings.CACHE_TTL
    )

# Rate limiting
if settings.RATE_LIMIT_ENABLED:
    app.add_middleware(
        RateLimitMiddleware,
        rate_limit_per_minute=settings.RATE_LIMIT_PER_MINUTE,
        window_size_seconds=settings.RATE_LIMIT_WINDOW_SECONDS
    )

# Auth middleware
app.middleware("http")(auth_middleware)

app.include_router(proxy_router)
