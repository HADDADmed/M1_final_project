---
title: "PRISMA-Agent: an agentic AI pipeline for rigorous, transparent and reproducible systematic reviews"
subtitle: "Technical report and user guide"
author: "Mohammed HADDAD (HADDADmed) · github.com/HADDADmed/M1_final_project"
date: "November 2026"
geometry: margin=2cm
fontsize: 10pt
mainfont: DejaVu Sans
monofont: DejaVu Sans Mono
colorlinks: true
---

# 1. Summary

PRISMA-Agent is a multi-agent system that carries out an end-to-end systematic literature review on any topic, following PRISMA 2020. A **LangGraph** state machine coordinates eight specialised agents:

- protocol, search strategy and multi-database retrieval;
- dual title/abstract screening with independent adjudication;
- full-text assessment through a **hybrid RAG** index, data extraction with verified quotes, and risk of bias;
- synthesis with GRADE-style certainty, critique, and report generation.

Humans can intervene at two checkpoints (`interrupt()`). Every decision is logged, and every run is reproducible: fixed seeds, pinned model digests, cached searches and SQLite checkpoints.

**Demonstration.** On *"How accurate are LLMs at title and abstract screening for systematic reviews?"* the system:

- identified 123 records and screened 115 twice (κ = 0.767);
- included 14 studies, with 92% of extracted quotes verified in the source text;
- reported 27/27 PRISMA items;
- finished in 15.6 min, against an estimated ~27.9 h of manual reviewer time.

# 2. Objectives and scope

The objectives come from the project brief:

- a modular multi-agent architecture;
- RAG over scientific corpora (OpenAlex, PubMed, arXiv, Semantic Scholar);
- the full PRISMA workflow: protocol, Boolean/MeSH search, dual screening, extraction, risk of bias, synthesis and report;
- transparency (logging, citation tracking, rationales, traces);
- human-in-the-loop oversight;
- generality across topics;
- evaluation of completeness, accuracy, consistency and time savings.

# 3. Architecture

PRISMA-Agent turns a research question into a PRISMA 2020 systematic review with a **multi-agent LangGraph workflow**. This document explains the components, the workflow, the shared state and the design decisions.

![System](docs/img/hand_system.png)

## 1. Components

| Layer | Component | File |
|---|---|---|
| Interfaces | Web app (FastAPI + SSE + vanilla JS), CLI (Typer + Rich), MCP server (FastMCP) | `server.py`, `cli.py`, `mcp_server.py` |
| Orchestration | LangGraph `StateGraph`, coordinator routing, `interrupt()` checkpoints, `RetryPolicy`, SQLite checkpointer | `graph.py`, `agents.py` |
| Agents | Planner, search strategist, retrieval, screeners A/B, adjudicator, full-text screener, extractor/appraiser, writer, critic | `agents.py` |
| Models | Ollama: `qwen3:8b` (worker), `gpt-oss:20b` (judge), `nomic-embed-text` (embeddings) | `llm.py`, `config.py` |
| Knowledge | OpenAlex, PubMed E-utilities, arXiv, Semantic Scholar; cached HTTP | `sources/` |
| RAG | PyMuPDF parsing → chunks → FAISS + BM25 → RRF → FlashRank → compression | `rag.py` |
| Outputs | PRISMA flow diagram, RoB traffic light, evidence map, results plot, Markdown/DOCX manuscript, BibTeX, CSV, checklist, manifest | `report.py` |
| Audit | Append-only `trace.jsonl` + LLM callback tracer | `trace.py` |

## 2. Workflow

![Agent graph](docs/img/hand_agent_graph.png)

