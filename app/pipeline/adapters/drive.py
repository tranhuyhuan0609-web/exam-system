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


@dataclass
class DriveFile:
    id: str
    name: str
    mime: str
    size: int = 0
    md5: str | None = None

    @property
    def is_folder(self) -> bool:
        return self.mime == FOLDER


class DriveError(Exception):
    pass


class Drive(Protocol):
    async def children(self, folder_id: str) -> list[DriveFile]: ...

    async def download(self, file: DriveFile) -> tuple[bytes, str]: ...  # (data, mime)


class DriveClient:
    def __init__(self, api_key: str, delay: float = 0.3, timeout: float = 120):
        if not api_key:
            raise DriveError("Chưa cấu hình GOOGLE_DRIVE_API_KEY (Google Cloud → APIs & Services → Credentials).")
        self._key, self._delay = api_key, delay
        self._client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)

    async def _get(self, url: str, params: dict) -> httpx.Response:
        await asyncio.sleep(self._delay)  # gentle with a shared folder
        response = await self._client.get(url, params={**params, "key": self._key})
        if response.status_code in (403, 404):
            raise DriveError(
                f"Drive từ chối ({response.status_code}): thư mục / file không công khai hoặc API chưa bật."
            )
        response.raise_for_status()
        return response

    async def children(self, folder_id: str) -> list[DriveFile]:
        files, token = [], None
        while True:
            params = {
                "q": f"'{folder_id}' in parents and trashed = false",
                "fields": "nextPageToken, files(id, name, mimeType, size, md5Checksum)",
                "pageSize": 1000,
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            }
            if token:
                params["pageToken"] = token
            data = (await self._get(API, params)).json()
            files += [
                DriveFile(f["id"], f["name"], f["mimeType"], int(f.get("size") or 0), f.get("md5Checksum"))
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
