"""The agents. Each node reads the shared ReviewState and returns a partial update; every decision is traced."""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import time
from collections import Counter

import pymupdf as fitz
import httpx
from langgraph.types import interrupt
from rapidfuzz import fuzz

from .config import CACHE_DIR, ReviewConfig
from .dedup import deduplicate
from .llm import make_llm, structured
from .rag import HybridIndex
from .schemas import (Adjudication, Critique, Extraction, FullTextDecision, Protocol, RiskOfBias, ScreeningDecision,
                      SearchStrategy, Synthesis)
from .sources.base import UA, Record
from .sources.clients import SEARCHERS
from .trace import Trace

INDEXES: dict[str, HybridIndex] = {}       # per-run RAG index (not serialisable, kept out of the checkpointed state)
FULLTEXT: dict[str, dict[str, str]] = {}   # per-run study id -> text used for quote verification


def cfg_of(state) -> ReviewConfig:
    return ReviewConfig(**state["config"])


def stage(state, name: str, t0: float) -> dict:
    timings = dict(state.get("timings", {}))
    timings[name] = round(time.time() - t0, 1)
    return timings


def criteria_text(p: dict) -> str:
    inc = "\n".join(f"- {c['id']}: {c['text']}" for c in p["inclusion"])
    exc = "\n".join(f"- {c['id']}: {c['text']}" for c in p["exclusion"])
    return f"INCLUSION criteria (all must be met):\n{inc}\nEXCLUSION criteria (any one excludes):\n{exc}"


# =============================================================================== 1. Planner / protocol
async def planner(state):
    t0, cfg, tr = time.time(), cfg_of(state), Trace.get(state["run_id"])
    tr.log("stage", "planner", status="start", title="Protocol definition")
    prompt = f"""You are a senior systematic-review methodologist. Write a PRISMA 2020 compliant review protocol
for the research question below. Choose the framework that fits (PICO / PCC / SPIDER).
Criteria must be specific, testable on a title+abstract, and numbered (I1.., E1..). Use 3-5 inclusion
and 3-5 exclusion criteria. Restrict to publications from {cfg.year_from} onward and to English.
Risk-of-bias domains must suit the study designs of this field (e.g. for ML/AI evaluation studies:
dataset representativeness, reference standard, leakage, reporting of metrics, reproducibility).

The title must be the title of the review itself (e.g. "X for Y: a systematic review"), not "Protocol for...".

Research question: {cfg.question}"""
    p = await structured(make_llm(cfg), Protocol, prompt, tr, "planner")
    p.title = re.sub(r"^(a\s+)?protocol (for|of) (a\s+)?(systematic review:?\s*)?", "", p.title, flags=re.I).strip()
    tr.log("decision", "planner", what="protocol", detail=p.model_dump())
    return {"protocol": p.model_dump(), "timings": stage(state, "protocol", t0), "search_round": 0}


async def protocol_review(state):
    """Human-in-the-loop checkpoint #1: approve or edit the protocol before any search is run."""
    cfg, tr = cfg_of(state), Trace.get(state["run_id"])
    if cfg.hitl == "human":
        tr.log("hitl", "human", status="waiting", what="protocol approval")
        answer = interrupt({"type": "protocol_approval", "protocol": state["protocol"]})
        protocol = answer.get("protocol") or state["protocol"]
        tr.log("hitl", "human", status="done", what="protocol approval", approved=True,
               edited=protocol != state["protocol"], note=answer.get("note", ""))
        return {"protocol": protocol, "protocol_approved_by": "human"}
    tr.log("hitl", "auto", status="done", what="protocol approval", approved=True, note="auto-approve mode (logged)")
    return {"protocol_approved_by": "auto"}


# =============================================================================== 2. Search strategy
def compile_queries(s: SearchStrategy) -> dict[str, list[str]]:
    blocks = s.concepts[:3]
    pubmed = " AND ".join("(" + " OR ".join([f'"{t}"[tiab]' for t in b.terms[:6]] +
                                            [f'"{m}"[MeSH Terms]' for m in b.mesh[:2]]) + ")" for b in blocks)
    arxiv = " AND ".join("(" + " OR ".join(f'all:"{t}"' if " " in t else f"all:{t}" for t in b.terms[:4]) + ")"
                         for b in blocks)
    generic = " AND ".join("(" + " OR ".join(f'"{t}"' for t in b.terms) + ")" for b in blocks)
    kw = s.keyword_queries[:3]
    return {"boolean": [generic], "pubmed": [pubmed], "arxiv": [arxiv], "openalex": kw, "semanticscholar": kw[:2]}


