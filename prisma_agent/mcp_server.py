"""PRISMA-Agent as a Model Context Protocol server (stdio).

Any MCP client (an IDE, a desktop assistant, another agent) can search the literature, run a full
systematic review and read its report through these tools.

    python -m prisma_agent.mcp_server
"""
from __future__ import annotations

import json
import os

from mcp.server.fastmcp import FastMCP

from .config import RUNS_DIR, ReviewConfig, new_run_id
from .dedup import deduplicate
from .sources.clients import SEARCHERS

mcp = FastMCP("prisma-agent")


@mcp.tool()
async def search_literature(query: str, sources: str = "openalex,pubmed,arxiv", per_source: int = 10,
                            year_from: int = 2020) -> str:
    """Search scholarly databases (openalex, pubmed, arxiv, semanticscholar) and return deduplicated records."""
    recs = []
    for s in [x.strip() for x in sources.split(",") if x.strip() in SEARCHERS]:
        try:
            recs += await SEARCHERS[s](query, per_source, year_from)
        except Exception as e:  # one failing source must not break the tool
            recs += []
            print(f"{s} failed: {e}")
    unique, _ = deduplicate(recs)
    return json.dumps([{"id": r.id, "title": r.title, "year": r.year, "venue": r.venue, "doi": r.doi,
                        "sources": [r.source, *r.also_in], "url": r.url} for r in unique], indent=1)


@mcp.tool()
async def run_review(question: str, max_per_query: int = 15, max_fulltext: int = 10) -> str:
    """Run a complete autonomous PRISMA 2020 systematic review (takes several minutes). Returns run id and metrics."""
    from .cli import _run
    cfg = ReviewConfig(question=question, hitl="auto", max_per_query=max_per_query, max_fulltext=max_fulltext)
    res = await _run(cfg, new_run_id(question))
    return json.dumps({"run_id": os.path.basename(res["report_dir"]), "metrics": res["metrics"]}, indent=1)


@mcp.tool()
def list_reviews() -> str:
    """List finished reviews with their key metrics."""
    out = []
    for rid in sorted(os.listdir(RUNS_DIR)):
        m = os.path.join(RUNS_DIR, rid, "manifest.json")
        if os.path.exists(m):
            man = json.load(open(m))
            out.append({"run_id": rid, "question": man["config"]["question"], "metrics": man["metrics"]})
    return json.dumps(out, indent=1)


@mcp.tool()
def get_report(run_id: str) -> str:
    """Return the Markdown manuscript of a finished review."""
    p = os.path.realpath(os.path.join(RUNS_DIR, run_id, "report.md"))
    if not p.startswith(os.path.realpath(RUNS_DIR)) or not os.path.exists(p):
        return "No report for this run id."
    return open(p).read()


if __name__ == "__main__":
    mcp.run(transport="stdio")
