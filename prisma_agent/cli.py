"""Command line: python -m prisma_agent.cli run "your research question" """
from __future__ import annotations

import asyncio
import json
import os

import typer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from rich.console import Console

from .config import ReviewConfig, new_run_id, run_dir
from .graph import build_graph
from .trace import Trace

app = typer.Typer(add_completion=False, help="PRISMA-Agent: autonomous, transparent systematic reviews.")
con = Console()


async def _run(cfg: ReviewConfig, run_id: str, resume: bool = False):
    d = run_dir(run_id)
    tr = Trace.get(run_id)
    q = tr.subscribe()

    async def printer():
        while True:
            ev = await q.get()
            if ev["kind"] == "stage" and ev.get("status") == "start":
                con.print(f"[bold #4338ca]▶ {ev['title']}[/]  [dim]{ev['ts']}[/]")
            elif ev["kind"] in ("coordinator", "hitl"):
                con.print(f"  [yellow]◆ {ev['agent']}[/] {ev.get('decision') or ev.get('what')} [dim]{ev.get('reason') or ev.get('note') or ''}[/]")
            elif ev["kind"] == "tool" and ev["agent"] == "retrieval":
                con.print(f"  [dim]⌕ {ev['tool']}: {ev['hits']} hits[/]")

    task = asyncio.create_task(printer())
    async with AsyncSqliteSaver.from_conn_string(os.path.join(d, "checkpoints.db")) as saver:
        graph = build_graph(saver)
        config = {"configurable": {"thread_id": run_id}, "recursion_limit": 60}
        inp = None if resume else {"run_id": run_id, "config": cfg.to_dict()}
        if not resume:
            tr.log("run", "cli", status="start", question=cfg.question, fingerprint=cfg.fingerprint())
        while True:
            result = await graph.ainvoke(inp, config)
            if "__interrupt__" not in result:
                break
            payload = result["__interrupt__"][0].value                       # human-in-the-loop in the terminal
            if payload["type"] == "protocol_approval":
                con.print_json(json.dumps(payload["protocol"]))
                typer.confirm("Approve this protocol?", default=True, abort=True)
                inp = Command(resume={"protocol": payload["protocol"]})
            else:
                decisions = {}
                for c in payload["conflicts"]:
                    con.print(f"[bold]{c['id']}[/] {c['title']}\n  A={c['a']}: {c['a_reason']}\n  B={c['b']}: {c['b_reason']}")
                    decisions[c["id"]] = "include" if typer.confirm("Include?", default=c["a"] == "include") else "exclude"
                inp = Command(resume={"decisions": decisions})
    task.cancel()
    m = result["metrics"]
    con.print(f"\n[bold green]✓ Review complete[/] → {result['report_dir']}")
    con.print(m)
    return result


@app.command()
def run(question: str, hitl: str = typer.Option("auto", help="auto | human"),
        max_per_query: int = 25, year_from: int = 2020, max_fulltext: int = 20, run_id: str = ""):
    """Run a full systematic review on QUESTION."""
    cfg = ReviewConfig(question=question, hitl=hitl, max_per_query=max_per_query, year_from=year_from,
                       max_fulltext=max_fulltext)
    asyncio.run(_run(cfg, run_id or new_run_id(question)))



@app.command()
def replay(run_id: str, from_node: str = typer.Option("extraction", help="node to re-run from"),
           set_: list[str] = typer.Option([], "--set", help="override a config value at the fork, e.g. --set max_critic_rounds=4")):
    """Time travel: fork the run from the checkpoint just before FROM_NODE and re-execute the rest."""
    async def go():
        tr = Trace.get(run_id)
        async with AsyncSqliteSaver.from_conn_string(os.path.join(run_dir(run_id), "checkpoints.db")) as saver:
            graph = build_graph(saver)
            cfg = {"configurable": {"thread_id": run_id}, "recursion_limit": 60}
            target = None
            async for snap in graph.aget_state_history(cfg):
                if snap.next and snap.next[0] == from_node:
                    target = snap
                    break
            if target is None:
                raise SystemExit(f"no checkpoint before {from_node}")
            fork = target.config
            if set_:
                conf = dict(target.values["config"])
                for kv in set_:
                    k, v = kv.split("=", 1)
                    conf[k] = type(conf[k])(v) if k in conf and not isinstance(conf[k], list) else v
                prev = [n for n in ("extraction", "fulltext", "conflict_resolution", "screening", "retrieval", "search_strategist",
                                    "protocol_review", "planner", "critic", "synthesis") if n != from_node]
                as_node = {"synthesis": "extraction", "extraction": "fulltext", "critic": "synthesis", "report": "critic",
                           "fulltext": "conflict_resolution"}.get(from_node, prev[0])
                fork = await graph.aupdate_state(target.config, {"config": conf}, as_node=as_node)
            tr.log("run", "cli", status="replay", from_node=from_node, checkpoint=target.config["configurable"]["checkpoint_id"],
                   overrides=set_)
            con.print(f"[bold]↺ replaying {run_id} from checkpoint before '{from_node}'[/]")
            res = await graph.ainvoke(None, {**fork, "recursion_limit": 60})
            con.print(res["metrics"])
    asyncio.run(go())


@app.command()
def show(run_id: str):
    """Print the metrics of a finished run."""
    con.print_json(open(os.path.join(run_dir(run_id), "manifest.json")).read())


if __name__ == "__main__":
    app()