async def search_strategist(state):
    t0, cfg, tr = time.time(), cfg_of(state), Trace.get(state["run_id"])
    rnd = state.get("search_round", 0) + 1
    tr.log("stage", "search_strategist", status="start", title=f"Search strategy (round {rnd})")
    p = state["protocol"]
    feedback = state.get("coordinator_feedback", "")
    prompt = f"""You are an information specialist designing a systematic literature search.
Review question: {cfg.question}
Population: {p['population']} | Intervention/concept: {p['intervention_or_concept']} | Outcomes: {', '.join(p['outcomes'])}
Build 2-3 concept blocks (the core concepts, NOT the outcomes) with synonyms and MeSH headings where relevant,
plus 3 short keyword queries for relevance-ranked search engines.
{"IMPORTANT, feedback from the coordinator: " + feedback if feedback else ""}"""
    s = await structured(make_llm(cfg), SearchStrategy, prompt, tr, "search_strategist")
    queries = compile_queries(s)
    tr.log("decision", "search_strategist", what="search strategy", detail={**s.model_dump(), "compiled": queries})
    return {"strategy": {**s.model_dump(), "compiled": queries}, "search_round": rnd,
            "timings": stage(state, f"search_strategy_r{rnd}", t0)}


# =============================================================================== 3. Retrieval
async def retrieval(state):
    t0, cfg, tr = time.time(), cfg_of(state), Trace.get(state["run_id"])
    tr.log("stage", "retrieval", status="start", title="Multi-source retrieval & deduplication")
    compiled = state["strategy"]["compiled"]

    async def run(source, q):
        try:
            recs = await SEARCHERS[source](q, cfg.max_per_query, cfg.year_from)
            tr.log("tool", "retrieval", tool=f"search_{source}", query=q, hits=len(recs))
            return source, q, recs, None
        except Exception as e:
            tr.log("tool", "retrieval", tool=f"search_{source}", query=q, hits=0, error=str(e)[:160])
            return source, q, [], str(e)[:160]

    jobs = [run(src, q) for src in cfg.sources for q in compiled.get(src, [])]
    results = await asyncio.gather(*jobs)
    prev = [Record(**r) for r in state.get("raw_records", [])]
    raw = prev + [r for _, _, recs, _ in results for r in recs]
    log = state.get("search_log", []) + [{"round": state["search_round"], "source": s, "query": q, "hits": len(r),
                                          "error": e, "date": time.strftime("%Y-%m-%d")} for s, q, r, e in results]
    unique, merges = deduplicate([r.model_copy(deep=True) for r in raw])
    identified = Counter(r.source for r in raw)
    tr.log("decision", "retrieval", what="deduplication", identified=len(raw), unique=len(unique),
           duplicates=len(raw) - len(unique), examples=merges[:5])
    return {"raw_records": [r.model_dump() for r in raw], "records": [r.model_dump() for r in unique],
            "search_log": log, "identified": dict(identified), "duplicates": len(raw) - len(unique),
            "timings": stage(state, "retrieval", t0)}


# =============================================================================== Coordinator (routing with rationale)
def coordinator_after_retrieval(state):
    tr = Trace.get(state["run_id"])
    n = len(state["records"])
    if n < 20 and state["search_round"] < 2:
        tr.log("coordinator", "coordinator", decision="broaden search", reason=f"only {n} unique records (< 20)")
        return "broaden"
    tr.log("coordinator", "coordinator", decision="proceed to screening", reason=f"{n} unique records")
    return "screen"


def broaden(state):
    return {"coordinator_feedback": f"The previous search returned only {len(state['records'])} unique records. "
                                    "Use broader, more general synonyms and fewer restrictive terms."}


def coordinator_after_screening(state):
    tr = Trace.get(state["run_id"])
    k = len(state["ta_included"])
    if k < 3 and state["search_round"] < 2:
        tr.log("coordinator", "coordinator", decision="broaden search", reason=f"only {k} records passed screening")
        return "broaden"
    tr.log("coordinator", "coordinator", decision="proceed to full-text", reason=f"{k} records included at title/abstract")
    return "fulltext"


