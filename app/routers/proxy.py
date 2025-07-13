from fastapi import APIRouter, Request, HTTPException
from starlette.responses import Response
import httpx
import time

from ..config import settings
from ..middleware.cache import ResponseCache


router = APIRouter()

@router.get("/health")
async def health_check():
    """
    Health check endpoint.
    """
    return {"status": "healthy"}


@router.get("/admin/cache/stats")
async def cache_stats():
    """
    Get cache statistics and information.
    """
    cache = ResponseCache()
    try:
        # Get Redis info
        redis_info = cache.redis_client.info()
        
        # Count cache keys
        cache_keys = cache.redis_client.keys(f"{cache.cache_prefix}*")
        
        return {
            "cache_enabled": settings.CACHE_ENABLED,
            "cache_ttl": settings.CACHE_TTL,
            "total_cache_keys": len(cache_keys),
            "redis_connected": True,
            "redis_memory_used": redis_info.get("used_memory_human", "unknown"),
            "redis_uptime": redis_info.get("uptime_in_seconds", 0)
        }
    except Exception as e:
        return {
            "cache_enabled": settings.CACHE_ENABLED,
            "cache_ttl": settings.CACHE_TTL,
            "error": str(e),
            "redis_connected": False
        }


@router.delete("/admin/cache")
async def clear_cache():
    """
    Clear all cache entries.
    """
    cache = ResponseCache()
    try:
        cache_keys = cache.redis_client.keys(f"{cache.cache_prefix}*")
        if cache_keys:
            cache.redis_client.delete(*cache_keys)
        
        return {
            "message": "Cache cleared successfully",
            "keys_deleted": len(cache_keys)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to clear cache: {str(e)}")


@router.delete("/admin/cache/{pattern}")
async def clear_cache_pattern(pattern: str):
    """
    Clear cache entries matching a pattern.
    """
    cache = ResponseCache()
    try:
        cache.invalidate_cache_pattern(pattern)
        return {"message": f"Cache entries matching '{pattern}' cleared"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to clear cache pattern: {str(e)}")


# Test endpoints for performance testing
@router.get("/test/slow")
async def slow_endpoint():
    """
    Test endpoint that simulates slow response for cache testing.
    """
    import asyncio
    await asyncio.sleep(0.1)  # 100ms delay
    return {
        "message": "This is a slow endpoint for testing cache performance",
        "timestamp": time.time(),
        "delay": "100ms"
    }


@router.get("/test/fast")
async def fast_endpoint():
    """
    Test endpoint for cache testing.
    """
    return {
        "message": "This is a fast endpoint for testing cache performance",
        "timestamp": time.time()
    }

@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_request(request: Request, path: str):
    """
    Proxy request to the target service.
    """
    # Construct the target URL
    url = httpx.URL(settings.DOWNSTREAM_URL).join(path)
    
    headers = dict(request.headers)
    headers.pop("host", None)

    # Create a client for making requests
    async with httpx.AsyncClient() as client:
        try:
            upstream = await client.request(
                request.method,
                url,
                content=await request.body(),
                headers=headers,
                params=request.query_params,
                timeout=30.0
            )
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail="Bad Gateway")

    # Return the response from the target service
    return Response(content=upstream.content, status_code=upstream.status_code, headers=dict(upstream.headers))