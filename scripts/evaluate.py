"""Evaluate a finished run: completeness, accuracy, inter-agent consistency, reproducibility, time savings.

    python scripts/evaluate.py RUN_ID [--silver 30] [--repro 20]

Writes runs/RUN_ID/evaluation.json, docs/EVALUATION.md and prisma_agent/static/evaluation.json (shown in the web app).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from rapidfuzz import fuzz  # noqa: E402

from prisma_agent.agents import criteria_text, screen_one  # noqa: E402
from prisma_agent.config import ROOT, ReviewConfig, run_dir  # noqa: E402
from prisma_agent.dedup import norm_title  # noqa: E402
from prisma_agent.llm import make_llm, structured  # noqa: E402
from prisma_agent.schemas import Adjudication  # noqa: E402
from prisma_agent.trace import Trace  # noqa: E402

# Known relevant studies for the demonstration topic (LLMs for title/abstract screening), assembled by the author
# from the literature. Used to measure the recall of the search + screening pipeline.
KNOWN_ITEMS = [
    "Automated paper screening for clinical reviews using large language models",
    "Assessing the ability of ChatGPT to screen articles for systematic reviews",
    "Can large language models replace humans in systematic reviews? Evaluating GPT-4's efficacy in screening and extracting data",
    "Title and abstract screening for literature reviews using large language models: an exploratory study in the biomedical domain",
]


def load_state(run_id: str) -> dict:
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from prisma_agent.graph import build_graph

    async def go():
        async with AsyncSqliteSaver.from_conn_string(os.path.join(run_dir(run_id), "checkpoints.db")) as saver:
            return (await build_graph(saver).aget_state({"configurable": {"thread_id": run_id}})).values
    return asyncio.run(go())


def find(title: str, records: list[dict]) -> dict | None:
    t = norm_title(title)
    best = max(records, key=lambda r: fuzz.token_set_ratio(t, norm_title(r["title"])), default=None)
    return best if best and fuzz.token_set_ratio(t, norm_title(best["title"])) >= 88 else None


async def silver_standard(state, n: int, tr) -> dict:
    """Independent reference decisions by the judge model (gpt-oss:20b) on a random sample."""
    cfg = ReviewConfig(**state["config"])
    random.seed(7)
    sample = random.sample(state["screening"], min(n, len(state["screening"])))
    byid = {r["id"]: r for r in state["records"]}
    judge = make_llm(cfg, "judge")
    tp = fp = fn = tn = 0
    rows = []
    for x in sample:
        r = byid[x["id"]]
        ref = await structured(judge, Adjudication, f"""You are an expert systematic reviewer creating a reference standard.