# =============================================================================== 4. Title/abstract screening (dual + adjudication)
def cohen_kappa(a: list[str], b: list[str]) -> float:
    n = len(a)
    if not n:
        return 0.0
    po = sum(x == y for x, y in zip(a, b)) / n
    pe = sum((a.count(c) / n) * (b.count(c) / n) for c in ("include", "exclude"))
    return round((po - pe) / (1 - pe), 3) if pe < 1 else 1.0


REVIEWER_STYLE = {
    # Same criteria and same rule for both reviewers (as in a real dual screening); independence comes from
    # separate calls, different sampling seeds/temperature and a different order of presentation.
    "A": "You are independent reviewer A.",
    "B": "You are independent reviewer B.",
}
RULE = ("Standard title/abstract screening rule: exclude only when the record clearly violates a criterion; "
        "when the abstract is silent or ambiguous on a criterion, mark it 'unclear' and do not exclude on it "
        "(it will be checked at full text).")


async def screen_one(cfg, tr, p, r: dict, who: str):
    llm = make_llm(cfg, seed_offset=1 if who == "A" else 2)
    if who == "B":
        llm = llm.model_copy(update={"temperature": 0.3})
    record = (f"Record {r['id']}\nTitle: {r['title']}\nYear: {r['year']} | Venue: {r['venue']}\n"
              f"Abstract: {r['abstract'][:2500] or '(no abstract available: decide on the title, use unclear where needed)'}")
    task = f"Systematic review question: {cfg.question}\n{criteria_text(p)}\n{RULE}"
    body = f"{task}\n\n{record}" if who == "A" else f"{record}\n\n{task}"
    prompt = f"""{REVIEWER_STYLE[who]}
{body}

Check every criterion, then decide include/exclude."""
    return await structured(llm, ScreeningDecision, prompt, tr, f"screener_{who}")


async def ta_screening(state):
    t0, cfg, tr = time.time(), cfg_of(state), Trace.get(state["run_id"])
    p, recs = state["protocol"], state["records"]
    done = {s["id"]: s for s in state.get("screening", []) if s["id"] in {r["id"] for r in recs}}
    todo = [r for r in recs if r["id"] not in done]                      # broadened search: screen only new ids
    tr.log("stage", "screener", status="start", title=f"Title/abstract screening: {len(todo)} records × 2 reviewers")
    sem = asyncio.Semaphore(4)

    async def both(r):
        async with sem:
            a = await screen_one(cfg, tr, p, r, "A")
            b = await screen_one(cfg, tr, p, r, "B")
        row = {"id": r["id"], "title": r["title"], "a": a.decision, "a_reason": a.reason, "a_conf": a.confidence,
               "b": b.decision, "b_reason": b.reason, "b_conf": b.confidence,
               "checks_a": [c.model_dump() for c in a.checks], "checks_b": [c.model_dump() for c in b.checks]}
        tr.log("screening", "screeners", id=r["id"], a=a.decision, b=b.decision, title=r["title"][:90])
        return row

    rows = list(done.values()) + list(await asyncio.gather(*(both(r) for r in todo)))
    conflicts = sum(x["a"] != x["b"] for x in rows)
    kappa = cohen_kappa([x["a"] for x in rows], [x["b"] for x in rows])
    tr.log("decision", "screeners", what="dual screening done", screened=len(rows), conflicts=conflicts, kappa=kappa)
    return {"screening": rows, "kappa": kappa, "conflicts": conflicts, "timings": stage(state, "ta_screening", t0)}


