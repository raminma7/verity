"""Blob storage for uploaded documents and chatbot logos.

`LocalStorage` keeps files on disk (a Docker volume in compose). Keys are generated here, never taken from
the client, so a key can't escape the storage root. An S3 implementation can replace it behind the same
interface.
"""

import asyncio
import uuid
from pathlib import Path
from typing import Protocol

from app.core.config import get_settings


class Storage(Protocol):
    async def put(self, workspace_id: uuid.UUID, data: bytes, *, suffix: str = "") -> str: ...
    async def get(self, key: str) -> bytes: ...
    async def delete(self, key: str) -> None: ...


class LocalStorage:
    def __init__(self, root: str):
        self.root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root not in p.parents:
            raise ValueError("invalid storage key")
        return p

    async def put(self, workspace_id: uuid.UUID, data: bytes, *, suffix: str = "") -> str:
        key = f"ws/{workspace_id}/{uuid.uuid4().hex}{suffix}"
        path = self._path(key)

        def write():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".part")
            tmp.write_bytes(data)
            tmp.replace(path)

        await asyncio.to_thread(write)
        return key

    async def get(self, key: str) -> bytes:
        return await asyncio.to_thread(self._path(key).read_bytes)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._path(key).unlink, missing_ok=True)


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        _storage = LocalStorage(get_settings().storage_dir)
    return _storage