| # | Node | Agent | What it does | Output in state |
|---|---|---|---|---|
| 1 | `planner` | Planner | Writes the protocol: PICO/PCC/SPIDER, numbered criteria (I1…, E1…), RoB domains, synthesis plan | `protocol` |
| 2 | `protocol_review` | Human (human) #1 | `interrupt()`: the human approves or edits the criteria (in `auto` mode the approval is logged) | `protocol_approved_by` |
| 3 | `search_strategist` | Search strategist | Concept blocks + synonyms + MeSH; a **deterministic compiler** writes PubMed / arXiv / generic Boolean syntax | `strategy` |
| 4 | `retrieval` | Retrieval | Parallel search of 4 databases (rate-limited, cached), DOI + fuzzy-title deduplication | `records`, `search_log`, `identified` |
| ◆ | coordinator | Coordinator | `< 20` unique records → `broaden` → new search round (max 2) | routing + logged reason |
| 5 | `screening` | Screeners A and B | Independent decisions with a yes/no/unclear check per criterion and a rationale; Cohen's κ | `screening`, `kappa` |
| 6 | `conflict_resolution` | Adjudicator / Human (human) #2 | Disagreements resolved by `gpt-oss:20b` or by the human (`interrupt()`) | `ta_included` |
| ◆ | coordinator | Coordinator | `< 3` included → broaden the search | |
| 7 | `fulltext` | Retrieval + full-text screener | Downloads open-access PDFs, builds the hybrid index, judges eligibility on retrieved passages with exclusion codes | `fulltext`, `included` |
| 8 | `extraction` | Extractor + appraiser | Structured extraction with verbatim quotes + chunk ids (verified), then risk of bias per protocol domain | `extractions`, `rob`, `grounding` |
| 9 | `synthesis` | Writer | Themes, GRADE-style certainty, meta-analysis feasibility, gaps; only from the evidence table | `synthesis` |
| 10 | `critic` | Critic | Deterministic checks (citations exist, coverage) + independent LLM review → `revise` (max 2 rounds) or `report` | `critiques` |
| 11 | `report` | Reporter | All artefacts + PRISMA checklist + manifest | `report_dir`, `metrics` |

## 3. Shared state

`ReviewState` (a `TypedDict`) is the single source of truth: protocol, strategy, search log, records, screening rows (A, B, final, decided-by, rationales), included ids, full-text decisions, extractions, risk of bias, synthesis, critiques, timings and metrics. It is **checkpointed in SQLite after every node** (`AsyncSqliteSaver`), which gives:

- **pause / resume**: human-in-the-loop interrupts can wait for hours;
- **crash recovery**: a failed node is retried (`RetryPolicy`) or the run is resumed from the last checkpoint;
- **time travel**: every intermediate state can be inspected.

The RAG index and parsed full texts are not serialisable, so they live in a per-run cache outside the state.

## 4. Hybrid RAG

![RAG](docs/img/hand_rag.png)

1. Open-access PDFs (OpenAlex `oa_url`, arXiv) are downloaded and parsed with PyMuPDF (first 14 pages, reference list removed).
2. Text is split into ~1,100-character chunks with 200 characters of overlap, cut at sentence boundaries. Each chunk keeps `study` and `chunk` ids (`S012#ft3`).
3. **Dense**: `nomic-embed-text` vectors (with `search_document:` / `search_query:` task prefixes), cosine similarity in FAISS.
4. **Sparse**: Okapi BM25 (exact terms, numbers, acronyms).
5. **Reciprocal Rank Fusion**, score = Σ 1/(60 + rank).
6. **FlashRank** cross-encoder (`ms-marco-MiniLM-L-12-v2`) re-ranks the top 20.
7. **Compression**: only the sentences that share most terms with the query are kept (eligibility). Extraction receives the full passages, so that quotes can be copied verbatim.
8. **Verification**: each quote is searched in the source text (exact match after normalisation, else a sliding-window RapidFuzz ratio ≥ 90).

## 5. Design decisions

| Decision | Why |
|---|---|
| LangGraph instead of a linear chain | Loops (broaden, revise), conditional routing, interrupts and checkpointing are first-class |
| Two model families (worker vs judge) | A model reviewing its own output shares its blind spots; the adjudicator and critic are independent |
| Structured output everywhere (Pydantic + JSON-schema decoding) | Every decision is machine-checkable; invalid output is retried with the validation error |
| Deterministic query compiler | LLMs design concepts well but often get database syntax wrong; the compiler guarantees valid PubMed/arXiv queries |
| Criterion-level screening checks | The rationale is explicit (which criterion failed), not a vague "irrelevant" |
| Local models (Ollama) | Privacy, zero cost, and reproducibility (pinned model digests in the manifest) |
| Cached HTTP | The same search can be replayed exactly; searches are dated in the report |
| Deterministic rules where possible | Overall RoB = worst domain; citation checks; PRISMA counts computed, not generated |

