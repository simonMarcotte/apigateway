import pytest
import time
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware.rate_limit import RateLimitMiddleware, RateLimiter

def test_rate_limiter_basic():
    """Test the core RateLimiter class functionality with token bucket"""

    limiter = RateLimiter(3)
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


def test_rate_limiter_token_refill():
    """Test that tokens are refilled over time"""

    # 2 tokens max, with refill of 2 tokens per second
    limiter = RateLimiter(2, window_size_seconds=1)
    client_id = "test-client"
    
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


def test_rate_limiter_different_clients():
    """Test that different clients have separate token buckets"""
    limiter = RateLimiter(2)
    
    # Client 1 uses all tokens
    limiter.is_rate_limited("client1")
    limiter.is_rate_limited("client1")
    
    # Client 1 should be limited now
    is_limited, _ = limiter.is_rate_limited("client1")
    assert is_limited
    
    # Client 2 should still have a full bucket
    is_limited, remaining = limiter.is_rate_limited("client2")
    assert not is_limited
    assert remaining == 1


def test_rate_limiter_cleanup():
    """Test that old token bucket entries get cleaned up"""
    # Create limiter with a small window for testing
    limiter = RateLimiter(5, window_size_seconds=1)
    
    # Initialize buckets for two clients
    limiter.is_rate_limited("client1")
    limiter.is_rate_limited("client2")
    
    # Verify buckets exist
    assert "client1" in limiter.buckets
    assert "client2" in limiter.buckets
    
    # Modify last refill time to be old
    now = time.time()
    old_time = now - 5  # 5 seconds ago
    tokens, _ = limiter.buckets["client1"]
    limiter.buckets["client1"] = (tokens, old_time)
    limiter.buckets["client2"] = (tokens, old_time)
    
    # Clean up (should remove entries older than 3 window sizes)
    limiter.clean_old_entries()
    
    # Entries should be gone now
    assert "client1" not in limiter.buckets
    assert "client2" not in limiter.buckets


@pytest.fixture
def app_client():
    """Create a test FastAPI app with the token bucket rate limit middleware"""
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
    
    return TestClient(app)


def test_middleware_health_endpoint(app_client):
    """Test that health endpoint bypasses rate limiting with token bucket"""
    # Make many requests to health endpoint - all should work
    for _ in range(10):
        response = app_client.get("/health")
        assert response.status_code == 200


def test_middleware_rate_limit_headers_token_bucket(app_client):
    """Test that token bucket rate limit headers are added to responses"""
    response = app_client.get("/test")
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


def test_middleware_enforces_token_bucket_rate_limit(app_client):
    """Test that middleware enforces the token bucket rate limit"""
    # First 3 requests should succeed (use all tokens)
    for i in range(3):
        response = app_client.get("/test")
        assert response.status_code == 200
        assert response.headers["X-RateLimit-Remaining"] == str(2 - i)
    
    # 4th request should be rate limited (bucket empty)
    response = app_client.get("/test")
    assert response.status_code == 429
    assert response.json()["detail"] == "Too many requests"
    
    # Wait for a token to be refilled
    time.sleep(0.4) 
    
    # Should be able to make one request now
    response = app_client.get("/test")
    assert response.status_code == 200
    assert response.headers["X-RateLimit-Remaining"] == "0"
    
    # Next request should be limited again
    response = app_client.get("/test")
    assert response.status_code == 429


def test_token_bucket_gradual_refill(app_client):
    """Test that tokens are gradually refilled over time"""
    # Use all tokens
    for _ in range(3):
        app_client.get("/test")
    
    # Should be rate limited now
    response = app_client.get("/test")
    assert response.status_code == 429
    
    # Wait a bit for partial refill 
    time.sleep(0.7)
    
    # Make 2 requests that should succeed
    for i in range(2):
        response = app_client.get("/test")
        assert response.status_code == 200
        
    # Third request should be limited again
    response = app_client.get("/test")
    assert response.status_code == 429
