"""PRISMA-Agent web app: FastAPI + Server-Sent Events.

    uvicorn prisma_agent.server:app --port 8000      # → http://127.0.0.1:8000
"""
from __future__ import annotations

import asyncio
import json
import os
import time

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from pydantic import BaseModel

from .config import ROOT, RUNS_DIR, ReviewConfig, new_run_id, run_dir
from .graph import build_graph
from .trace import Trace

STATIC = os.path.join(ROOT, "prisma_agent", "static")
app = FastAPI(title="PRISMA-Agent")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
TASKS: dict[str, asyncio.Task] = {}
STATUS: dict[str, str] = {}


def status_of(run_id: str) -> str:
    if run_id in STATUS:
        return STATUS[run_id]
    d = os.path.join(RUNS_DIR, run_id)
    if os.path.exists(os.path.join(d, "manifest.json")):
        return "done"
    tr = os.path.join(d, "trace.jsonl")                       # started from the CLI and still writing
    return "running" if os.path.exists(tr) and time.time() - os.path.getmtime(tr) < 120 else "stopped"


@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


class NewReview(BaseModel):
    question: str
    hitl: str = "human"
    max_per_query: int = 25
    max_fulltext: int = 20
    year_from: int = 2020


async def drive(run_id: str, inp):
    """Run (or resume) the graph until it finishes or pauses on a human-in-the-loop interrupt."""
    STATUS[run_id] = "running"
    tr = Trace.get(run_id)
    try:
        async with AsyncSqliteSaver.from_conn_string(os.path.join(run_dir(run_id), "checkpoints.db")) as saver:
            graph = build_graph(saver)
            res = await graph.ainvoke(inp, {"configurable": {"thread_id": run_id}, "recursion_limit": 60})
        if "__interrupt__" in res:
            STATUS[run_id] = "waiting"
            tr.log("interrupt", "graph", payload_type=res["__interrupt__"][0].value["type"])
        else:
            STATUS[run_id] = "done"
            tr.log("run", "graph", status="done", metrics=res.get("metrics"))
    except Exception as e:
        STATUS[run_id] = "error"
        tr.log("error", "graph", message=f"{type(e).__name__}: {e}"[:400])


@app.post("/api/reviews")
async def start(body: NewReview):
    cfg = ReviewConfig(question=body.question, hitl=body.hitl, max_per_query=body.max_per_query,
                       max_fulltext=body.max_fulltext, year_from=body.year_from)
    run_id = new_run_id(body.question)
    Trace.get(run_id).log("run", "api", status="start", question=cfg.question, fingerprint=cfg.fingerprint(), hitl=cfg.hitl)
    TASKS[run_id] = asyncio.create_task(drive(run_id, {"run_id": run_id, "config": cfg.to_dict()}))
    return {"run_id": run_id}


@app.get("/api/reviews")
async def list_runs():
    out = []
    for rid in sorted(os.listdir(RUNS_DIR), reverse=True):
        d = os.path.join(RUNS_DIR, rid)
        if not os.path.isdir(d) or not os.path.exists(os.path.join(d, "trace.jsonl")) or rid.endswith("-eval"):
            continue
        first = json.loads(open(os.path.join(d, "trace.jsonl")).readline() or "{}")
        man = os.path.join(d, "manifest.json")
        out.append({"run_id": rid, "question": first.get("question", ""), "status": status_of(rid),
                    "metrics": json.load(open(man))["metrics"] if os.path.exists(man) else None})
    return out


async def snapshot(run_id: str) -> dict:
    db = os.path.join(run_dir(run_id), "checkpoints.db")
    if not os.path.exists(db):
        return {}
    async with AsyncSqliteSaver.from_conn_string(db) as saver:
        st = await build_graph(saver).aget_state({"configurable": {"thread_id": run_id}})
    v = dict(st.values or {})
    v.pop("raw_records", None)
    pending = [t.interrupts[0].value for t in st.tasks if t.interrupts] if st.tasks else []
    return {"values": v, "next": list(st.next), "pending": pending[0] if pending else None}


@app.get("/api/reviews/{run_id}/state")
async def state(run_id: str):
    snap = await snapshot(run_id)
    snap["status"] = status_of(run_id)
    return snap


class Resume(BaseModel):
    value: dict


@app.post("/api/reviews/{run_id}/resume")
async def resume(run_id: str, body: Resume):
    if STATUS.get(run_id) == "running":
        raise HTTPException(409, "run is still executing")
    Trace.get(run_id).log("hitl", "human", status="answer", what=list(body.value))
    TASKS[run_id] = asyncio.create_task(drive(run_id, Command(resume=body.value)))
    return {"ok": True}


@app.get("/api/reviews/{run_id}/events")
async def events(run_id: str):
    tr = Trace.get(run_id)
    q = tr.subscribe()

    async def gen():
        if os.path.exists(tr.path):                                   # replay history first
            for line in open(tr.path):
                yield f"data: {line.strip()}\n\n"
        yield 'data: {"kind": "replayed"}\n\n'
        while True:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=15)
                yield f"data: {json.dumps(ev, default=str)}\n\n"
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.get("/runs/{run_id}/{name}")
async def artifact(run_id: str, name: str):
    path = os.path.realpath(os.path.join(RUNS_DIR, run_id, name))
    if not path.startswith(os.path.realpath(RUNS_DIR)) or not os.path.exists(path):
        raise HTTPException(404)
    return FileResponse(path)