## 6. Observability

Every event goes to `runs/<id>/trace.jsonl`. The event kinds are `stage`, `decision`, `tool`, `llm_call` (model, seconds, tokens), `screening`, `coordinator`, `hitl`, `interrupt` and `error`. The web app replays the file and streams new events through Server-Sent Events. `manifest.json` records the configuration and its fingerprint, the model digests, package versions, timings, PRISMA counts and metrics.


# 4. Demonstration on a real topic

**Question.** How accurate are large language models at title and abstract screening for systematic reviews?

**Flow.** 123 records (OpenAlex, PubMed, arXiv) → 8 duplicates removed → 115 screened by two agents (13 conflicts adjudicated) → 15 full texts assessed → **14 studies included**.

![PRISMA 2020 flow diagram](runs/demo-llm-screening/prisma_flow.png){ width=62% }

**Answer produced by the agents.** Large language models (LLMs) demonstrate high sensitivity in title and abstract screening for systematic reviews, with some studies reporting perfect sensitivity (100%) [S037], while specificity varies more widely, ranging from 81% [S037] to 99.9% [S034]. Overall accuracy ranges from 85.1% [S008] to 99.9% [S034]. However, the trade-off between sensitivity and specificity is a key consideration, as some models prioritize sensitivity at the expense of specificity. Time efficiency is also notable, with some studies reporting a 75% reduction in screening workload [S034] and others noting that LLMs can complete screening in under 1 day for a fraction of the cost of human screening [S033]. While LLMs show strong inter-rater agreement with human reviewers in some cases [S034], variability in performance across domains and prompting strategies suggests that further refinement is needed for consistent and reliable use.

**Themes.**

- **Accuracy of LLMs in Title and Abstract Screening** (certainty: low): Large language models (LLMs) demonstrate high sensitivity in identifying relevant studies for title and abstract screening, with some studies reporting perfect sensitivity (100%). Specificity, however, varies more widely, with some models achieving high specificity (e.g., 99.9%) and others reporting lower values (e.g., 81%). Overall accuracy ranges from 85.1% to 99.9%, depending on the model and context. The trade-off between sensitivity and specificity is a key consideration, as some models prioritize sensitivity at the expense of specificity.
- **Time Efficiency of LLMs in Screening** (certainty: low): LLMs significantly reduce the workload for title and abstract screening compared to traditional methods. Some studies report a 75% reduction in screening workload (S034), while others note that LLMs can complete screening in under 1 day for a fraction of the cost of human screening (S033). However, S032 only provides qualitative statements about time efficiency, with no numeric workload reduction reported.
- **Variability in LLM Performance Across Domains and Prompting Strategies** (certainty: low): LLM performance in title and abstract screening varies depending on the domain (e.g., clinical vs. preclinical) and the prompting strategy used. For example, models like GPT-4o and DeepSeek show consistent performance across different domains, while others struggle with tightly scoped or highly specialized topics. Prompt optimization, such as the use of pre-specified rules and early 
- **Inter-Rater Agreement and Consistency Between LLMs and Human Reviewers** (certainty: low): LLMs show strong inter-rater agreement with human reviewers in title and abstract screening, with some studies reporting Cohen's Kappa of 99.8% (S034) and high consistency in inter-rater reliability (S032). However, other studies report lower agreement, suggesting variability in how well LLMs align with human judgment.

![Risk of bias](runs/demo-llm-screening/risk_of_bias.png){ width=60% }

The critic agent sent the synthesis back twice: it detected, for example, a specificity value attributed to the wrong study and a mis-reported range. It approved the third version. This shows that the QA loop corrects real errors.

# 5. Evaluation

Run `demo-llm-screening`, question: *How accurate are large language models at title and abstract screening for systematic reviews?*. Evaluated on 2026-11-15; the evaluation itself took 122 s.

![Evaluation](docs/img/evaluation.png)