async def resolve_conflicts(state):
    """Human-in-the-loop checkpoint #2 (or independent adjudicator model) for reviewer disagreements."""
    t0, cfg, tr = time.time(), cfg_of(state), Trace.get(state["run_id"])
    p, recs = state["protocol"], state["records"]
    rows = [dict(x) for x in state["screening"]]
    conflicts = [x for x in rows if x["a"] != x["b"]]
    tr.log("stage", "adjudicator", status="start", title=f"Conflict resolution: {len(conflicts)} disagreements")
    if conflicts and cfg.hitl == "human":
        tr.log("hitl", "human", status="waiting", what="screening conflicts", n=len(conflicts))
        answer = interrupt({"type": "screening_conflicts", "conflicts": conflicts})
        for x in conflicts:
            x["final"], x["final_by"] = answer["decisions"][x["id"]], "human"
            x["final_reason"] = "Resolved by the human reviewer."
        tr.log("hitl", "human", status="done", what="screening conflicts")
    else:
        judge = make_llm(cfg, "judge")
        byid = {r["id"]: r for r in recs}
        for x in conflicts:
            r = byid[x["id"]]
            adj = await structured(judge, Adjudication, f"""You are the senior adjudicator of a systematic review.
Two reviewers disagree on this record. Read both rationales and the criteria, then make the final decision.
Question: {cfg.question}
{criteria_text(p)}
Title: {r['title']}
Abstract: {r['abstract'][:2500]}
Reviewer A ({x['a']}): {x['a_reason']}
Reviewer B ({x['b']}): {x['b_reason']}""", tr, "adjudicator")
            x["final"], x["final_by"], x["final_reason"] = adj.decision, "adjudicator", adj.reason
            tr.log("decision", "adjudicator", id=x["id"], final=adj.decision, reason=adj.reason)
    for x in rows:
        if x["a"] == x["b"]:
            x["final"], x["final_by"], x["final_reason"] = x["a"], "agreement", x["a_reason"]
    included = [x["id"] for x in rows if x["final"] == "include"]
    tr.log("decision", "adjudicator", what="title/abstract screening", screened=len(rows), included=len(included),
           conflicts=len(conflicts), kappa=state["kappa"])
    return {"screening": rows, "ta_included": included, "timings": stage(state, "conflict_resolution", t0)}


# =============================================================================== 5. Full-text retrieval + eligibility (RAG)
async def fetch_pdf_text(url: str) -> str:
    if not url:
        return ""
    if "arxiv.org/abs/" in url:
        url = url.replace("/abs/", "/pdf/")
    path = os.path.join(CACHE_DIR, "pdf", hashlib.sha256(url.encode()).hexdigest()[:20] + ".pdf")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        try:
            async with httpx.AsyncClient(timeout=40, follow_redirects=True, headers={"User-Agent": UA}) as c:
                r = await c.get(url)
            if r.status_code != 200 or not r.content.startswith(b"%PDF"):
                return ""
            open(path, "wb").write(r.content)
        except Exception:
            return ""
    try:
        doc = fitz.open(path)
        text = " ".join(doc[i].get_text() for i in range(min(14, len(doc))))
        cut = re.search(r"\n\s*(References|REFERENCES|Bibliography)\s*\n", text)
        return text[:cut.start()] if cut and cut.start() > 3000 else text
    except Exception:
        return ""


async def fulltext(state):
    t0, cfg, tr = time.time(), cfg_of(state), Trace.get(state["run_id"])
    p, run = state["protocol"], state["run_id"]
    byid = {r["id"]: r for r in state["records"]}
    rows = {x["id"]: x for x in state["screening"]}
    conf = lambda i: (rows[i]["a_conf"] + rows[i]["b_conf"]) / 2
    sought = sorted(state["ta_included"], key=lambda i: -conf(i))[: cfg.max_fulltext]
    tr.log("stage", "fulltext", status="start", title=f"Full-text retrieval & eligibility: {len(sought)} reports")
    texts = await asyncio.gather(*(fetch_pdf_text(byid[i]["pdf_url"] or (byid[i]["url"] if "arxiv" in byid[i]["url"] else ""))
                                   for i in sought))
    index = HybridIndex(cfg.embed_model)
    FULLTEXT[run] = {}
    availability = {}
    for i, t in zip(sought, texts):
        r = byid[i]
        index.add_study(i, f"{r['title']}. {r['abstract']}", "ab")
        if len(t) > 2000:
            index.add_study(i, t, "ft")
            availability[i] = "full text"
        else:
            availability[i] = "abstract only"
        FULLTEXT[run][i] = f"{r['title']}. {r['abstract']} {t}"
        tr.log("tool", "fulltext", tool="fetch_and_parse_pdf", id=i, result=availability[i], chars=len(t))
    INDEXES[run] = index.build()
    tr.log("tool", "rag", tool="build_hybrid_index", chunks=len(index.docs), studies=len(sought))

    q = "study design, data, participants, method evaluated and outcomes measured; " + p["intervention_or_concept"]
    decisions = []
    for i in sought:
        ctx = INDEXES[run].search(q, k=6, study=i)
        passages = "\n".join(f"[{c['chunk']}] {c['text']}" for c in ctx)
        d = await structured(make_llm(cfg), FullTextDecision, f"""You assess full-text eligibility for a systematic review.
Question: {cfg.question}
{criteria_text(p)}
Study {i}: {byid[i]['title']} ({availability[i]})
Retrieved passages:
{passages}
Decide include/exclude. If excluded give the criterion id. Cite the chunk ids you relied on.""", tr, "fulltext_screener")
        decisions.append({"id": i, "availability": availability[i], **d.model_dump()})
        tr.log("decision", "fulltext_screener", id=i, decision=d.decision, code=d.exclusion_code, reason=d.reason)
    included = [d["id"] for d in decisions if d["decision"] == "include"]
    return {"fulltext": decisions, "included": included, "ft_sought": sought,
            "timings": stage(state, "fulltext", t0)}


