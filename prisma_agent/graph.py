"""The PRISMA-Agent LangGraph workflow."""
from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from . import agents as A
from .report import write_report


class ReviewState(TypedDict, total=False):
    run_id: str
    config: dict
    protocol: dict
    protocol_approved_by: str
    strategy: dict
    search_round: int
    coordinator_feedback: str
    search_log: list
    raw_records: list
    records: list
    identified: dict
    duplicates: int
    screening: list
    kappa: float
    conflicts: int
    ta_included: list
    ft_sought: list
    fulltext: list
    included: list
    extractions: dict
    rob: dict
    grounding: float
    synthesis: dict
    critiques: list
    timings: dict
    report_dir: str
    prisma: dict
    checklist_coverage: float
    metrics: dict
    errors: Annotated[list[Any], operator.add]


async def report_node(state):
    return write_report(state)


def build_graph(checkpointer=None):
    retry = RetryPolicy(max_attempts=3, initial_interval=1.0)
    g = StateGraph(ReviewState)
    g.add_node("planner", A.planner, retry_policy=retry)
    g.add_node("protocol_review", A.protocol_review)              # HITL #1
    g.add_node("search_strategist", A.search_strategist, retry_policy=retry)
    g.add_node("retrieval", A.retrieval, retry_policy=retry)
    g.add_node("broaden", A.broaden)
    g.add_node("screening", A.ta_screening, retry_policy=retry)
    g.add_node("conflict_resolution", A.resolve_conflicts)       # HITL #2 or adjudicator
    g.add_node("fulltext", A.fulltext, retry_policy=retry)
    g.add_node("extraction", A.extraction, retry_policy=retry)
    g.add_node("synthesis", A.synthesis, retry_policy=retry)
    g.add_node("critic", A.critic, retry_policy=retry)
    g.add_node("report", report_node)

    g.add_edge(START, "planner")
    g.add_edge("planner", "protocol_review")
    g.add_edge("protocol_review", "search_strategist")
    g.add_edge("search_strategist", "retrieval")
    g.add_conditional_edges("retrieval", A.coordinator_after_retrieval, {"broaden": "broaden", "screen": "screening"})
    g.add_edge("broaden", "search_strategist")
    g.add_edge("screening", "conflict_resolution")
    g.add_conditional_edges("conflict_resolution", A.coordinator_after_screening, {"broaden": "broaden", "fulltext": "fulltext"})
    g.add_edge("fulltext", "extraction")
    g.add_edge("extraction", "synthesis")
    g.add_edge("synthesis", "critic")
    g.add_conditional_edges("critic", A.after_critic, {"revise": "synthesis", "report": "report"})
    g.add_edge("report", END)
    return g.compile(checkpointer=checkpointer)