| Dimension | Metric | Result |
|---|---|---|
| Completeness | PRISMA 2020 checklist items present in the manuscript | **27/27** |
| Completeness | Pipeline stages completed autonomously | 10/10 |
| Completeness | Extraction fields filled (not "not reported") | 86% |
| Accuracy | Extracted quotes found verbatim in the source text | **92%** |
| Accuracy | Screening vs silver standard (gpt-oss:20b, n=30): accuracy | **83%** |
| Accuracy | … sensitivity / specificity | 86% / 83% (TP 6, FP 4, FN 1, TN 19) |
| Accuracy | Known key papers retrieved / passed screening | 2/4 / 2/4 |
| Consistency | Cohen's κ between screening agents A and B | **0.767** (raw agreement 89%, 13 conflicts adjudicated) |
| Consistency | Reproducibility: identical decisions when re-screening 20 records | **100%** |
| Consistency | Critic rounds / final approval | 3 / True |
| Time | End-to-end pipeline | **15.6 min** (383 LLM calls) |
| Time | Manual estimate for the same workload | ~27.9 h (2 reviewers × (0.5 min per abstract + 10 min per full text + 45 min per extraction/appraisal)) |
| Time | Speed-up | **×107.3** |

## Known-item test

| Study | Record | Outcome |
|---|---|---|
| Automated paper screening for clinical reviews using large language models | — | not retrieved |
| Assessing the ability of ChatGPT to screen articles for systematic reviews | — | not retrieved |
| Can large language models replace humans in systematic reviews? Evaluating GPT-4 | S066 | passed screening |
| Title and abstract screening for literature reviews using large language models: | S082 | passed screening |

## How to read these numbers

- The **silver standard** is an independent model, not a human gold standard. It measures consistency with a stronger, different reviewer, not the absolute truth.
- The **manual estimate** counts reviewer time only (two reviewers), not protocol writing, searching or writing the manuscript, so the real saving is larger.
- **Known-item recall** depends on the search budget (records per query) and on API rate limits (Semantic Scholar often returns HTTP 429 without an API key).


# 6. Discussion and limitations

- **Full texts.** Only open-access full texts are retrieved; the other studies are assessed on the abstract and flagged in the PRISMA diagram.
- **No human gold standard.** The reference used for accuracy is an independent model (silver standard), not human reviewers. A benchmark on published Cochrane reviews is the next step.
- **Model size.** Local 8B/20B models are private, free and reproducible, but weaker than frontier models. Their slips are handled by schema validation, deterministic repairs, quote verification and the critic loop.
- **Search budget.** The budget (records per query, full-text cap) trades completeness against time. Semantic Scholar often rate-limits requests without an API key.

# 7. User guide

See `docs/USER_GUIDE.md`. In short:

- `uvicorn prisma_agent.server:app` starts the web app;
- `python -m prisma_agent.cli run "question" --hitl human` runs a review in the terminal;
- `python -m prisma_agent.cli replay <run> --from-node synthesis` re-runs from a stage (time travel);
- `python -m prisma_agent.mcp_server` starts the MCP server;
- `python scripts/evaluate.py <run>` evaluates a run.

# 8. Team and individual contribution

The team has a single member, **Mohammed HADDAD (HADDADmed)**, who carried out the whole project:

- requirements analysis and architecture;
- implementation of all agents, the LangGraph workflow, human-in-the-loop checkpoints, the source clients, deduplication, the hybrid RAG pipeline and quote verification;
- the reporting module (PRISMA diagram, manuscript, BibTeX, checklist, manifest);
- the web application, CLI and MCP server;
- tests, the evaluation and the demonstration runs;
- documentation, presentation and video.

# References

- Page MJ, et al. The PRISMA 2020 statement: an updated guideline for reporting systematic reviews. *BMJ* 2021;372:n71.
- Borah R, Brown AW, Capers PL, Kaiser KA. Analysis of the time and workers needed to conduct systematic reviews of medical interventions using data from the PROSPERO registry. *BMJ Open* 2017;7:e012545.
- Cormack GV, Clarke CLA, Buettcher S. Reciprocal rank fusion outperforms Condorcet and individual rank learning methods. *SIGIR* 2009.
- LangGraph documentation, https://langchain-ai.github.io/langgraph/
