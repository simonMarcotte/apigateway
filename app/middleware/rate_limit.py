import time
import logging
import redis
from typing import Tuple
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from ..config import settings
from ..utils.redis_utils import RedisConnection

logger = logging.getLogger("gateway")

class RedisRateLimiter:
    """Redis-based distributed rate limiter using token bucket algorithm"""

    def __init__(self, rate_limit_per_minute: int, window_size_seconds: int = 60):
        """
        Initialize a Redis-based token bucket rate limiter.
        
        Args:
            rate_limit_per_minute: The number of tokens added per minute
            window_size_seconds: The time window in seconds for token refill (default: 60)
        """
        self.max_tokens = rate_limit_per_minute
        self.refill_rate = rate_limit_per_minute / window_size_seconds  # tokens per second
        self.window_size = window_size_seconds
        self.redis_client = RedisConnection.get_client()

    def is_rate_limited(self, client_id: str) -> Tuple[bool, int]:
        """
        Check if the client has exceeded their rate limit using Redis token bucket.
        
        Args:
            client_id: The identifier for the client
            
        Returns:
            (is_limited, remaining_tokens)
        """
        now = time.time()
        key = f"rate_limit:{client_id}"
        ttl = self.window_size * 3  # Keep bucket for 3 window periods
        
        try:
            # Use Redis WATCH/MULTI/EXEC for atomic operations
            with self.redis_client.pipeline() as pipe:
                # Keep retrying if another client modifies the key
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        # Watch the key for changes
                        pipe.watch(key)
                        
                        # Get current bucket state
                        bucket_data = pipe.hmget(key, 'tokens', 'last_refill')
                        
                        # Parse current state
                        tokens = float(bucket_data[0]) if bucket_data[0] else self.max_tokens
                        last_refill = float(bucket_data[1]) if bucket_data[1] else now
                        
                        # Calculate token refill
                        time_elapsed = now - last_refill
                        tokens_to_add = time_elapsed * self.refill_rate
                        tokens = min(self.max_tokens, tokens + tokens_to_add)
                        
                        # Check if rate limited
                        is_limited = tokens < 1
                        
                        # If not limited, consume a token
                        if not is_limited:
                            tokens -= 1
                        
                        # Begin transaction
                        pipe.multi()
                        
                        # Update bucket state atomically
                        pipe.hset(key, mapping={
                            'tokens': tokens,
                            'last_refill': now
                        })
                        pipe.expire(key, ttl)
                        
                        # Execute transaction
                        pipe.execute()
                        
                        # Success - return result
                        return is_limited, int(tokens)
                        
                    except redis.WatchError:
                        # Another client modified the key, retry
                        logger.debug(f"Watch error on attempt {attempt + 1} for {client_id}")
                        if attempt == max_retries - 1:
                            # Max retries reached, fallback
                            logger.warning(f"Max retries reached for rate limit check: {client_id}")
                            return False, self.max_tokens
                        continue
                    finally:
                        pipe.reset()
                        
        except Exception as e:
            logger.error(f"Redis rate limit check failed for {client_id}: {e}")
            # Fallback: allow request but log error
            return False, self.max_tokens


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, rate_limit_per_minute: int = None, window_size_seconds: int = None):
        super().__init__(app)
        self.rate_limiter = RedisRateLimiter(
            rate_limit_per_minute or settings.RATE_LIMIT_PER_MINUTE,
            window_size_seconds or settings.RATE_LIMIT_WINDOW_SECONDS
        )

    async def dispatch(self, request: Request, call_next):
        # Skip rate limiting for health checks
        if request.url.path == "/health":
            return await call_next(request)
        
        # Get client identifier from JWT token (if available) or IP address
        client_id = self._get_client_id(request)
        
        # Check if rate limited
        limited, remaining = self.rate_limiter.is_rate_limited(client_id)
        
        # Calculate token refill time based on token bucket algorithm
        now = time.time()
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
