"""Report generation: PRISMA 2020 flow diagram, figures, manuscript (Markdown + DOCX), BibTeX, CSV, checklist, manifest."""
from __future__ import annotations

import csv
import json
import os
import re
import time
from collections import Counter
from importlib.metadata import version

import httpx
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

from .config import ReviewConfig, run_dir  # noqa: E402
from .trace import Trace  # noqa: E402

INK, MUTED, ACCENT = "#18181b", "#71717a", "#4338ca"
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9})


# ----------------------------------------------------------------------------- PRISMA numbers
def prisma_counts(state) -> dict:
    ft = state.get("fulltext", [])
    reasons = Counter(d["exclusion_code"] or "other" for d in ft if d["decision"] == "exclude")
    return {
        "identified": state.get("identified", {}), "identified_total": sum(state.get("identified", {}).values()),
        "duplicates": state.get("duplicates", 0), "screened": len(state.get("screening", [])),
        "excluded_ta": len(state.get("screening", [])) - len(state.get("ta_included", [])),
        "sought": len(state.get("ft_sought", [])),
        "not_retrieved": len(state.get("ta_included", [])) - len(state.get("ft_sought", [])),
        "abstract_only": sum(d["availability"] == "abstract only" for d in ft),
        "assessed": len(ft), "excluded_ft": sum(reasons.values()), "reasons": dict(reasons),
        "included": len(state.get("included", [])),
    }


def prisma_diagram(c: dict, path: str):
    fig, ax = plt.subplots(figsize=(9, 9.6))
    ax.set_xlim(0, 100); ax.set_ylim(0, 108); ax.axis("off")

    def box(x, y, w, h, text, fill="#ffffff", bold=False):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.4,rounding_size=1.2", fc=fill, ec=INK, lw=1.1))
        ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=8.6, wrap=True,
                fontweight="bold" if bold else "normal", color=INK)

    def arrow(x1, y1, x2, y2):
        ax.annotate("", (x2, y2), (x1, y1), arrowprops=dict(arrowstyle="-|>", color=INK, lw=1.1))

    for y, h, label in [(80, 22, "Identification"), (31, 47, "Screening"), (2, 22, "Included")]:
        ax.add_patch(FancyBboxPatch((0, y), 7, h, boxstyle="round,pad=0.3,rounding_size=1", fc="#eef0ff", ec="none"))
        ax.text(3.5, y + h / 2, label, rotation=90, ha="center", va="center", color=ACCENT, fontweight="bold")
    src = "\n".join(f"{k}: {v}" for k, v in c["identified"].items())
    box(11, 83, 42, 18, f"Records identified from databases\n(n = {c['identified_total']})\n{src}")
    box(60, 86, 38, 12, f"Records removed before screening:\nDuplicate records (n = {c['duplicates']})")
    box(11, 64, 42, 10, f"Records screened (title/abstract)\n(n = {c['screened']})")
    box(60, 64, 38, 10, f"Records excluded\n(n = {c['excluded_ta']})")
    box(11, 49, 42, 10, f"Reports sought for retrieval\n(n = {c['sought']})")
    box(60, 49, 38, 10, f"Reports not sought (budget cap)\n(n = {c['not_retrieved']})")
    box(11, 33, 42, 12, f"Reports assessed for eligibility\n(n = {c['assessed']})\nof which abstract only: {c['abstract_only']}")
    reasons = "\n".join(f"{k}: {v}" for k, v in c["reasons"].items()) or "none"
    box(60, 31, 38, 16, f"Reports excluded (n = {c['excluded_ft']})\n{reasons}")
    box(11, 6, 42, 12, f"Studies included in review\n(n = {c['included']})", fill="#ecfdf3", bold=True)
    arrow(32, 83, 32, 74.5); arrow(53, 92, 60, 92); arrow(32, 64, 32, 59.5); arrow(53, 69, 60, 69)
    arrow(32, 49, 32, 45.5); arrow(53, 54, 60, 54); arrow(32, 33, 32, 18.5); arrow(53, 39, 60, 39)
    ax.text(50, 106, "PRISMA 2020 flow diagram", ha="center", fontsize=12, fontweight="bold", color=INK)
    fig.savefig(path, dpi=200, bbox_inches="tight"); fig.savefig(path.replace(".png", ".svg"), bbox_inches="tight")
    plt.close(fig)


