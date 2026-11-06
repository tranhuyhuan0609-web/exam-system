"""Google Drive (v3 REST, API key) for public folders: list children, download files, export Google Docs to PDF."""

import asyncio
from dataclasses import dataclass
from typing import Protocol

import httpx

API = "https://www.googleapis.com/drive/v3/files"
FOLDER = "application/vnd.google-apps.folder"
EXPORTS = {  # Google-native files are exported
    "application/vnd.google-apps.document": "application/pdf",
    "application/vnd.google-apps.presentation": "application/pdf",
}
MAX_BYTES = 60 * 1024 * 1024
RATE_LIMITED = {"userRateLimitExceeded", "rateLimitExceeded"}  # not downloadQuotaExceeded: daily, per file


def _reason(response: httpx.Response) -> str:
    if response.status_code < 400:
        return ""
    try:
        return response.json()["error"]["errors"][0]["reason"]
    except (ValueError, KeyError, IndexError, TypeError):
        return " ".join(response.text[:120].split())


@dataclass
class DriveFile:
    id: str
    name: str
    mime: str
    size: int = 0
    md5: str | None = None
    modified: str = ""  # RFC 3339; Google Docs have no md5, so their version is this

    @property
    def version(self) -> str:
        return self.md5 or (f"mtime:{self.modified}" if self.modified else "")

    @property
    def is_folder(self) -> bool:
        return self.mime == FOLDER


class DriveError(Exception):
    pass


class DriveBlocked(DriveError):
    """Google's "Sorry…" page: this IP sent too many requests. Stop and try again hours later."""


class Drive(Protocol):
    async def children(self, folder_id: str) -> list[DriveFile]: ...

    async def download(self, file: DriveFile) -> tuple[bytes, str]: ...  # (data, mime)


class DriveClient:
    def __init__(self, api_key: str, delay: float = 1.0, timeout: float = 120):
        if not api_key:
            raise DriveError("Chưa cấu hình GOOGLE_DRIVE_API_KEY (Google Cloud → APIs & Services → Credentials).")
        self._key, self._delay = api_key, delay
        # Key in a header, never in the URL: request URLs end up in logs.
        self._client = httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers={"x-goog-api-key": api_key})

    async def _get(self, url: str, params: dict) -> httpx.Response:
        for attempt in range(5):
            await asyncio.sleep(self._delay)  # gentle with a shared folder
            try:
                response = await self._client.get(url, params=params)
            except httpx.TransportError as error:  # dropped connection on a long download: try again
                if attempt == 4:
                    raise DriveError(f"Mạng lỗi khi tải từ Drive: {error!r}") from error
                await asyncio.sleep(2**attempt * 3)
                continue
            reason = _reason(response)
            if response.status_code == 429 or reason in RATE_LIMITED:
                await asyncio.sleep(2**attempt * 5)  # Drive throttles bursts of downloads: back off and retry
                continue
            break
        if response.status_code in (403, 429) and "<title>Sorry" in response.text[:400]:
            raise DriveBlocked("Google tạm chặn tải tự động từ IP này; chạy lại sau vài giờ.")
        if response.status_code in (403, 404, 429):
            raise DriveError(f"Drive từ chối ({response.status_code} {reason}): file không công khai hoặc bị giới hạn.")
        response.raise_for_status()
        return response

    async def children(self, folder_id: str) -> list[DriveFile]:
        files, token = [], None
        while True:
            params = {
                "q": f"'{folder_id}' in parents and trashed = false",
                "fields": "nextPageToken, files(id, name, mimeType, size, md5Checksum, modifiedTime)",
                "pageSize": 1000,
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            }
            if token:
                params["pageToken"] = token
            data = (await self._get(API, params)).json()
            files += [
                DriveFile(
                    f["id"],
                    f["name"],
                    f["mimeType"],
                    int(f.get("size") or 0),
                    f.get("md5Checksum"),
                    f.get("modifiedTime", ""),
                )
                for f in data.get("files", [])
            ]
            token = data.get("nextPageToken")
            if not token:
                return files

    async def download(self, file: DriveFile) -> tuple[bytes, str]:
        if file.size > MAX_BYTES:
            raise DriveError(f"File quá lớn ({file.size // 1_000_000} MB): {file.name}")
        if file.mime in EXPORTS:
            target = EXPORTS[file.mime]
            response = await self._get(f"{API}/{file.id}/export", {"mimeType": target})
            return response.content, target
        response = await self._get(f"{API}/{file.id}", {"alt": "media", "supportsAllDrives": "true"})
        return response.content, file.mime

    async def close(self) -> None:
        await self._client.aclose()
