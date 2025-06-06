import pytest
import time
import redis
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware.rate_limit import RateLimitMiddleware, RedisRateLimiter
from app.utils.redis_utils import RedisConnection


@pytest.fixture(scope="function")
def redis_client():
    """Get Redis client and clean up test keys after each test"""
    client = RedisConnection.get_client()
    
    # Clean up any existing test keys before test
    test_keys = client.keys("rate_limit:test-*")
    if test_keys:
        client.delete(*test_keys)
    
    yield client
    
    # Clean up test keys after test
    test_keys = client.keys("rate_limit:test-*")
    if test_keys:
        client.delete(*test_keys)


def test_redis_rate_limiter_basic(redis_client):
    """Test the core RedisRateLimiter functionality with token bucket"""
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


def test_redis_rate_limiter_token_refill(redis_client):
    """Test that tokens are refilled over time in Redis"""
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
    # Should have 0 tokens left after this request
    assert remaining == 0
    
    # Next request should be limited
    is_limited, _ = limiter.is_rate_limited(client_id)
    assert is_limited
    
    # Wait for full refill
    time.sleep(1.0)
    is_limited, remaining = limiter.is_rate_limited(client_id)
    assert not is_limited
    # Should have refilled both tokens and used one
    assert remaining == 1


def test_redis_rate_limiter_different_clients(redis_client):
    """Test that different clients have separate token buckets in Redis"""
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


def test_redis_rate_limiter_persistence(redis_client):
    """Test that rate limit state persists in Redis across limiter instances"""
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


def test_redis_rate_limiter_error_handling(redis_client):
    """Test rate limiter behavior when Redis is unavailable"""
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


@pytest.fixture
def app_client_redis():
    """Create a test FastAPI app with Redis rate limit middleware"""
    app = FastAPI()
    
    # 3 tokens max, refilled at 3 per second for faster testing
    app.add_middleware(
        RateLimitMiddleware, 
        rate_limit_per_minute=3,
        window_size_seconds=1
    )
    
    # Add test endpoints
    @app.get("/health")
    async def health():
        return {"status": "ok"}
    
    @app.get("/test")
    async def test_endpoint():
        return {"status": "ok"}
    
    client = TestClient(app)
    
    # Clean up any existing test keys
    redis_client = RedisConnection.get_client()
    test_keys = redis_client.keys("rate_limit:*")
    if test_keys:
        redis_client.delete(*test_keys)
    
    yield client
    
    # Clean up after test
    test_keys = redis_client.keys("rate_limit:*")
    if test_keys:
        redis_client.delete(*test_keys)


def test_redis_middleware_health_endpoint(app_client_redis):
    """Test that health endpoint bypasses Redis rate limiting"""
    # Make many requests to health endpoint - all should work
    for _ in range(10):
        response = app_client_redis.get("/health")
        assert response.status_code == 200


def test_redis_middleware_rate_limit_headers(app_client_redis):
    """Test that Redis rate limit headers are added to responses"""
    response = app_client_redis.get("/test")
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


def test_redis_middleware_enforces_rate_limit(app_client_redis):
    """Test that middleware enforces Redis rate limit"""
    # First 3 requests should succeed (use all tokens)
    for i in range(3):
        response = app_client_redis.get("/test")
        assert response.status_code == 200
        assert response.headers["X-RateLimit-Remaining"] == str(2 - i)
    
    # 4th request should be rate limited (bucket empty)
    response = app_client_redis.get("/test")
    assert response.status_code == 429
    assert response.json()["detail"] == "Too many requests"
    
    # Wait for a token to be refilled
    time.sleep(0.4) 
    
    # Should be able to make one request now
    response = app_client_redis.get("/test")
    assert response.status_code == 200
    assert response.headers["X-RateLimit-Remaining"] == "0"
    
    # Next request should be limited again
    response = app_client_redis.get("/test")
    assert response.status_code == 429


def test_redis_distributed_rate_limiting(app_client_redis):
    """Test distributed rate limiting across multiple client instances"""
    # Create second client (simulating different gateway instance)
    app = FastAPI()
    app.add_middleware(
        RateLimitMiddleware, 
        rate_limit_per_minute=3,
        window_size_seconds=1
    )
    
    @app.get("/test")
    async def test_endpoint():
        return {"status": "ok"}
    
    client2 = TestClient(app)
    
    # Use 2 tokens with first client
    app_client_redis.get("/test")
    app_client_redis.get("/test")
    
    # Use remaining token with second client - should work
    response = client2.get("/test")
    assert response.status_code == 200
    assert response.headers["X-RateLimit-Remaining"] == "0"
    
    # Next request from either client should be limited
    response1 = app_client_redis.get("/test")
    assert response1.status_code == 429
    
    response2 = client2.get("/test")
    assert response2.status_code == 429