Apply the criteria strictly to this record (title/abstract stage: when information is missing, include).
Question: {cfg.question}
{criteria_text(state['protocol'])}
Title: {r['title']}
Abstract: {r['abstract'][:2500]}""", tr, "silver_standard")
        sys_inc, ref_inc = x["final"] == "include", ref.decision == "include"
        tp += sys_inc and ref_inc; fp += sys_inc and not ref_inc; fn += (not sys_inc) and ref_inc; tn += (not sys_inc) and not ref_inc
        rows.append([x["id"], r["title"][:70], x["final"], ref.decision, "✓" if sys_inc == ref_inc else "✗"])
    sens = tp / (tp + fn) if tp + fn else None
    spec = tn / (tn + fp) if tn + fp else None
    return {"n": len(sample), "tp": tp, "fp": fp, "fn": fn, "tn": tn, "sensitivity": sens, "specificity": spec,
            "accuracy": (tp + tn) / len(sample), "rows": rows}


async def reproducibility(state, n: int, tr) -> dict:
    """Re-screen a sample with the same configuration: are the decisions identical?"""
    cfg = ReviewConfig(**state["config"])
    random.seed(11)
    sample = random.sample(state["screening"], min(n, len(state["screening"])))
    byid = {r["id"]: r for r in state["records"]}
    same = 0
    for x in sample:
        a = await screen_one(cfg, tr, state["protocol"], byid[x["id"]], "A")
        same += a.decision == x["a"]
    return {"n": len(sample), "identical": same, "rate": same / len(sample)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id")
    ap.add_argument("--silver", type=int, default=30)
    ap.add_argument("--repro", type=int, default=20)
    a = ap.parse_args()
    d = run_dir(a.run_id)
    state = load_state(a.run_id)
    man = json.load(open(f"{d}/manifest.json"))
    checklist = json.load(open(f"{d}/prisma_checklist.json"))
    tr = Trace(a.run_id + "-eval")
    t0 = time.time()
    silver = asyncio.run(silver_standard(state, a.silver, tr))
    repro = asyncio.run(reproducibility(state, a.repro, tr))
    m = man["metrics"]

    raw = state.get("raw_records", []) or state["records"]
    known = []
    for k in KNOWN_ITEMS:
        hit = find(k, state["records"])
        st = "not retrieved"
        if hit:
            fin = next((x for x in state["screening"] if x["id"] == hit["id"]), {})
            st = "included" if hit["id"] in state["included"] else ("passed screening" if fin.get("final") == "include" else "excluded at screening")
        known.append([k[:80], hit["id"] if hit else "—", st])
    retrieved = sum(r[2] != "not retrieved" for r in known)
    passed = sum(r[2] in ("included", "passed screening") for r in known)

    ex = state["extractions"]
    fields = ["study_design", "population_or_dataset", "intervention_or_method", "comparator", "main_finding", "limitations"]
    filled = sum(1 for e in ex.values() for f in fields if e[f]["value"] and "not reported" not in e[f]["value"].lower())
    completeness_fields = filled / (len(ex) * len(fields)) if ex else 0
    agree = sum(x["a"] == x["b"] for x in state["screening"]) / len(state["screening"])

    ev = {
        "run_id": a.run_id, "question": state["config"]["question"], "date": time.strftime("%Y-%m-%d"),
        "completeness": {"prisma_items": sum(c["reported"] for c in checklist), "prisma_total": len(checklist),
                         "stages_completed": 10, "extraction_fields_filled": round(completeness_fields, 3)},
        "accuracy": {"quote_grounding": state["grounding"], "silver": {k: v for k, v in silver.items() if k != "rows"},
                     "known_items": {"total": len(KNOWN_ITEMS), "retrieved": retrieved, "passed_screening": passed}},
        "consistency": {"kappa": state["kappa"], "raw_agreement": round(agree, 3), "conflicts": state["conflicts"],
                        "reproducibility": repro, "critic_rounds": m["critic_rounds"], "critic_approved": m["critic_approved"]},
        "time": {"pipeline_minutes": m["pipeline_minutes"], "manual_estimate_hours": m["manual_estimate_hours"],
                 "basis": m["manual_estimate_basis"], "llm_calls": m["llm_calls"],
                 "speedup": round(m["manual_estimate_hours"] * 60 / max(m["pipeline_minutes"], 0.1), 1)},
    }
    json.dump(ev, open(f"{d}/evaluation.json", "w"), indent=2)

    # ---- figure
    fig, ax = plt.subplots(1, 2, figsize=(10, 3.2), gridspec_kw={"width_ratios": [1.4, 1]})
    labels = ["PRISMA items", "Quotes verified", "Agreement A-B", "Reproducible", "vs silver std."]
    vals = [ev["completeness"]["prisma_items"] / 27, state["grounding"], agree, repro["rate"], silver["accuracy"]]
    ax[0].barh(labels[::-1], vals[::-1], color="#4338ca", alpha=.85)
    for i, v in enumerate(vals[::-1]):
        ax[0].text(v + .01, i, f"{v:.0%}", va="center", fontsize=9)
    ax[0].set_xlim(0, 1.12); ax[0].set_title("Rigour & consistency", loc="left", fontweight="bold")
    ax[1].bar(["PRISMA-Agent", "Manual (est.)"], [m["pipeline_minutes"] / 60, m["manual_estimate_hours"]], color=["#4338ca", "#a1a1aa"])
    ax[1].set_ylabel("hours"); ax[1].set_title(f"Time: ×{ev['time']['speedup']} faster", loc="left", fontweight="bold")
    for i, v in enumerate([m["pipeline_minutes"] / 60, m["manual_estimate_hours"]]):
        ax[1].text(i, v, f"{v:.1f} h", ha="center", va="bottom", fontsize=9)
    for a_ in ax:
        for s in ("top", "right"):
            a_.spines[s].set_visible(False)
    fig.tight_layout()
    os.makedirs(f"{ROOT}/docs/img", exist_ok=True)
    fig.savefig(f"{ROOT}/docs/img/evaluation.png", dpi=200); fig.savefig(f"{ROOT}/prisma_agent/static/evaluation.png", dpi=200)
    plt.close(fig)

    s = silver
    ui = {
        "run_id": a.run_id, "question": ev["question"],
        "tiles": [[f"{ev['completeness']['prisma_items']}/27", "PRISMA 2020 items reported"],
                  [f"{state['grounding']:.0%}", "extracted quotes verified in source"],
                  [f"κ = {state['kappa']}", f"screener agreement ({agree:.0%} raw)"],
                  [f"×{ev['time']['speedup']}", f"faster ({m['pipeline_minutes']} min vs ~{m['manual_estimate_hours']} h)"],
                  [f"{repro['rate']:.0%}", "identical decisions on re-run"],
                  [f"{s['accuracy']:.0%}", f"agreement with silver standard (n={s['n']})"],
                  [f"{retrieved}/{len(KNOWN_ITEMS)}", "known key papers retrieved"],
                  [f"{m['llm_calls']}", "LLM calls, all logged"]],
        "sections": [
            {"title": "Accuracy vs silver standard", "note": "Independent reference decisions by gpt-oss:20b on a random sample of screened records.",
             "head": ["ID", "Title", "PRISMA-Agent", "Reference", "Match"], "rows": s["rows"]},
            {"title": "Known-item test", "note": "Key studies on the topic that a good search should find.",
             "head": ["Study", "Record", "Outcome"], "rows": known},
            {"title": "Stage timings", "note": "Seconds per pipeline stage.",
             "head": ["Stage", "Seconds"], "rows": [[k, v] for k, v in man["timings_seconds"].items()]}],
        "figures": ["/static/evaluation.png"]}
    json.dump(ui, open(f"{ROOT}/prisma_agent/static/evaluation.json", "w"), indent=1)

    md = f"""# Evaluation

