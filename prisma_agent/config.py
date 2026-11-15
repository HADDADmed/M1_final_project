"""Run configuration. Everything that influences the result lives here and is written to the run manifest."""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS_DIR = os.path.join(ROOT, "runs")
CACHE_DIR = os.path.join(ROOT, ".cache")


@dataclass
class ReviewConfig:
    question: str
    # retrieval
    sources: list[str] = field(default_factory=lambda: ["openalex", "pubmed", "arxiv", "semanticscholar"])
    max_per_query: int = 25                 # records requested per query and per source
    year_from: int = 2020
    # models (all local, served by Ollama)
    worker_model: str = "qwen3:8b"          # planner, search strategist, screeners, extraction, writer
    judge_model: str = "gpt-oss:latest"     # adjudicator + critic (different model family = independent check)
    embed_model: str = "nomic-embed-text"
    seed: int = 42
    temperature: float = 0.0
    # screening
    max_fulltext: int = 20                  # cap on studies going to full-text assessment (demo budget)
    # human-in-the-loop: "auto" = approve automatically (logged), "human" = pause the graph (interrupt)
    hitl: str = "auto"
    max_critic_rounds: int = 2

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:12]

    def to_dict(self) -> dict:
        return asdict(self)


def new_run_id(question: str) -> str:
    slug = "".join(c if c.isalnum() else "-" for c in question.lower())[:40].strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{slug}"


def run_dir(run_id: str) -> str:
    d = os.path.join(RUNS_DIR, run_id)
    os.makedirs(d, exist_ok=True)
    return d