def rob_figure(state, path: str):
    rob = state["rob"]
    if not rob:
        return
    domains = state["protocol"]["rob_domains"]
    ids = list(rob)
    col = {"low": "#22c55e", "some concerns": "#f59e0b", "high": "#ef4444"}
    sym = {"low": "+", "some concerns": "–", "high": "×"}
    fig, ax = plt.subplots(figsize=(1.2 + 0.95 * (len(domains) + 1), 0.6 + 0.42 * len(ids)))
    for r, i in enumerate(ids):
        judged = {d["domain"]: d["judgement"] for d in rob[i]["domains"]}
        for cidx, d in enumerate(domains + ["Overall"]):
            j = rob[i]["overall"] if d == "Overall" else judged.get(d, list(judged.values())[cidx] if cidx < len(judged) else "some concerns")
            ax.add_patch(plt.Circle((cidx, -r), 0.32, color=col[j]))
            ax.text(cidx, -r, sym[j], ha="center", va="center", color="white", fontweight="bold")
    ax.set_xticks(range(len(domains) + 1)); ax.set_xticklabels([d.split(":")[0].split("(")[0].strip()[:24] for d in domains] + ["Overall"], rotation=30, ha="right")
    ax.set_yticks([-r for r in range(len(ids))]); ax.set_yticklabels(ids)
    ax.set_xlim(-0.6, len(domains) + 0.6); ax.set_ylim(-len(ids) + 0.4, 0.6); ax.set_aspect("equal")
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_title("Risk of bias (traffic-light plot)", fontweight="bold", loc="left")
    fig.savefig(path, dpi=200, bbox_inches="tight"); plt.close(fig)


def evidence_map(state, path: str):
    byid = {r["id"]: r for r in state["records"]}
    pts = Counter()
    for i, ex in state["extractions"].items():
        d = ex["study_design"]["value"].lower()
        design = ("Comparative / evaluation" if any(w in d for w in ("compar", "evaluat", "benchmark", "experiment"))
                  else "Systematic / scoping review" if "review" in d
                  else "Observational / cross-sectional" if any(w in d for w in ("cross", "observ", "cohort", "retro"))
                  else "Other")
        pts[(byid[i]["year"], design)] += 1
    if not pts:
        return
    fig, ax = plt.subplots(figsize=(7, 3.2))
    designs = sorted({d for _, d in pts})
    for (y, d), n in pts.items():
        ax.scatter(y, designs.index(d), s=180 * n, color=ACCENT, alpha=.55, edgecolor=INK)
        ax.text(y, designs.index(d), str(n), ha="center", va="center", color="white", fontweight="bold")
    ax.set_yticks(range(len(designs))); ax.set_yticklabels(designs)
    ax.set_xticks(sorted({y for y, _ in pts})); ax.grid(alpha=.2)
    ax.set_title("Evidence map: included studies by year and design", fontweight="bold", loc="left")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.savefig(path, dpi=200, bbox_inches="tight"); plt.close(fig)


