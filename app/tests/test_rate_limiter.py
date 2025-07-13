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
    test_keys = client.keys("rate_limit:test-*")
    if test_keys:
        client.delete(*test_keys)
    
    yield client
    
    # Clean up test keys after test
    test_keys = client.keys("rate_limit:test-*")
    if test_keys:
        client.delete(*test_keys)


class TestRedisRateLimiterCore:
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


class TestRedisRateLimiterEdgeCases:
    """Test edge cases and error conditions"""
    
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
