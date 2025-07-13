import time
import json
import hashlib
import logging
from typing import Optional, Tuple
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response as StarletteResponse

from ..config import settings
from ..utils.redis_utils import RedisConnection

logger = logging.getLogger("gateway")

class ResponseCache:
    """Redis-based response cache for API Gateway"""
    
    def __init__(self, default_ttl: int = 300):
        """
        Initialize response cache.
        
        Args:
            default_ttl: Default cache TTL in seconds (5 minutes)
        """
        self.redis_client = RedisConnection.get_client()
        self.default_ttl = default_ttl
        self.cache_prefix = "cache:"
    
    def _generate_cache_key(self, request: Request) -> str:
        """Generate a unique cache key for the request"""
        # Include method, path, query params, and user context
        key_parts = [
            request.method,
            str(request.url.path),
            str(request.url.query),
        ]
        
        # Include user context if authenticated
        if hasattr(request.state, "user") and request.state.user:
            key_parts.append(f"user:{request.state.user.get('sub', 'anonymous')}")
        else:
            key_parts.append("anonymous")
        
        # Create hash of the key parts
        key_string = "|".join(key_parts)
        key_hash = hashlib.md5(key_string.encode()).hexdigest()
        
        return f"{self.cache_prefix}{key_hash}"
    
    def get_cached_response(self, request: Request) -> Optional[Tuple[bytes, int, dict]]:
        """
        Get cached response for the request.
        
        Returns:
            Tuple of (content, status_code, headers) if cached, None otherwise
        """
        if not self._should_cache_request(request):
            return None
        
        cache_key = self._generate_cache_key(request)
        
        try:
            cached_data = self.redis_client.get(cache_key)
            if cached_data:
                data = json.loads(cached_data)
                logger.info(f"Cache HIT for {request.method} {request.url.path}")
                
                # Clean headers - remove any cache-related headers from stored data (case-insensitive)
                headers = {}
                for key, value in data["headers"].items():
                    key_lower = key.lower()
                    if key_lower not in ['x-cache', 'x-process-time', 'x-cache-ttl']:
                        headers[key] = value
                
                return (
                    data["content"].encode() if isinstance(data["content"], str) else data["content"],
                    data["status_code"],
                    headers
                )
        except Exception as e:
            logger.error(f"Cache read error: {e}")
        
        logger.info(f"Cache MISS for {request.method} {request.url.path}")
        return None
    
    def cache_response(self, request: Request, response: Response, content: bytes, ttl: Optional[int] = None):
        """
        Cache the response.
        
        Args:
            request: The original request
            response: The response to cache
            content: Response content as bytes
            ttl: Cache TTL in seconds (uses default if None)
        """
        if not self._should_cache_response(request, response):
            return
        
        cache_key = self._generate_cache_key(request)
        cache_ttl = ttl or self.default_ttl
        
        try:
            # Prepare data for caching - clean headers first (case-insensitive)
            headers_to_store = {}
            for key, value in response.headers.items():
                key_lower = key.lower()
                if key_lower not in ['x-cache', 'x-process-time', 'x-cache-ttl']:
                    headers_to_store[key] = value
            
            cache_data = {
                "content": content.decode() if content else "",
                "status_code": response.status_code,
                "headers": headers_to_store,
                "cached_at": time.time()
            }
            
            # Store in Redis
            self.redis_client.setex(
                cache_key,
                cache_ttl,
                json.dumps(cache_data)
            )
            
            logger.info(f"Cached response for {request.method} {request.url.path} (TTL: {cache_ttl}s)")
            
        except Exception as e:
            logger.error(f"Cache write error: {e}")
    
    def _should_cache_request(self, request: Request) -> bool:
        """Determine if the request should be cached"""
        # Only cache GET requests
        if request.method != "GET":
            return False
        
        # Skip health checks
        if request.url.path == "/health":
            return False
        
        # Skip if caching is disabled via header
        if request.headers.get("Cache-Control") == "no-cache":
            return False
        
        return True
    
    def _should_cache_response(self, request: Request, response: Response) -> bool:
        """Determine if the response should be cached"""
        # Only cache successful responses
        if response.status_code < 200 or response.status_code >= 300:
            return False
        
        # Skip if response has cache-control headers preventing caching
        cache_control = response.headers.get("Cache-Control", "")
        if "no-cache" in cache_control or "no-store" in cache_control:
            return False
        
        return True
    
    def invalidate_cache_pattern(self, pattern: str):
        """Invalidate cache entries matching a pattern"""
        try:
            keys = self.redis_client.keys(f"{self.cache_prefix}{pattern}")
            if keys:
                self.redis_client.delete(*keys)
                logger.info(f"Invalidated {len(keys)} cache entries matching pattern: {pattern}")
        except Exception as e:
            logger.error(f"Cache invalidation error: {e}")


class CacheMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware for response caching"""
    
    def __init__(self, app, cache_ttl: int = None, cache_enabled: bool = None):
        super().__init__(app)
        self.cache = ResponseCache(cache_ttl or getattr(settings, 'CACHE_TTL', 300))
        self.cache_enabled = cache_enabled if cache_enabled is not None else getattr(settings, 'CACHE_ENABLED', True)
    
    async def dispatch(self, request: Request, call_next):
        # Skip caching if disabled
        if not self.cache_enabled:
            response = await call_next(request)
            response.headers["X-Cache"] = "DISABLED"
            return response
        
        # Try to get cached response
        cached = self.cache.get_cached_response(request)
        if cached:
            content, status_code, headers = cached
            # Ensure we have a clean X-Cache header for hits
            headers = dict(headers)  # Make a copy
            headers["X-Cache"] = "HIT"
            headers["X-Cache-TTL"] = str(self.cache.default_ttl)
            return StarletteResponse(
                content=content,
                status_code=status_code,
                headers=headers
            )
        
        # Process request normally
        start_time = time.time()
        response = await call_next(request)
        process_time = time.time() - start_time
        
        # Add cache miss headers
        response.headers["X-Cache"] = "MISS"
        response.headers["X-Process-Time"] = f"{process_time:.4f}"
        
        # Cache the response if appropriate
        if hasattr(response, 'body'):
            content = response.body
        else:
            # For streaming responses, we need to read the content
            content = b""
            async for chunk in response.body_iterator:
                content += chunk
            
            # Create new response with the content, removing cache headers
            headers = dict(response.headers)
            headers.pop("X-Cache", None)  # Remove X-Cache before storing
            headers.pop("X-Process-Time", None)  # Remove process time before storing
            
            response = StarletteResponse(
                content=content,
                status_code=response.status_code,
                headers=headers,
                media_type=response.media_type
            )
            
            # Re-add the MISS headers for this response
            response.headers["X-Cache"] = "MISS"
            response.headers["X-Process-Time"] = f"{process_time:.4f}"
        
        # Cache the response (this will clean headers before storing)
        self.cache.cache_response(request, response, content)
        
        return response
