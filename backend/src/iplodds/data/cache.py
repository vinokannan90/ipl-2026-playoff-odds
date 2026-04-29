"""Cache backed by Azure Blob with in-memory fallback for dev.

Stores small JSON blobs keyed by name. TTL enforced on read.
"""

from __future__ import annotations

import time
from typing import Any

import orjson
import structlog
from azure.core.exceptions import ResourceNotFoundError
from azure.identity.aio import DefaultAzureCredential
from azure.storage.blob.aio import BlobServiceClient

from iplodds.config import get_settings

log = structlog.get_logger(__name__)


class Cache:
    """Async key/value cache with TTL.

    In production: Azure Blob (one blob per key). In dev (no blob_account_url): in-memory.
    """

    def __init__(self) -> None:
        self._mem: dict[str, tuple[float, bytes]] = {}
        self._client: BlobServiceClient | None = None
        self._cred: DefaultAzureCredential | None = None
        self._container: str = get_settings().blob_container

    async def _ensure_client(self) -> BlobServiceClient | None:
        s = get_settings()
        if not s.blob_account_url:
            return None
        if self._client is None:
            self._cred = DefaultAzureCredential()
            self._client = BlobServiceClient(account_url=s.blob_account_url, credential=self._cred)
        return self._client

    async def get(self, key: str, ttl_s: int) -> Any | None:
        client = await self._ensure_client()
        if client is None:
            entry = self._mem.get(key)
            if not entry:
                return None
            ts, data = entry
            if time.time() - ts > ttl_s:
                return None
            return orjson.loads(data)

        try:
            blob = client.get_blob_client(self._container, key)
            props = await blob.get_blob_properties()
            age = time.time() - props.last_modified.timestamp()
            if age > ttl_s:
                return None
            stream = await blob.download_blob()
            data = await stream.readall()
            return orjson.loads(data)
        except ResourceNotFoundError:
            return None
        except Exception:
            log.exception("cache.get_failed", key=key)
            return None

    async def set(self, key: str, value: Any) -> None:
        data = orjson.dumps(value)
        client = await self._ensure_client()
        if client is None:
            self._mem[key] = (time.time(), data)
            return
        try:
            blob = client.get_blob_client(self._container, key)
            await blob.upload_blob(data, overwrite=True)
        except Exception:
            log.exception("cache.set_failed", key=key)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
        if self._cred is not None:
            await self._cred.close()
            self._cred = None


_cache: Cache | None = None


def get_cache() -> Cache:
    global _cache
    if _cache is None:
        _cache = Cache()
    return _cache
