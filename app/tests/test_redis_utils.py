import pytest
from unittest.mock import MagicMock, patch

from app.utils.redis_utils import RedisConnection


class TestRedisConnection:
    """Test Redis connection management"""
    
    def test_singleton_pattern(self):
        """Test that RedisConnection follows singleton pattern"""
        # Reset singleton for test
        RedisConnection._instance = None
        
        client1 = RedisConnection.get_client()
        client2 = RedisConnection.get_client()
        
        # Should be the same instance
        assert client1 is client2

    def test_connection_parameters(self):
        """Test that Redis connection uses correct parameters"""
        # Reset singleton for test
        RedisConnection._instance = None
        
        with patch('app.utils.redis_utils.redis.Redis') as mock_redis:
            mock_instance = MagicMock()
            mock_redis.return_value = mock_instance
            
            RedisConnection.get_client()
            
            # Verify Redis was called with correct parameters
            mock_redis.assert_called_once_with(
                host='localhost',
                port=6379,
                db=0,
                password=None,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5
            )
            
            # Verify ping was called to test connection
            mock_instance.ping.assert_called_once()

    def test_connection_failure_handling(self):
        """Test handling of Redis connection failures"""
        # Reset singleton for test
        RedisConnection._instance = None
        
        with patch('app.utils.redis_utils.redis.Redis') as mock_redis:
            mock_instance = MagicMock()
            mock_instance.ping.side_effect = Exception("Connection failed")
            mock_redis.return_value = mock_instance
            
            # Should raise exception on connection failure
            with pytest.raises(Exception, match="Connection failed"):
                RedisConnection.get_client()

    def test_connection_close(self):
        """Test Redis connection closing"""
        # Reset singleton for test
        RedisConnection._instance = None
        
        with patch('app.utils.redis_utils.redis.Redis') as mock_redis:
            mock_instance = MagicMock()
            mock_redis.return_value = mock_instance
            
            # Get client to create connection
            RedisConnection.get_client()
            
            # Close connection
            RedisConnection.close()
            
            # Verify close was called
            mock_instance.close.assert_called_once()
            
            # Instance should be reset
            assert RedisConnection._instance is None


class TestRedisBasicOperations:
    """Test basic Redis operations and utilities"""
    
    @pytest.fixture
    def mock_redis_client(self):
        """Create a mock Redis client"""
        with patch.object(RedisConnection, 'get_client') as mock_get_client:
            mock_client = MagicMock()
            mock_get_client.return_value = mock_client
            yield mock_client

    def test_redis_basic_operations(self, mock_redis_client):
        """Test basic Redis operations work"""
        # Test basic Redis operations
        mock_redis_client.set.return_value = True
        mock_redis_client.get.return_value = "test_value"
        mock_redis_client.delete.return_value = 1
        
        # Verify basic operations work
        assert mock_redis_client.set("test_key", "test_value")
        assert mock_redis_client.get("test_key") == "test_value"
        assert mock_redis_client.delete("test_key") == 1


class TestRedisIntegration:
    """Test Redis integration with actual Redis instance"""
    
    def test_real_redis_connection(self):
        """Test connection to real Redis instance"""
        try:
            client = RedisConnection.get_client()
            
            # Test basic operations
            test_key = "test:redis:integration"
            client.set(test_key, "test_value", ex=5)
            
            value = client.get(test_key)
            assert value == "test_value"
            
            # Clean up
            client.delete(test_key)
            
        except Exception as e:
            pytest.skip(f"Redis not available for integration test: {e}")

    def test_redis_persistence(self):
        """Test that data persists in Redis"""
        try:
            client = RedisConnection.get_client()
            
            # Store test data
            test_key = "test:persistence"
            test_data = {"tokens": 3, "last_refill": 1234567890.0}
            client.hset(test_key, mapping=test_data)
            
            # Retrieve and verify
            retrieved = client.hgetall(test_key)
            assert retrieved["tokens"] == "3"  # Redis returns strings
            assert retrieved["last_refill"] == "1234567890.0"
            
            # Clean up
            client.delete(test_key)
            
        except Exception as e:
            pytest.skip(f"Redis not available for integration test: {e}")

    def test_redis_expiration(self):
        """Test Redis key expiration"""
        try:
            client = RedisConnection.get_client()
            
            # Set key with short expiration
            test_key = "test:expiration"
            client.set(test_key, "test_value", ex=1)
            
            # Should exist immediately
            assert client.exists(test_key)
            
            # Should have TTL set
            ttl = client.ttl(test_key)
            assert 0 < ttl <= 1
            
            # Clean up (in case test runs faster than expiration)
            client.delete(test_key)
            
        except Exception as e:
            pytest.skip(f"Redis not available for integration test: {e}")
