"""Shared record model + a polite, cached HTTP client (the cache makes searches reproducible)."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re

import httpx
from pydantic import BaseModel, Field

from ..config import CACHE_DIR

UA = "prisma-agent/1.0 (academic systematic-review prototype)"


class Record(BaseModel):
    id: str = ""                       # stable id assigned after deduplication (S001, S002, ...)
    source: str
    source_id: str
    title: str
    abstract: str = ""
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str = ""
    doi: str = ""
    url: str = ""
    pdf_url: str = ""
    query: str = ""                    # the query that found it
    also_in: list[str] = Field(default_factory=list)   # other sources that returned the same work

    def citation(self) -> str:
        a = self.authors[0].split()[-1] if self.authors else "Anon."
        etal = " et al." if len(self.authors) > 1 else ""
        return f"{a}{etal} ({self.year or 'n.d.'})"


def clean(text: str | None) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


class CachedClient:
    """GET with an on-disk cache keyed by URL+params, retries with back-off on 429/5xx."""

    def __init__(self, name: str, min_interval: float = 0.4):
        self.name, self.min_interval = name, min_interval
        self.dir = os.path.join(CACHE_DIR, "http", name)
        os.makedirs(self.dir, exist_ok=True)
        self._lock = asyncio.Lock()
        self.hits = self.misses = 0

    async def get(self, url: str, params: dict | None = None, as_json: bool = True, retries: int = 4):
        key = hashlib.sha256((url + json.dumps(params or {}, sort_keys=True)).encode()).hexdigest()[:24]
        path = os.path.join(self.dir, key + (".json" if as_json else ".txt"))
        if os.path.exists(path):
            self.hits += 1
            with open(path) as f:
                return json.load(f) if as_json else f.read()
        self.misses += 1
        async with self._lock:                      # sequential + rate limited per source
            for attempt in range(retries):
                try:
                    async with httpx.AsyncClient(timeout=40, headers={"User-Agent": UA}, follow_redirects=True) as c:
                        r = await c.get(url, params=params)
                    if r.status_code in (429, 500, 502, 503, 504):
                        await asyncio.sleep(2 ** attempt * 1.5)
                        continue
                    r.raise_for_status()
                    data = r.json() if as_json else r.text
                    with open(path, "w") as f:
                        json.dump(data, f) if as_json else f.write(data)
                    await asyncio.sleep(self.min_interval)
                    return data
                except (httpx.TransportError, json.JSONDecodeError):
                    await asyncio.sleep(2 ** attempt)
            raise RuntimeError(f"{self.name}: request failed after {retries} attempts")
