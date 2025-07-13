import pytest
import time
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware.rate_limit import RateLimitMiddleware
from app.utils.redis_utils import RedisConnection


@pytest.fixture(scope="function")
def redis_client():
    """Get Redis client and clean up test keys after each test"""
    client = RedisConnection.get_client()
    
    # Clean up any existing test keys before test
    test_keys = client.keys("rate_limit:*")
    if test_keys:
        client.delete(*test_keys)
    
    yield client
    
    # Clean up test keys after test
    test_keys = client.keys("rate_limit:*")
    if test_keys:
        client.delete(*test_keys)


@pytest.fixture
def app_with_rate_limiting(redis_client):
    """Create a test FastAPI app with rate limiting middleware"""
    app = FastAPI()
    
    # 3 tokens max, refilled at 3 per second for faster testing
    app.add_middleware(
        RateLimitMiddleware, 
        rate_limit_per_minute=3,
        window_size_seconds=1
    )
    
    @app.get("/health")
    async def health():
        return {"status": "ok"}
    
    @app.get("/api/data")
    async def api_data():
        return {"data": "test"}
    
    @app.post("/api/create")
    async def api_create():
        return {"created": True}
    
    return app


class TestRateLimitMiddleware:
    """Test RateLimitMiddleware integration with FastAPI"""
    
    def test_health_endpoint_bypass(self, app_with_rate_limiting):
        """Test that health endpoint bypasses rate limiting"""
        client = TestClient(app_with_rate_limiting)
        
        # Make many requests to health endpoint - all should work
        for _ in range(10):
            response = client.get("/health")
            assert response.status_code == 200

    def test_rate_limit_headers_added(self, app_with_rate_limiting):
        """Test that rate limit headers are added to responses"""
        client = TestClient(app_with_rate_limiting)
        
        response = client.get("/api/data")
        assert response.status_code == 200
        
        # Check headers
        assert "X-RateLimit-Limit" in response.headers
        assert "X-RateLimit-Remaining" in response.headers
        assert "X-RateLimit-Reset" in response.headers
        
        # First request should have max_tokens-1 tokens remaining
        assert response.headers["X-RateLimit-Limit"] == "3"
        assert response.headers["X-RateLimit-Remaining"] == "2"
        
        # Reset time should be in the future
        reset_time = int(response.headers["X-RateLimit-Reset"])
        assert reset_time > time.time()

    def test_rate_limit_enforcement(self, app_with_rate_limiting):
        """Test that middleware enforces rate limits"""
        client = TestClient(app_with_rate_limiting)
        
        # First 3 requests should succeed (use all tokens)
        for i in range(3):
            response = client.get("/api/data")
            assert response.status_code == 200
            expected_remaining = 2 - i
            actual_remaining = int(response.headers["X-RateLimit-Remaining"])
            assert actual_remaining == expected_remaining
        
        # 4th request should be rate limited (bucket empty)
        response = client.get("/api/data")
        assert response.status_code == 429
        assert response.json()["detail"] == "Too many requests"

    def test_rate_limit_recovery(self, app_with_rate_limiting):
        """Test that rate limit recovers after token refill"""
        client = TestClient(app_with_rate_limiting)
        
        # Exhaust tokens
        for _ in range(3):
            client.get("/api/data")
        
        # Should be rate limited
        response = client.get("/api/data")
        assert response.status_code == 429
        
        # Wait for token refill
        time.sleep(0.4)
        
        # Should be able to make one request now
        response = client.get("/api/data")
        assert response.status_code == 200
        assert response.headers["X-RateLimit-Remaining"] == "0"
        
        # Next request should be limited again
        response = client.get("/api/data")
        assert response.status_code == 429

    def test_gradual_token_refill(self, app_with_rate_limiting):
        """Test that tokens are gradually refilled over time"""
        client = TestClient(app_with_rate_limiting)
        
        # Use all tokens
        for _ in range(3):
            client.get("/api/data")
        
        # Should be rate limited now
        response = client.get("/api/data")
        assert response.status_code == 429
        
        # Wait a bit for partial refill 
        time.sleep(0.7)
        
        # Make 2 requests that should succeed
        for i in range(2):
            response = client.get("/api/data")
            assert response.status_code == 200
            
        # Third request should be limited again
        response = client.get("/api/data")
        assert response.status_code == 429

    def test_different_endpoints_share_limit(self, app_with_rate_limiting):
        """Test that different endpoints share the same rate limit per client"""
        client = TestClient(app_with_rate_limiting)
        
        # Use tokens across different endpoints
        response1 = client.get("/api/data")
        assert response1.status_code == 200
        assert response1.headers["X-RateLimit-Remaining"] == "2"
        
        response2 = client.post("/api/create")
        assert response2.status_code == 200
        assert response2.headers["X-RateLimit-Remaining"] == "1"
        
        response3 = client.get("/api/data")
        assert response3.status_code == 200
        assert response3.headers["X-RateLimit-Remaining"] == "0"
        
        # Next request should be limited regardless of endpoint
        response4 = client.post("/api/create")
        assert response4.status_code == 429


class TestRateLimitConfiguration:
    """Test rate limit configuration options"""
    
    def test_disabled_rate_limiting(self):
        """Test that rate limiting can be disabled"""
        app = FastAPI()
        
        # Don't add rate limiting middleware
        @app.get("/test")
        async def test_endpoint():
            return {"status": "ok"}
        
        client = TestClient(app)
        
        # Should be able to make many requests without rate limiting
        for _ in range(10):
            response = client.get("/test")
            assert response.status_code == 200
            assert "X-RateLimit-Limit" not in response.headers

    def test_custom_rate_limits(self, redis_client):
        """Test custom rate limit configuration"""
        app = FastAPI()
        app.add_middleware(
            RateLimitMiddleware, 
            rate_limit_per_minute=5,  # Custom limit
            window_size_seconds=2     # Custom window
        )
        
        @app.get("/test")
        async def test_endpoint():
            return {"status": "ok"}
        
        client = TestClient(app)
        
        response = client.get("/test")
        assert response.status_code == 200
        assert response.headers["X-RateLimit-Limit"] == "5"
        assert response.headers["X-RateLimit-Remaining"] == "4"
