import time
import logging
from typing import Dict, Tuple, List
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from ..config import settings

logger = logging.getLogger("gateway")

class RateLimiter:

    def __init__(self, rate_limit_per_minute: int, window_size_seconds: int = 60):
        """
        Initialize a token bucket rate limiter.
        
        Args:
            rate_limit_per_minute: The number of tokens added per minute
            window_size_seconds: The time window in seconds for token refill (default: 60)
        """
        self.max_tokens = rate_limit_per_minute
        self.refill_rate = rate_limit_per_minute / window_size_seconds  # tokens per second
        self.window_size = window_size_seconds
        # Dict of client_id -> (tokens, last_refill_time)
        self.buckets: Dict[str, Tuple[float, float]] = {}
    

    def is_rate_limited(self, client_id: str) -> Tuple[bool, int]:
        """
        Check if the client has exceeded their rate limit using token bucket algorithm.
        
        Args:
            client_id: The identifier for the client
            
        Returns:
            (is_limited, remaining_tokens)
        """
        now = time.time()
        
        # If this is a new client, initialize their bucket with full tokens
        if client_id not in self.buckets:
            self.buckets[client_id] = (self.max_tokens, now)
            tokens = self.max_tokens
        else:
            tokens, last_refill = self.buckets[client_id]

            # Calculate how many tokens to add based on time elapsed
            time_elapsed = now - last_refill
            tokens_to_add = time_elapsed * self.refill_rate
            tokens = min(self.max_tokens, tokens + tokens_to_add)

        is_limited = tokens < 1
        
        # If not limited, consume a token
        if not is_limited:
            tokens -= 1
            
        self.buckets[client_id] = (tokens, now)

        return is_limited, int(tokens)


    def clean_old_entries(self):
        """
        Remove entries for clients that haven't been seen in a while to prevent memory issues.
        In token bucket, we consider clients inactive if they haven't been seen for 
        several window_size periods.
        """
        now = time.time()
        inactive_threshold = now - (self.window_size * 3)  # 3 windows of inactivity
        
        for client_id in list(self.buckets.keys()):
            _, last_refill = self.buckets[client_id]
            if last_refill < inactive_threshold:
                del self.buckets[client_id]


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, rate_limit_per_minute: int = None, window_size_seconds: int = None):
        super().__init__(app)
        self.rate_limiter = RateLimiter(
            rate_limit_per_minute or settings.RATE_LIMIT_PER_MINUTE,
            window_size_seconds or settings.RATE_LIMIT_WINDOW_SECONDS
        )
        self.cleanup_interval = 60  # Clean up every minute
        self.last_cleanup = time.time()
    

    async def dispatch(self, request: Request, call_next):
        # Skip rate limiting for health checks
        if request.url.path == "/health":
            return await call_next(request)
        
        # Get client identifier from JWT token (if available) or IP address
        client_id = self._get_client_id(request)
        
        # Periodically clean up old entries
        now = time.time()
        if now - self.last_cleanup > self.cleanup_interval:
            self.rate_limiter.clean_old_entries()
            self.last_cleanup = now
        
        # Check if rate limited
        limited, remaining = self.rate_limiter.is_rate_limited(client_id)
        
        # Calculate token refill time based on token bucket algorithm
        # Based on refill rate, how long until one token is added
        seconds_per_token = 1.0 / self.rate_limiter.refill_rate
        reset_time = int(now + seconds_per_token) + 1
        
        # Add rate limit headers
        headers = {
            "X-RateLimit-Limit": str(self.rate_limiter.max_tokens),
            "X-RateLimit-Remaining": str(remaining),
            "X-RateLimit-Reset": str(reset_time)
        }
        
        if limited:
            logger.warning(f"Rate limit exceeded for client: {client_id}")
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests"},
                headers=headers
            )
        
        # Proceed with the request
        response = await call_next(request)
        
        # Add rate limit headers to the response
        for name, value in headers.items():
            response.headers[name] = value
        
        return response
    
    
    def _get_client_id(self, request: Request) -> str:
        """Extract client identifier from request"""
        # Try to get user ID from JWT if authenticated
        if hasattr(request.state, "user") and request.state.user.get("sub"):
            return f"user:{request.state.user['sub']}"
        
        # Fallback to IP address
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return f"ip:{forwarded.split(',')[0].strip()}"
        
        return f"ip:{request.client.host}"
