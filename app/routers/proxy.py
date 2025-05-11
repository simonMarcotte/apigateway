from fastapi import APIRouter, Request, HTTPException
from starlette.responses import Response
import httpx

from ..config import settings


router = APIRouter()

@router.get("/health")
async def health_check():
    """
    Health check endpoint.
    """
    return {"status": "healthy"}

@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_request(request: Request, path: str):
    """
    Proxy request to the target service.
    """
    # Construct the target URL
    url = httpx.URL(settings.DOWNSTREAM_URL).join(path)
    
    headers = dict(request.headers)
    headers.pop("host", None)

    # Create a client for making requests
    async with httpx.AsyncClient() as client:
        try:
            upstream = await client.request(
                request.method,
                url,
                content=await request.body(),
                headers=headers,
                params=request.query_params,
                timeout=30.0
            )
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail="Bad Gateway")

    # Return the response from the target service
    return Response(content=upstream.content, status_code=upstream.status_code, headers=dict(upstream.headers))