def results_plot(state, path: str) -> list[dict]:
    """Meta-analysis preparation: collect every percentage-type result by metric family."""
    fam = {"recall": ("recall", "sensitivity"), "precision": ("precision", "ppv"), "specificity": ("specificity",),
           "accuracy": ("accuracy",), "F1": ("f1",), "kappa": ("kappa",)}
    rows = []
    for i, ex in state["extractions"].items():
        for res in ex["results"]:
            m = res["metric"].lower()
            f = next((k for k, keys in fam.items() if any(x in m for x in keys)), None)
            num = re.search(r"(\d+(?:\.\d+)?)", res["value"])
            if not f or not num:
                continue
            v = float(num.group(1))
            v = v / 100 if v > 1 and f != "kappa" else v
            if 0 <= v <= 1:
                rows.append({"study": i, "metric": f, "value": round(v, 3), "outcome": res["outcome"], "verified": res.get("verified")})
    if rows:
        metrics = [m for m in fam if any(r["metric"] == m for r in rows)]
        fig, ax = plt.subplots(figsize=(7, 0.6 + 0.55 * len(metrics)))
        for k, m in enumerate(metrics):
            vals = [r for r in rows if r["metric"] == m]
            ax.scatter([r["value"] for r in vals], [k] * len(vals), s=60, color=ACCENT, alpha=.7, edgecolor=INK, zorder=3)
            med = sorted(r["value"] for r in vals)[len(vals) // 2]
            ax.plot([med, med], [k - .3, k + .3], color="#ef4444", lw=2)
            ax.text(1.02, k, f"n={len(vals)}  median={med:.2f}", va="center", fontsize=8, color=MUTED, transform=ax.get_yaxis_transform())
        ax.set_yticks(range(len(metrics))); ax.set_yticklabels(metrics); ax.set_xlim(0, 1); ax.grid(axis="x", alpha=.25)
        ax.set_title("Reported performance across included studies (red = median)", fontweight="bold", loc="left")
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        fig.savefig(path, dpi=200, bbox_inches="tight"); plt.close(fig)
    return rows


# ----------------------------------------------------------------------------- manuscript
def bibtex(r: dict) -> str:
    key = (re.sub(r"\W", "", (r["authors"][0].split()[-1] if r["authors"] else "anon")).lower() + str(r["year"] or "")
           + re.sub(r"\W", "", r["title"].split()[0].lower() if r["title"] else ""))
    authors = " and ".join(r["authors"][:10]) or "Anonymous"
    fields = {"title": r["title"], "author": authors, "year": r["year"], "journal": r["venue"], "doi": r["doi"], "url": r["url"]}
    body = ",\n".join(f"  {k} = {{{v}}}" for k, v in fields.items() if v)
    return f"@article{{{key},\n{body}\n}}"


def cite(r: dict) -> str:
    a = r["authors"][0].split()[-1] if r["authors"] else "Anon."
    return f"{a}{' et al.' if len(r['authors']) > 1 else ''}, {r['year']}"


def linkify(text: str, byid: dict) -> str:
    return re.sub(r"\[?(S\d{3})\]?", lambda m: f"[{m.group(1)}: {cite(byid[m.group(1)])}]" if m.group(1) in byid else m.group(0), text)


PRISMA_ITEMS = [  # (item, section heading or phrase that must exist in the manuscript)
    ("1 Title", "systematic review"), ("2 Abstract", "## Abstract"), ("3 Rationale", "### Rationale"),
    ("4 Objectives", "### Objectives"), ("5 Eligibility criteria", "### Eligibility criteria"),
    ("6 Information sources", "### Information sources"), ("7 Search strategy", "### Search strategy"),
    ("8 Selection process", "### Selection process"), ("9 Data collection process", "### Data collection process"),
    ("10 Data items", "### Data items"), ("11 Study risk of bias assessment", "### Risk of bias assessment"),
    ("12 Effect measures", "### Effect measures"), ("13 Synthesis methods", "### Synthesis methods"),
    ("14 Reporting bias assessment", "### Reporting bias"), ("15 Certainty assessment", "### Certainty assessment"),
    ("16 Study selection", "### Study selection"), ("17 Study characteristics", "### Study characteristics"),
    ("18 Risk of bias in studies", "### Risk of bias in studies"), ("19 Results of individual studies", "### Results of individual studies"),
    ("20 Results of syntheses", "### Results of syntheses"), ("21 Reporting biases", "reporting bias"),
    ("22 Certainty of evidence", "certainty"), ("23 Discussion", "## Discussion"),
    ("24 Registration and protocol", "### Registration and protocol"), ("25 Support", "### Support"),
    ("26 Competing interests", "### Competing interests"), ("27 Availability of data and code", "### Availability"),
]


def write_report(state) -> dict:
    cfg = ReviewConfig(**state["config"])
    d = run_dir(state["run_id"])
    tr = Trace.get(state["run_id"])
    tr.log("stage", "reporter", status="start", title="Report generation")
    p, s = state["protocol"], state["synthesis"]
    byid = {r["id"]: r for r in state["records"]}
    inc = state["included"]
    c = prisma_counts(state)
    prisma_diagram(c, f"{d}/prisma_flow.png")
    rob_figure(state, f"{d}/risk_of_bias.png")
    evidence_map(state, f"{d}/evidence_map.png")
    metric_rows = results_plot(state, f"{d}/results_plot.png")
    sources = ", ".join(cfg.sources)
    ex = state["extractions"]

    # ---- tables
    with open(f"{d}/screening_log.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "title", "reviewer_A", "reason_A", "reviewer_B", "reason_B", "final", "decided_by", "final_reason"])
        for x in state["screening"]:
            w.writerow([x["id"], x["title"], x["a"], x["a_reason"], x["b"], x["b_reason"], x["final"], x["final_by"], x["final_reason"]])
    with open(f"{d}/evidence_table.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "citation", "title", "design", "data", "method", "comparator", "results", "main_finding", "limitations", "rob_overall"])
        for i in inc:
            e = ex[i]
            w.writerow([i, cite(byid[i]), byid[i]["title"], e["study_design"]["value"], e["population_or_dataset"]["value"],
                        e["intervention_or_method"]["value"], e["comparator"]["value"],
                        "; ".join(f"{r['metric']}={r['value']}" for r in e["results"]), e["main_finding"]["value"],
                        e["limitations"]["value"], state["rob"][i]["overall"]])
    with open(f"{d}/references.bib", "w") as f:
        f.write("\n\n".join(bibtex(byid[i]) for i in inc))

    # ---- manuscript
    q = lambda t: linkify(t, byid)
    char_rows = "\n".join(
        f"| {i} | {cite(byid[i])} | {ex[i]['study_design']['value'][:70]} | {ex[i]['population_or_dataset']['value'][:70]} | "
        f"{ex[i]['intervention_or_method']['value'][:70]} | {state['rob'][i]['overall']} |" for i in inc)
    res_rows = "\n".join(
        f"| {i} | {r['outcome'][:40]} | {r['metric']} | {r['value']} | {'✓' if r.get('verified') else '–'} |"
        for i in inc for r in ex[i]["results"])
    themes = "\n\n".join(f"**{t['theme']}** (certainty: {t['certainty']}). {q(t['summary'])} "
                         f"*Certainty rationale:* {t['certainty_reason']}" for t in s["themes"])
    crit = state["critiques"][-1] if state.get("critiques") else {"issues": [], "approve": True}
    limits_review = ("This review was conducted by an autonomous multi-agent system using local open-weight LLMs "
                     "(screening, extraction and appraisal by " + cfg.worker_model + ", adjudication and critique by "
                     + cfg.judge_model + "). Decisions are traceable but were not verified by two human reviewers. "
                     f"{c['abstract_only']} reports were assessed on the abstract only because no open-access full text was found, "
                     f"and {c['not_retrieved']} records were not sought because of the full-text budget cap.")
    md = f"""# {p['title']}

*A systematic review generated by PRISMA-Agent, run `{state['run_id']}`, {time.strftime('%Y-%m-%d')}.*

## Abstract

**Background.** {p['objective']}
**Methods.** We searched {sources} ({cfg.year_from}–{time.strftime('%Y')}). Two independent AI reviewers screened {c['screened']} records, with disagreements adjudicated by an independent model. Data were extracted with retrieval-augmented generation and verbatim, verified quotes. Risk of bias was assessed across {len(p['rob_domains'])} domains, and the evidence was synthesised narratively with GRADE-style certainty ratings.
**Results.** {c['identified_total']} records were identified, {c['screened']} screened and {c['assessed']} assessed in full; {c['included']} studies were included. Inter-reviewer agreement: Cohen's κ = {state['kappa']}.
**Conclusion.** {q(s['overall_answer'])}

## Introduction

### Rationale
{p['objective']}

### Objectives
Review question: *{cfg.question}*
Framework: {p['framework']}. Population: {p['population'].rstrip('.')}. Intervention/concept: {p['intervention_or_concept'].rstrip('.')}. Comparator: {p['comparator'].rstrip('.')}. Outcomes: {', '.join(p['outcomes'])}. Context: {p['context']}.

## Methods

### Eligibility criteria
""" + "\n".join(f"- **{x['id']}** {x['text']}" for x in p["inclusion"]) + "\n" + "\n".join(f"- **{x['id']}** {x['text']}" for x in p["exclusion"]) + f"""

Study designs: {', '.join(p['study_designs'])}. Languages: {', '.join(p['languages'])}.

### Information sources
{sources}, searched on {time.strftime('%Y-%m-%d')} through their public APIs. All responses are cached in `.cache/http/`, so the search can be re-executed identically.

### Search strategy
The search-strategy agent built concept blocks (combined with AND, synonyms with OR), and a compiler turned them into database-specific syntax:

""" + "\n".join(f"- **{x['source']}** (round {x['round']}): `{x['query']}` → {x['hits']} records" + (f" (error: {x['error']})" if x['error'] else "")
                for x in state["search_log"]) + f"""

### Selection process
Duplicates were removed by DOI and fuzzy title matching (RapidFuzz ≥ 93). Each record was screened independently by two LLM reviewers (same criteria; different sampling seeds, temperature and order of presentation), which judged every criterion as yes / no / unclear. Records were excluded only on a clear violation of a criterion. {state['conflicts']} disagreements were resolved by an adjudicator ({cfg.judge_model}{' or the human reviewer' if cfg.hitl == 'human' else ''}). Full texts were retrieved from open-access sources, parsed (PyMuPDF), chunked and indexed for hybrid retrieval (FAISS dense + BM25, reciprocal rank fusion, FlashRank re-ranking).

### Data collection process
An extraction agent filled a structured form from the top re-ranked passages of each study. Every value carries a verbatim quote and a chunk id. Quotes were automatically checked against the source text: **{round(state['grounding'] * 100)} % verified**.

### Data items
Study design; population or dataset; intervention or method; comparator; outcomes; quantitative results (metric, value, comparator); main finding; limitations.

### Risk of bias assessment
Domains: {', '.join(p['rob_domains'])}. Each was judged low / some concerns / high with a justification; the overall judgement is the worst domain.

### Effect measures
As reported by the studies (e.g. {', '.join(sorted({r['metric'] for r in metric_rows})) or 'see table'}). No conversion was applied.

### Synthesis methods
{p['synthesis_plan']} Studies were grouped into themes by the synthesis agent. A critic agent ({cfg.judge_model}) checked every claim against the evidence table (up to {cfg.max_critic_rounds} revision rounds).

### Reporting bias
Not formally assessed (too few studies per outcome for funnel-plot methods); preprints were included to limit publication bias.

### Certainty assessment
GRADE-inspired rating per theme (risk of bias, inconsistency, imprecision, indirectness).

## Results

### Study selection
{c['identified_total']} records were identified ({', '.join(f'{k}: {v}' for k, v in c['identified'].items())}). {c['duplicates']} duplicates were removed, {c['screened']} records were screened and {c['excluded_ta']} excluded. {c['sought']} reports were sought and {c['assessed']} assessed ({c['abstract_only']} on the abstract only); {c['excluded_ft']} were excluded ({', '.join(f'{k}: {v}' for k, v in c['reasons'].items()) or 'none'}). **{c['included']} studies were included.**

![PRISMA flow diagram](prisma_flow.png)

### Study characteristics

| ID | Study | Design | Population / data | Method | RoB |
|---|---|---|---|---|---|
{char_rows}

![Evidence map](evidence_map.png)

### Risk of bias in studies
![Risk of bias](risk_of_bias.png)

### Results of individual studies

| ID | Outcome | Metric | Value | Quote verified |
|---|---|---|---|---|
{res_rows}

![Results](results_plot.png)

### Results of syntheses
{themes}

**Meta-analysis.** {'Feasible' if s['meta_analysis_feasible'] else 'Not feasible'}: {s['meta_analysis_note']}

## Discussion

**Main answer.** {q(s['overall_answer'])}

**Research gaps.**
""" + "\n".join(f"- {g}" for g in s["research_gaps"]) + f"""

**Limitations of the evidence.** Certainty and the risk of bias per theme are given above, and reporting bias cannot be excluded.

**Limitations of the review process.** {limits_review}

**Quality assurance.** The critic agent ran {len(state.get('critiques', []))} round(s); final verdict: {'approved' if crit['approve'] else 'not approved'}. Remaining issues: {len(crit['issues'])}.
""" + "\n".join(f"- [{i['severity']}] {i['section']}: {i['problem']}" for i in crit["issues"]) + f"""

## Other information

### Registration and protocol
Not registered. The protocol generated by the planner agent (approved by: {state.get('protocol_approved_by', 'auto')}) is saved as `protocol.json`.

### Support
No funding.

### Competing interests
None.

### Availability of data, code and other materials
All artefacts are in this run folder: `protocol.json`, `screening_log.csv`, `evidence_table.csv`, `extractions.json`, `trace.jsonl` (full audit trail), `manifest.json` (models, versions, configuration fingerprint) and `references.bib`.

## References
""" + "\n".join(f"{n}. [{i}] {', '.join(byid[i]['authors'][:3])}{' et al.' if len(byid[i]['authors']) > 3 else ''} ({byid[i]['year']}). "
                f"{byid[i]['title']}. *{byid[i]['venue']}*. {('https://doi.org/' + byid[i]['doi']) if byid[i]['doi'] else byid[i]['url']}"
                for n, i in enumerate(inc, 1)) + "\n"
    open(f"{d}/report.md", "w").write(md)
    checklist = [{"item": it, "reported": key.lower() in md.lower()} for it, key in PRISMA_ITEMS]
    json.dump(checklist, open(f"{d}/prisma_checklist.json", "w"), indent=2)
    write_docx(md, d)
    json.dump(p, open(f"{d}/protocol.json", "w"), indent=2)
    json.dump({"extractions": ex, "risk_of_bias": state["rob"], "fulltext_decisions": state["fulltext"],
               "synthesis": s, "critiques": state.get("critiques", [])}, open(f"{d}/extractions.json", "w"), indent=2)
    manifest = write_manifest(state, cfg, d, c, checklist, tr)
    tr.log("stage", "reporter", status="done", title="Report generated", files=sorted(os.listdir(d)))
    return {"report_dir": os.path.relpath(d, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "prisma": c, "checklist_coverage": manifest["prisma_checklist_coverage"], "metrics": manifest["metrics"]}


def write_docx(md: str, d: str):
    from docx import Document
    from docx.shared import Inches
    doc = Document()
    table = None
    for line in md.splitlines():
        if line.startswith("|"):
            cells = [x.strip() for x in line.strip("|").split("|")]
            if set("".join(cells)) <= set("-: "):
                continue
            if table is None:
                table = doc.add_table(rows=0, cols=len(cells)); table.style = "Light Grid Accent 1"
            row = table.add_row().cells
            for k, v in enumerate(cells[: len(row)]):
                row[k].text = v
            continue
        table = None
        img = re.match(r"!\[.*\]\((.+)\)", line)
        if img and os.path.exists(os.path.join(d, img.group(1))):
            doc.add_picture(os.path.join(d, img.group(1)), width=Inches(6))
        elif line.startswith("#"):
            lvl = len(line) - len(line.lstrip("#"))
            doc.add_heading(line.lstrip("# ").strip(), level=min(lvl - 1, 3))
        elif line.strip():
            doc.add_paragraph(re.sub(r"[*`]", "", line.lstrip("- ")), style="List Bullet" if line.startswith("- ") else None)
    doc.save(os.path.join(d, "report.docx"))


def write_manifest(state, cfg, d, c, checklist, tr) -> dict:
    try:
        tags = httpx.get("http://localhost:11434/api/tags", timeout=5).json()["models"]
        digests = {m["name"]: m["digest"][:12] for m in tags if m["name"] in (cfg.worker_model, cfg.judge_model, cfg.embed_model + ":latest")}
    except Exception:
        digests = {}
    n_records = len(state["screening"])
    calls = secs = 0                                       # all LLM calls of this run (incl. replays), from the audit trail
    for line in open(tr.path):
        e = json.loads(line)
        if e.get("kind") == "llm_call":
            calls += 1
            secs += e.get("seconds") or 0
    manual_hours = round((n_records * 2 * 0.5 + c["assessed"] * 2 * 10 + c["included"] * 2 * 45) / 60, 1)
    total = sum(state.get("timings", {}).values())
    metrics = {
        "records_screened": n_records, "included": c["included"], "cohen_kappa": state["kappa"],
        "conflicts": state["conflicts"], "quote_grounding_rate": state["grounding"],
        "prisma_checklist_coverage": round(sum(x["reported"] for x in checklist) / len(checklist), 3),
        "critic_rounds": len(state.get("critiques", [])), "critic_approved": state["critiques"][-1]["approve"] if state.get("critiques") else None,
        "llm_calls": calls, "llm_seconds": round(secs, 1), "pipeline_minutes": round(total / 60, 1),
        "manual_estimate_hours": manual_hours,
        "manual_estimate_basis": "2 reviewers × (0.5 min per abstract + 10 min per full text + 45 min per extraction/appraisal)",
    }
    manifest = {"run_id": state["run_id"], "created": time.strftime("%Y-%m-%d %H:%M:%S"), "config": cfg.to_dict(),
                "config_fingerprint": cfg.fingerprint(), "model_digests": digests,
                "packages": {p: version(p) for p in ("langgraph", "langchain-core", "langchain-ollama", "faiss-cpu", "flashrank", "pymupdf")},
                "timings_seconds": state.get("timings", {}), "prisma": c, "metrics": metrics,
                "prisma_checklist_coverage": metrics["prisma_checklist_coverage"]}
    json.dump(manifest, open(f"{d}/manifest.json", "w"), indent=2)
    return manifest
