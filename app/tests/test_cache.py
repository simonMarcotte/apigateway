import pytest
import time
import asyncio
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.middleware.cache import CacheMiddleware, ResponseCache
from app.utils.redis_utils import RedisConnection


@pytest.fixture(scope="function")
def redis_client():
    """Get Redis client and clean up test keys after each test"""
    client = RedisConnection.get_client()
    
    # Clean up any existing test keys before test
    test_keys = client.keys("cache:*")
    if test_keys:
        client.delete(*test_keys)
    
    yield client
    
    # Clean up test keys after test
    test_keys = client.keys("cache:*")
    if test_keys:
        client.delete(*test_keys)


@pytest.fixture
def mock_slow_backend():
    """Mock a slow backend service for latency testing"""
    async def slow_response():
        await asyncio.sleep(0.1)  # 100ms delay
        return {"message": "slow response", "timestamp": time.time()}
    
    return slow_response


@pytest.fixture
def app_with_cache(mock_slow_backend, redis_client):
    """Create FastAPI app with caching enabled"""
    app = FastAPI()
    
    # Add cache middleware
    app.add_middleware(CacheMiddleware, cache_ttl=60, cache_enabled=True)
    
    @app.get("/health")
    async def health():
        return {"status": "ok"}
    
    @app.get("/slow")
    async def slow_endpoint():
        return await mock_slow_backend()
    
    @app.get("/fast")
    async def fast_endpoint():
        return {"message": "fast response", "timestamp": time.time()}
    
    @app.get("/no-cache")
    async def no_cache_endpoint(request: Request):
        # This endpoint sets no-cache header
        return {"message": "never cached", "timestamp": time.time()}
    
    return app


@pytest.fixture
def app_without_cache(mock_slow_backend, redis_client):
    """Create FastAPI app with caching disabled"""
    app = FastAPI()
    
    # Add cache middleware but disabled
    app.add_middleware(CacheMiddleware, cache_ttl=60, cache_enabled=False)
    
    @app.get("/health")
    async def health():
        return {"status": "ok"}
    
    @app.get("/slow")
    async def slow_endpoint():
        return await mock_slow_backend()
    
    @app.get("/fast")
    async def fast_endpoint():
        return {"message": "fast response", "timestamp": time.time()}
    
    return app


def test_cache_basic_functionality(app_with_cache):
    """Test basic cache hit/miss functionality"""
    client = TestClient(app_with_cache)
    
    # First request should be a cache miss
    response1 = client.get("/fast")
    assert response1.status_code == 200
    assert response1.headers["X-Cache"] == "MISS"
    
    # Second request should be a cache hit
    response2 = client.get("/fast")
    assert response2.status_code == 200
    assert response2.headers["X-Cache"] == "HIT"
    
    # Responses should be identical
    assert response1.json()["message"] == response2.json()["message"]


def test_cache_disabled(app_without_cache):
    """Test that caching can be disabled"""
    client = TestClient(app_without_cache)
    
    # All requests should have cache disabled
    response1 = client.get("/fast")
    assert response1.status_code == 200
    assert response1.headers["X-Cache"] == "DISABLED"
    
    response2 = client.get("/fast")
    assert response2.status_code == 200
    assert response2.headers["X-Cache"] == "DISABLED"


def test_cache_health_endpoint_bypass(app_with_cache):
    """Test that health endpoint bypasses cache"""
    client = TestClient(app_with_cache)
    
    # Health endpoint should not be cached
    response1 = client.get("/health")
    assert response1.status_code == 200
    assert response1.headers["X-Cache"] == "MISS"
    
    response2 = client.get("/health")
    assert response2.status_code == 200
    assert response2.headers["X-Cache"] == "MISS"


def test_cache_only_get_requests(app_with_cache):
    """Test that only GET requests are cached"""
    client = TestClient(app_with_cache)
    
    # POST requests should not be cached
    response1 = client.post("/fast", json={"test": "data"})
    if response1.status_code == 200:  # If endpoint exists
        # Should not have cache headers or should be MISS always
        pass


def test_cache_key_generation():
    """Test cache key generation for different scenarios"""
    """Test cache key generation for different scenarios"""
    cache = ResponseCache()
    
    # Mock requests with different parameters
    class MockRequest:
        def __init__(self, method, path, query="", user=None):
            self.method = method
            self.url = MockURL(path, query)
            if user:
                self.state = MockState(user)
            else:
                self.state = MockState(None)
    
    class MockURL:
        def __init__(self, path, query=""):
            self.path = path
            self.query = query
    
    class MockState:
        def __init__(self, user):
            self.user = user
    
    # Test different cache keys are generated
    req1 = MockRequest("GET", "/api/data")
    req2 = MockRequest("GET", "/api/data", "filter=test")
    req3 = MockRequest("GET", "/api/data", user={"sub": "user123"})
    
    key1 = cache._generate_cache_key(req1)
    key2 = cache._generate_cache_key(req2)
    key3 = cache._generate_cache_key(req3)
    
    # All keys should be different
    assert key1 != key2
    assert key1 != key3
    assert key2 != key3
    
    # Keys should be consistent for same request
    key1_again = cache._generate_cache_key(req1)
    assert key1 == key1_again


def test_cache_ttl_expiration():
    """Test that cache entries expire according to TTL"""
    # Create app with very short TTL
    app = FastAPI()
    app.add_middleware(CacheMiddleware, cache_ttl=1, cache_enabled=True)  # 1 second TTL
    
    @app.get("/test")
    async def test_endpoint():
        return {"timestamp": time.time()}
    
    client = TestClient(app)
    
    # First request - cache miss
    response1 = client.get("/test")
    assert response1.headers["X-Cache"] == "MISS"
    timestamp1 = response1.json()["timestamp"]
    
    # Second request immediately - cache hit
    response2 = client.get("/test")
    assert response2.headers["X-Cache"] == "HIT"
    timestamp2 = response2.json()["timestamp"]
    assert timestamp1 == timestamp2  # Should be same cached response
    
    # Wait for cache to expire
    time.sleep(1.5)
    
    # Third request - should be cache miss again
    response3 = client.get("/test")
    assert response3.headers["X-Cache"] == "MISS"
    timestamp3 = response3.json()["timestamp"]
    assert timestamp3 != timestamp1  # Should be new response


if __name__ == "__main__":
    # Run basic cache tests when called directly
    pytest.main(["-v", __file__ + "::test_cache_basic_functionality"])
    pytest.main(["-v", __file__ + "::test_cache_ttl_expiration"])
