import httpx
from typing import Any, Dict, Optional, Union


class HttpClient:
    def __init__(self, timeout: int = 10, follow_redirects: bool = True):
        self.timeout = timeout
        self.follow_redirects = follow_redirects

    async def get(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> httpx.Response:
        async with httpx.AsyncClient(
            timeout=self.timeout, follow_redirects=self.follow_redirects
        ) as client:
            return await client.get(url, params=params, headers=headers)

    async def post(
        self,
        url: str,
        data: Union[Dict[str, Any], str, None] = None,
        json: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> httpx.Response:
        async with httpx.AsyncClient(
            timeout=self.timeout, follow_redirects=self.follow_redirects
        ) as client:
            return await client.post(url, data=data, json=json, headers=headers)

    async def put(
        self,
        url: str,
        json: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> httpx.Response:
        async with httpx.AsyncClient(
            timeout=self.timeout, follow_redirects=self.follow_redirects
        ) as client:
            return await client.put(url, json=json, headers=headers)

    async def patch(
        self,
        url: str,
        json: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> httpx.Response:
        async with httpx.AsyncClient(
            timeout=self.timeout, follow_redirects=self.follow_redirects
        ) as client:
            return await client.patch(url, json=json, headers=headers)

    async def delete(
        self, url: str, headers: Optional[Dict[str, str]] = None
    ) -> httpx.Response:
        async with httpx.AsyncClient(
            timeout=self.timeout, follow_redirects=self.follow_redirects
        ) as client:
            return await client.delete(url, headers=headers)
