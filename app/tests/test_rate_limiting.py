import pytest
import time
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware.rate_limit import RateLimitMiddleware, RedisRateLimiter
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


class TestRedisRateLimiter:
    """Test core RedisRateLimiter functionality"""
    
    def test_basic_token_bucket(self, redis_client):
        """Test basic token bucket algorithm"""
        limiter = RedisRateLimiter(3)
        client_id = "test-client"
        
        # First 3 requests should not be limited
        for i in range(3):
            is_limited, remaining = limiter.is_rate_limited(client_id)
            assert not is_limited
            assert remaining == 2 - i
        
        # 4th request should be limited (bucket is empty)
        is_limited, remaining = limiter.is_rate_limited(client_id)
        assert is_limited
        assert remaining == 0

    def test_token_refill_over_time(self, redis_client):
        """Test that tokens are refilled over time"""
        # 2 tokens max, with refill of 2 tokens per second
        limiter = RedisRateLimiter(2, window_size_seconds=1)
        client_id = "test-client-refill"
        
        # Use up all tokens
        limiter.is_rate_limited(client_id)
        limiter.is_rate_limited(client_id)
        
        # Should be limited now
        is_limited, _ = limiter.is_rate_limited(client_id)
        assert is_limited
        
        # Wait for half a second (should refill 1 token)
        time.sleep(0.5)
        
        # Should be able to make one request now
        is_limited, remaining = limiter.is_rate_limited(client_id)
        assert not is_limited
        assert remaining == 0
        
        # Next request should be limited
        is_limited, _ = limiter.is_rate_limited(client_id)
        assert is_limited
        
        # Wait for full refill
        time.sleep(1.0)
        is_limited, remaining = limiter.is_rate_limited(client_id)
        assert not is_limited
        assert remaining == 1

    def test_client_isolation(self, redis_client):
        """Test that different clients have separate token buckets"""
        limiter = RedisRateLimiter(2)
        
        # Client 1 uses all tokens
        limiter.is_rate_limited("test-client1")
        limiter.is_rate_limited("test-client1")
        
        # Client 1 should be limited now
        is_limited, _ = limiter.is_rate_limited("test-client1")
        assert is_limited
        
        # Client 2 should still have a full bucket
        is_limited, remaining = limiter.is_rate_limited("test-client2")
        assert not is_limited
        assert remaining == 1

    def test_distributed_persistence(self, redis_client):
        """Test that rate limit state persists across limiter instances"""
        limiter1 = RedisRateLimiter(3)
        client_id = "test-persistence"
        
        # Use 2 tokens with first limiter instance
        limiter1.is_rate_limited(client_id)
        limiter1.is_rate_limited(client_id)
        
        # Create new limiter instance (simulating different gateway instance)
        limiter2 = RedisRateLimiter(3)
        
        # Should have 1 token remaining from previous instance
        is_limited, remaining = limiter2.is_rate_limited(client_id)
        assert not is_limited
        assert remaining == 0
        
        # Next request should be limited
        is_limited, _ = limiter2.is_rate_limited(client_id)
        assert is_limited

    def test_redis_key_cleanup(self, redis_client):
        """Test that Redis keys are properly cleaned up with TTL"""
        limiter = RedisRateLimiter(5, window_size_seconds=1)
        client_id = "test-cleanup"
        
        # Make a request to create the key
        limiter.is_rate_limited(client_id)
        
        # Verify key exists
        key = f"rate_limit:{client_id}"
        assert redis_client.exists(key)
        
        # Check that TTL is set (should be window_size * 3)
        ttl = redis_client.ttl(key)
        assert ttl > 0
        assert ttl <= 3  # 3 seconds for window_size=1

    def test_error_handling_redis_unavailable(self, redis_client):
        """Test graceful fallback when Redis is unavailable"""
        limiter = RedisRateLimiter(3)
        
        # Mock Redis error by replacing the redis client with None
        original_client = limiter.redis_client
        limiter.redis_client = None
        
        # Should fallback gracefully (allow request)
        is_limited, remaining = limiter.is_rate_limited("test-error")
        assert not is_limited
        assert remaining == 3  # Should return max tokens as fallback
        
        # Restore client
        limiter.redis_client = original_client

    def test_zero_tokens(self, redis_client):
        """Test behavior with zero token bucket"""
        limiter = RedisRateLimiter(0)
        
        # Should always be limited
        is_limited, remaining = limiter.is_rate_limited("test-zero")
        assert is_limited
        assert remaining == 0

    def test_large_token_bucket(self, redis_client):
        """Test behavior with large token bucket"""
        limiter = RedisRateLimiter(1000)
        client_id = "test-large"
        
        # Should handle large numbers correctly
        for _ in range(100):
            is_limited, remaining = limiter.is_rate_limited(client_id)
            assert not is_limited
        
        # Should still have plenty of tokens
        assert remaining >= 900


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
