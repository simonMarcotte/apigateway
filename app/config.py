# app/config.py
from pydantic_settings import BaseSettings
from pydantic import ConfigDict

class Settings(BaseSettings):
    DOWNSTREAM_URL: str
    # JWT settings
    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    JWT_AUDIENCE: str = "your-audience"
    JWT_ISSUER: str = "your-issuer"
    
    # Rate limiting settings
    RATE_LIMIT_PER_MINUTE: int = 60
    RATE_LIMIT_WINDOW_SECONDS: int = 60
    RATE_LIMIT_ENABLED: bool = True
    
    # Redis settings
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str | None = None

    model_config = ConfigDict(env_file=".env")

settings = Settings()
