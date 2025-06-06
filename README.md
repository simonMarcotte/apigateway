# Python API Gateway
Lightweight Python API Gateway built with FastAPI

## Features

- [x] Authentication (JWT with issuer/audience validation)
- [x] Rate Limiting (Distributed Redis-based token bucket algorithm)
- [x] Request Logging
- [x] Request Proxying
- [ ] Multiple Service Routing
- [ ] Circuit Breaker
- [ ] Response Caching
- [ ] Schema Validation
- [ ] API Key Management
- [ ] Metrics & Monitoring

## Configuration

```
# Required
DOWNSTREAM_URL=http://your-backend-service
JWT_SECRET=your-secret-key

# Optional (defaults shown)
JWT_ALGORITHM=HS256
JWT_AUDIENCE=your-audience
JWT_ISSUER=your-issuer
RATE_LIMIT_PER_MINUTE=60
RATE_LIMIT_WINDOW_SECONDS=60
RATE_LIMIT_ENABLED=true

# Redis Configuration (for distributed rate limiting)
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=  # Optional
```

References for later: [reference](https://github.com/MJ-API-Development/api-gateway/blob/master/src/prefetch/dynamic_urls.py)