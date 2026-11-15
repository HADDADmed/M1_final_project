# User guide

## 1. Install

```bash
# Local models (Ollama ≥ 0.12)
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen3:8b            # worker agents             ~5 GB
ollama pull gpt-oss:20b         # adjudicator + critic       ~13 GB  (optional: set judge_model to qwen3:8b)
ollama pull nomic-embed-text    # embeddings for RAG

# Python 3.11
git clone https://github.com/HADDADmed/M1_final_project.git && cd M1_final_project
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium     # only for scripts/demo_video.py
```

A GPU is recommended. On an RTX-class GPU a review of ~100 records takes about 10–15 minutes; a CPU works, but slowly.

## 2. Run a review

### Web app (recommended)

```bash
uvicorn prisma_agent.server:app --port 8000
```

Open http://127.0.0.1:8000, type a research question, choose the oversight mode and click **Start review**.

| Option | Meaning |
|---|---|
| Oversight: *Human-in-the-loop* | The graph pauses at **checkpoint 1** (protocol approval; you can edit the criteria as JSON) and at **checkpoint 2** (reviewer disagreements) |
| Oversight: *Fully autonomous* | The checkpoints are approved automatically; the adjudicator model resolves conflicts (logged as `auto`) |
| Records / query | Number of results requested per query and per database |
| Full-text budget | Maximum number of reports read in full (highest screening confidence first; the rest are reported as "not sought") |
| From year | Publication-date filter |

While it runs you see the **stepper** (10 stages with timings), the **live audit trail**, and tabs that fill in progressively: Overview · Protocol · Search · Screening · Evidence · Synthesis · Report.

### Command line

```bash
python -m prisma_agent.cli run "Does retrieval-augmented generation reduce hallucinations of LLMs?" \
       --hitl human --max-per-query 25 --max-fulltext 15
python -m prisma_agent.cli show <run_id>         # metrics of a finished run
```

In `--hitl human` mode the terminal shows the protocol and asks for approval, then shows each conflict and asks for your decision.

### As an MCP server

```bash
python -m prisma_agent.mcp_server       # stdio transport
```

Tools: `search_literature(query, sources, per_source, year_from)`, `run_review(question, …)`, `list_reviews()`, `get_report(run_id)`. Example client configuration:

```json
{"mcpServers": {"prisma-agent": {"command": "/path/to/.venv/bin/python", "args": ["-m", "prisma_agent.mcp_server"],
  "cwd": "/path/to/M1_final_project"}}}
```

## 3. Outputs (`runs/<run_id>/`)

| File | Content |
|---|---|
| `report.md`, `report.docx` | Full manuscript following the PRISMA 2020 structure (abstract → availability) |
| `prisma_flow.png/.svg` | PRISMA 2020 flow diagram (computed counts) |
| `risk_of_bias.png`, `evidence_map.png`, `results_plot.png` | Figures |
| `protocol.json` | The approved protocol |
| `screening_log.csv` | Every record: decisions and rationales of A and B, final decision, who decided |
| `evidence_table.csv`, `extractions.json` | Extracted data with quotes, chunk ids and verification flags; RoB; full-text decisions; synthesis; critiques |
| `references.bib` | BibTeX of the included studies |
| `prisma_checklist.json` | The 27 PRISMA 2020 items and whether each is reported |
| `trace.jsonl` | Complete audit trail |
| `manifest.json` | Configuration, fingerprint, model digests, package versions, timings, metrics |
| `checkpoints.db` | LangGraph checkpoints (resume / inspect any intermediate state) |

## 4. Configuration (`prisma_agent/config.py`)

`worker_model`, `judge_model`, `embed_model`, `seed`, `temperature`, `sources`, `max_per_query`, `year_from`, `max_fulltext`, `hitl`, `max_critic_rounds`. The configuration is hashed into a **fingerprint** stored in the manifest: two runs with the same fingerprint, the same model digests and the same HTTP cache reproduce the same decisions.

## 5. Evaluate a run

```bash
python scripts/evaluate.py <run_id> --silver 30 --repro 20
```

This computes completeness (PRISMA items, extraction coverage), accuracy (quote grounding, known items, a silver standard from an independent model), consistency (κ, reproducibility, critic) and time savings. It writes `docs/EVALUATION.md` and the data for the **Evaluation** page of the web app.

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| `semanticscholar … 429` in the search log | Expected without an API key; the run continues with the other sources (logged in the report) |
| OpenAlex errors | The free daily budget is shared per IP; responses are cached, so retry later or reduce `max_per_query` |
| Run stuck at "Waiting for you" | It's a human-in-the-loop checkpoint: answer it in the yellow panel |
| Out of GPU memory | Use `judge_model = "qwen3:8b"` |
