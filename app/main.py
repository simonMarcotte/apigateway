
import logging
from fastapi import FastAPI, Request, HTTPException

from app.middleware.logging import LoggingMiddleware
from app.routers.proxy import router as proxy_router
from app.middleware.auth import auth_middleware


app = FastAPI(title="Gateway API", version="1.0.0")

app.add_middleware(LoggingMiddleware)
app.middleware("http")(auth_middleware)

app.include_router(proxy_router)


