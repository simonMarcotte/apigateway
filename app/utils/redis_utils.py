import redis
import json
import logging
from typing import Optional
from ..config import settings

logger = logging.getLogger("gateway")

class RedisConnection:
    _instance: Optional[redis.Redis] = None
    
    @classmethod
    def get_client(cls) -> redis.Redis:
        """Get Redis client instance (singleton pattern)"""
        if cls._instance is None:
            try:
                cls._instance = redis.Redis(
                    host=getattr(settings, 'REDIS_HOST', 'localhost'),
                    port=getattr(settings, 'REDIS_PORT', 6379),
                    db=getattr(settings, 'REDIS_DB', 0),
                    password=getattr(settings, 'REDIS_PASSWORD', None),
                    decode_responses=True,
                    socket_connect_timeout=5,
                    socket_timeout=5,
                    retry_on_timeout=True
                )
                # Test connection
                cls._instance.ping()
                logger.info("Redis connection established")
            except Exception as e:
                logger.error(f"Failed to connect to Redis: {e}")
                raise
        
        return cls._instance
    
    @classmethod
    def close(cls):
        """Close Redis connection"""
        if cls._instance:
            cls._instance.close()
            cls._instance = None