async def ensure_index(state):
    """Rebuild the per-run RAG index from cached PDFs (needed after a restart or a replay from a checkpoint)."""
    run = state["run_id"]
    if run in INDEXES:
        return
    cfg = cfg_of(state)
    byid = {r["id"]: r for r in state["records"]}
    sought = state["ft_sought"]
    texts = await asyncio.gather(*(fetch_pdf_text(byid[i]["pdf_url"] or (byid[i]["url"] if "arxiv" in byid[i]["url"] else ""))
                                   for i in sought))
    index, FULLTEXT[run] = HybridIndex(cfg.embed_model), {}
    for i, t in zip(sought, texts):
        r = byid[i]
        index.add_study(i, f"{r['title']}. {r['abstract']}", "ab")
        if len(t) > 2000:
            index.add_study(i, t, "ft")
        FULLTEXT[run][i] = f"{r['title']}. {r['abstract']} {t}"
    INDEXES[run] = index.build()
    Trace.get(run).log("tool", "rag", tool="rebuild_hybrid_index", chunks=len(index.docs), studies=len(sought))


CHUNK_ID = re.compile(r"^\[?S\d{3}#\w+\]?$")


def repair_extraction(exd: dict) -> dict:
    """Deterministic repairs of common small-model slips (value = chunk id, tag inside quote)."""
    for k, v in exd.items():
        if isinstance(v, dict) and "value" in v:
            v["quote"] = re.sub(r"^\s*\[S\d{3}#\w+\]\s*", "", v["quote"])
            if CHUNK_ID.match(v["value"].strip()):
                v["chunk"], v["value"] = v["value"].strip("[]"), (v["quote"][:160] + "…") if len(v["quote"]) > 160 else v["quote"]
    for r in exd.get("results", []):
        r["quote"] = re.sub(r"^\s*\[S\d{3}#\w+\]\s*", "", r["quote"])
        if CHUNK_ID.match(r["value"].strip()):
            key = r["metric"].split()[0].lower()
            m = re.search(re.escape(key) + r"[^0-9]{0,40}?(\d+(?:\.\d+)?\s?%?)", r["quote"].lower())
            r["chunk"], r["value"] = r["value"].strip("[]"), (m.group(1) if m else "see quote")
    return exd


