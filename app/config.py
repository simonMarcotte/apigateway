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

    model_config = ConfigDict(env_file=".env")

settings = Settings()
