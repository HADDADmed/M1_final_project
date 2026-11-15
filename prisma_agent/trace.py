"""Audit trail: every agent decision, tool call and LLM call is appended to runs/<id>/trace.jsonl.

Subscribers (the web UI) receive the same events live.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

from langchain_core.callbacks import AsyncCallbackHandler

from .config import run_dir


class Trace:
    _registry: dict[str, "Trace"] = {}

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.path = os.path.join(run_dir(run_id), "trace.jsonl")
        self.t0 = time.time()
        self.queues: list[asyncio.Queue] = []
        self.llm_calls = 0
        self.llm_seconds = 0.0
        Trace._registry[run_id] = self

    @classmethod
    def get(cls, run_id: str) -> "Trace":
        return cls._registry.get(run_id) or cls(run_id)

    def log(self, kind: str, agent: str = "", **data: Any) -> dict:
        ev = {"t": round(time.time() - self.t0, 3), "ts": time.strftime("%H:%M:%S"), "kind": kind, "agent": agent, **data}
        with open(self.path, "a") as f:
            f.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
        for q in self.queues:
            q.put_nowait(ev)
        return ev

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self.queues.append(q)
        return q


class LLMTracer(AsyncCallbackHandler):
    """LangChain callback: records model, latency and token usage of every LLM call."""

    def __init__(self, trace: Trace, agent: str):
        self.trace, self.agent, self.starts = trace, agent, {}

    async def on_chat_model_start(self, serialized, messages, *, run_id, **kw):
        self.starts[run_id] = time.time()

    async def on_llm_end(self, response, *, run_id, **kw):
        dt = time.time() - self.starts.pop(run_id, time.time())
        self.trace.llm_calls += 1
        self.trace.llm_seconds += dt
        usage = {}
        try:
            msg = response.generations[0][0].message
            usage = getattr(msg, "usage_metadata", None) or {}
            model = msg.response_metadata.get("model", "")
        except Exception:
            model = ""
        self.trace.log("llm_call", self.agent, model=model, seconds=round(dt, 2),
                       in_tokens=usage.get("input_tokens"), out_tokens=usage.get("output_tokens"))
