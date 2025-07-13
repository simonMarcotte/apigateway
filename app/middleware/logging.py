import time
import logging
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("gateway")

class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        logger.info(f"→ {request.method} {request.url.path}")
        try:
            response = await call_next(request)
        except Exception as exc:
            logger.exception("Error handling request")
            raise
        process_time = (time.time() - start_time) * 1000  # Convert to milliseconds

        logger.info(
            f"Request: {request.method} {request.url} - "
            f"Response: {response.status_code} - "
            f"Process Time: {process_time:.4f}ms"
        )

        return response
    