"""Image downloader used by Silver to re-host every <img> of a crawled question."""

import httpx

from app.config import get_settings

MAX_IMAGE_BYTES = 5 * 1024 * 1024


class HttpDownloader:
    def __init__(self, timeout: float = 20):
        self._client = httpx.AsyncClient(
            timeout=timeout, follow_redirects=True, headers={"User-Agent": get_settings().crawler_user_agent}
        )

    async def download(self, url: str) -> tuple[bytes, str]:
        response = await self._client.get(url)
        response.raise_for_status()
        if len(response.content) > MAX_IMAGE_BYTES:
            raise OSError(f"Image too large: {url}")
        return response.content, response.headers.get("content-type", "application/octet-stream").split(";")[0]

    async def close(self) -> None:
        await self._client.aclose()
