import pytest
import jwt as pyjwt
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.config import settings
from app.middleware.auth import auth_middleware

# Override JWT settings for tests
settings.JWT_SECRET = "testsecret"
settings.JWT_ALGORITHM = "HS256"
settings.JWT_AUDIENCE = "test-aud"
settings.JWT_ISSUER = "test-iss"

@pytest.fixture
def client():
    app = FastAPI()
    # Add the middleware under test
    app.middleware("http")(auth_middleware)

    # Health endpoint auth skips
    @app.get("/health")
    async def health():
        return {"status": "ok"}

    # Protected endpoint
    @app.get("/protected")
    async def protected(request: Request):
        # middleware sets request.state.user
        return {"user": request.state.user}

    return TestClient(app, raise_server_exceptions=False)

def make_token(**claims):
    now = datetime.now(timezone.utc)
    payload = {
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        **claims,
    }
    return pyjwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)

def test_health_skips_auth(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

def test_missing_auth_header(client):
    resp = client.get("/protected")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Missing or invalid Authorization header"

def test_invalid_scheme(client):
    token = make_token(sub="user123")
    # Missing "Bearer " prefix
    resp = client.get("/protected", headers={"Authorization": token})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Missing or invalid Authorization header"

def test_malformed_token(client):
    resp = client.get("/protected", headers={"Authorization": "Bearer not.a.jwt"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Token invalid"

def test_expired_token(client, monkeypatch):
    # create token that expired 1 second ago
    now = datetime.now(timezone.utc)
    payload = {
        "iat": now - timedelta(minutes=2),
        "exp": now - timedelta(seconds=1),
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "sub": "user123"
    }
    expired = pyjwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    resp = client.get("/protected", headers={"Authorization": f"Bearer {expired}"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Token expired"

def test_valid_token_allows_request(client):
    token = make_token(sub="user123")
    resp = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    # Confirm request.state.user was set to our payload
    assert body["user"]["sub"] == "user123"
    assert body["user"]["iss"] == settings.JWT_ISSUER
    assert body["user"]["aud"] == settings.JWT_AUDIENCE