# =============================================================================== 6. Extraction & appraisal (RAG)
def verify_quote(quote: str, source: str) -> bool:
    """True if the quote appears verbatim (or with >= 90 % similarity, e.g. hyphenation/PDF artefacts) in the source."""
    quote = re.sub(r"\[S\d{3}#\w+\]", " ", quote or "")
    parts = [x for x in re.split(r"…|\.\.\.", quote) if len(x.strip()) >= 12]
    if len(parts) > 1:
        return all(verify_quote(x, source) for x in parts)
    if len(quote.strip()) < 12 or "not reported" in quote.lower():
        return False
    q = re.sub(r"\W+", " ", quote.lower()).strip()
    s = re.sub(r"\W+", " ", source.lower())
    if q in s:
        return True
    qw, sw = q.split(), s.split()
    n = len(qw)
    anchors = set(qw)
    for start in range(0, max(1, len(sw) - n + 1), max(1, n // 4)):     # sliding window, O(len(source))
        window = sw[start:start + n]
        if len(anchors.intersection(window)) >= 0.6 * n and fuzz.ratio(q, " ".join(window)) >= 90:
            return True
    return False


async def extraction(state):
    t0, cfg, tr = time.time(), cfg_of(state), Trace.get(state["run_id"])
    p, run = state["protocol"], state["run_id"]
    byid = {r["id"]: r for r in state["records"]}
    tr.log("stage", "extractor", status="start", title=f"Data extraction & risk of bias: {len(state['included'])} studies")
    await ensure_index(state)
    queries = ["study design, dataset, sample, participants",
               "method or intervention evaluated and what it is compared with",
               "results: accuracy, sensitivity, recall, precision, specificity, F1, kappa, workload or time saved",
               "limitations, threats to validity, bias"]
    extractions, rob, checks = {}, {}, []
    for i in state["included"]:
        seen, ctx = set(), []
        for q in queries:
            for c in INDEXES[run].search(q, k=4, study=i, compress_ctx=False):
                if c["chunk"] not in seen:
                    seen.add(c["chunk"])
                    ctx.append(c)
        passages = "\n".join(f"[{c['chunk']}] {c['text']}" for c in ctx[:12])
        ex = await structured(make_llm(cfg), Extraction, f"""Extract data from study {i} for a systematic review.
Review question: {cfg.question}. Outcomes of interest: {', '.join(p['outcomes'])}.
Use ONLY the passages below. Each quote must be ONE contiguous span copied character-for-character from a single
passage (no ellipsis, no paraphrase, without the [chunk] tag); put the chunk id in the separate 'chunk' field.
Write 'not reported' when the information is absent.
Study: {byid[i]['title']} ({byid[i]['year']})
Passages:
{passages}""", tr, "extractor")
        exd = repair_extraction(ex.model_dump())
        src = FULLTEXT[run][i]
        has_quote = lambda t: bool(t) and "not reported" not in t.lower() and len(t.strip()) >= 12
        exd["results"] = [r for r in exd["results"] if "not reported" not in (r["value"] + r["metric"]).lower()]
        for field in ("study_design", "population_or_dataset", "intervention_or_method", "comparator", "main_finding", "limitations"):
            exd[field]["verified"] = verify_quote(exd[field]["quote"], src)
            checks.append(exd[field]["verified"] if has_quote(exd[field]["quote"]) else None)
        for res in exd["results"]:
            res["verified"] = verify_quote(res["quote"], src)
            checks.append(res["verified"])
        extractions[i] = exd
        rb = await structured(make_llm(cfg), RiskOfBias, f"""Appraise the risk of bias of study {i}.
Use exactly these domains: {', '.join(p['rob_domains'])}.
Judge each domain low / some concerns / high with a one-sentence justification based on the passages.
Overall = the worst domain judgement unless clearly justified otherwise.
Study: {byid[i]['title']}
Passages:
{passages}""", tr, "appraiser")
        rob[i] = rb.model_dump()
        order = ["low", "some concerns", "high"]                     # overall = worst domain (deterministic rule)
        rob[i]["overall"] = max((d["judgement"] for d in rob[i]["domains"]), key=order.index, default=rb.overall)
        tr.log("decision", "extractor", id=i, design=exd["study_design"]["value"][:80], results=len(exd["results"]),
               rob=rb.overall)
    graded = [c for c in checks if c is not None]
    grounding = round(sum(graded) / len(graded), 3) if graded else 0.0
    tr.log("decision", "extractor", what="quote verification", verified=sum(graded), total=len(graded), rate=grounding)
    return {"extractions": extractions, "rob": rob, "grounding": grounding,
            "timings": stage(state, "extraction_appraisal", t0)}


# =============================================================================== 7. Synthesis & writing
def evidence_table(state) -> str:
    byid = {r["id"]: r for r in state["records"]}
    lines = []
    for i, ex in state["extractions"].items():
        res = "; ".join(f"{x['metric']}={x['value']} ({x['outcome']})" for x in ex["results"]) or "no numeric result"
        lines.append(f"[{i}] {byid[i]['title']} ({byid[i]['year']}) | design: {ex['study_design']['value']} | "
                     f"data: {ex['population_or_dataset']['value']} | method: {ex['intervention_or_method']['value']} | "
                     f"results: {res} | finding: {ex['main_finding']['value']} | RoB: {state['rob'][i]['overall']}")
    return "\n".join(lines)


async def synthesis(state):
    t0, cfg, tr = time.time(), cfg_of(state), Trace.get(state["run_id"])
    rnd = len(state.get("critiques", []))
    tr.log("stage", "writer", status="start", title="Synthesis & writing" + (f" (revision {rnd})" if rnd else ""))
    fb = ""
    if state.get("critiques"):
        last = state["critiques"][-1]
        fb = "Revise your previous synthesis. Fix these reviewer issues:\n" + "\n".join(
            f"- [{i['severity']}] {i['section']}: {i['problem']} -> {i['fix']}" for i in last["issues"])
    s = await structured(make_llm(cfg), Synthesis, f"""You are writing the synthesis of a systematic review.
Question: {cfg.question}
Synthesis plan: {state['protocol']['synthesis_plan']}
Evidence table of the {len(state['extractions'])} included studies (cite ONLY these ids, e.g. [S004]):
{evidence_table(state)}

overall_answer must DIRECTLY answer "{cfg.question}" with the key figures (ranges of sensitivity / specificity /
workload etc. and the study ids); meta-analysis feasibility belongs ONLY in meta_analysis_note.
Group the evidence into 3-5 themes. Rate the certainty of each theme (GRADE-style: consider risk of bias,
inconsistency, imprecision). Say whether a meta-analysis is feasible (same outcome AND comparable metric in >= 3 studies).
Never invent numbers: use only those in the table.
{fb}""", tr, "writer")
    return {"synthesis": s.model_dump(), "timings": stage(state, f"synthesis_r{rnd}", t0)}


# =============================================================================== 8. Critic / QA
async def critic(state):
    t0, cfg, tr = time.time(), cfg_of(state), Trace.get(state["run_id"])
    tr.log("stage", "critic", status="start", title="Quality assurance (critic)")
    s, included = state["synthesis"], set(state["included"])
    text = " ".join([s["overall_answer"]] + [t["summary"] for t in s["themes"]])
    cited = set(re.findall(r"S\d{3}", text)) | {x for t in s["themes"] for x in t["studies"]}
    # deterministic checks first (cheap, exact)
    issues = []
    bad = sorted(cited - included)
    if bad:
        issues.append({"severity": "major", "section": "citations", "problem": f"cites studies not included: {bad}",
                       "fix": "cite only included study ids"})
    uncited = sorted(included - cited)
    if uncited and len(uncited) > len(included) / 2:
        issues.append({"severity": "minor", "section": "coverage", "problem": f"{len(uncited)} included studies never cited",
                       "fix": "make sure every included study contributes to a theme"})
    review = await structured(make_llm(cfg, "judge"), Critique, f"""You are the critical peer reviewer (QA agent) of a
systematic review. Check the synthesis against the evidence table: unsupported or exaggerated claims, numbers not
in the table, certainty ratings inconsistent with risk of bias, missing limitations, deviation from the protocol.
Report only real problems. approve=true if no major issue.
Question: {cfg.question}
Evidence table:
{evidence_table(state)}
Synthesis:
{s}""", tr, "critic")
    allissues = issues + [i.model_dump() for i in review.issues]
    approve = review.approve and not any(i["severity"] == "major" for i in issues)
    crit = {"round": len(state.get("critiques", [])) + 1, "issues": allissues, "approve": approve}
    tr.log("decision", "critic", approve=approve, issues=len(allissues),
           majors=sum(i["severity"] == "major" for i in allissues))
    return {"critiques": state.get("critiques", []) + [crit], "timings": stage(state, f"critic_r{crit['round']}", t0)}


def after_critic(state):
    tr = Trace.get(state["run_id"])
    last = state["critiques"][-1]
    if not last["approve"] and last["round"] < cfg_of(state).max_critic_rounds:
        tr.log("coordinator", "coordinator", decision="revise synthesis", reason="critic found major issues")
        return "revise"
    tr.log("coordinator", "coordinator", decision="write report",
           reason="critic approved" if last["approve"] else "max revision rounds reached (issues reported)")
    return "report"