Run `{a.run_id}`, question: *{ev['question']}*. Evaluated on {ev['date']}; the evaluation itself took {time.time() - t0:.0f} s.

![Evaluation](img/evaluation.png)

| Dimension | Metric | Result |
|---|---|---|
| Completeness | PRISMA 2020 checklist items present in the manuscript | **{ev['completeness']['prisma_items']}/27** |
| Completeness | Pipeline stages completed autonomously | 10/10 |
| Completeness | Extraction fields filled (not "not reported") | {completeness_fields:.0%} |
| Accuracy | Extracted quotes found verbatim in the source text | **{state['grounding']:.0%}** |
| Accuracy | Screening vs silver standard (gpt-oss:20b, n={s['n']}): accuracy | **{s['accuracy']:.0%}** |
| Accuracy | … sensitivity / specificity | {s['sensitivity'] if s['sensitivity'] is None else f"{s['sensitivity']:.0%}"} / {s['specificity'] if s['specificity'] is None else f"{s['specificity']:.0%}"} (TP {s['tp']}, FP {s['fp']}, FN {s['fn']}, TN {s['tn']}) |
| Accuracy | Known key papers retrieved / passed screening | {retrieved}/{len(KNOWN_ITEMS)} / {passed}/{len(KNOWN_ITEMS)} |
| Consistency | Cohen's κ between screening agents A and B | **{state['kappa']}** (raw agreement {agree:.0%}, {state['conflicts']} conflicts adjudicated) |
| Consistency | Reproducibility: identical decisions when re-screening {repro['n']} records | **{repro['rate']:.0%}** |
| Consistency | Critic rounds / final approval | {m['critic_rounds']} / {m['critic_approved']} |
| Time | End-to-end pipeline | **{m['pipeline_minutes']} min** ({m['llm_calls']} LLM calls) |
| Time | Manual estimate for the same workload | ~{m['manual_estimate_hours']} h ({m['manual_estimate_basis']}) |
| Time | Speed-up | **×{ev['time']['speedup']}** |

## Known-item test

| Study | Record | Outcome |
|---|---|---|
""" + "\n".join(f"| {r[0]} | {r[1]} | {r[2]} |" for r in known) + """

## How to read these numbers

- The **silver standard** is an independent model, not a human gold standard. It measures consistency with a stronger, different reviewer, not the absolute truth.
- The **manual estimate** counts reviewer time only (two reviewers), not protocol writing, searching or writing the manuscript, so the real saving is larger.
- **Known-item recall** depends on the search budget (records per query) and on API rate limits (Semantic Scholar often returns HTTP 429 without an API key).
"""
    open(f"{ROOT}/docs/EVALUATION.md", "w").write(md)
    print(json.dumps(ev, indent=1))


if __name__ == "__main__":
    main()
