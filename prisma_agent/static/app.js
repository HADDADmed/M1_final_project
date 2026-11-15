// PRISMA-Agent front-end
const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
marked.use({ renderer: { html: t => esc(t.text ?? t) } });

const STEPS = [
  ["planner", "Protocol"], ["search_strategist", "Search strategy"], ["retrieval", "Retrieval"],
  ["screener", "Dual screening"], ["adjudicator", "Adjudication"], ["fulltext", "Full text + RAG"],
  ["extractor", "Extraction & RoB"], ["writer", "Synthesis"], ["critic", "Critic QA"], ["reporter", "Report"]];
const AGENT_COLOR = { planner: "#4338ca", search_strategist: "#0ea5e9", retrieval: "#0891b2", screener_A: "#16a34a",
  screener_B: "#65a30d", screeners: "#16a34a", adjudicator: "#d97706", fulltext: "#9333ea", fulltext_screener: "#9333ea",
  rag: "#9333ea", extractor: "#db2777", appraiser: "#e11d48", writer: "#2563eb", critic: "#dc2626", coordinator: "#18181b",
  reporter: "#15803d", human: "#c2410c", auto: "#71717a", graph: "#18181b", api: "#71717a", cli: "#71717a" };

// ------------------------------------------------------------ navigation
document.querySelectorAll(".nav").forEach(b => b.onclick = () => show(b.dataset.view));
function show(v) {
  document.querySelectorAll(".nav").forEach(n => n.classList.toggle("on", n.dataset.view === v));
  document.querySelectorAll(".view").forEach(n => n.classList.toggle("on", n.id === "v-" + v));
  if (v === "arch") window.drawDiagrams?.();
  if (v === "eval") loadEval();
}

// ------------------------------------------------------------ runs list
async function loadRuns(active) {
  const runs = await (await fetch("/api/reviews")).json();
  $("#runs").innerHTML = `<h5>Reviews</h5><button class="run-item ${!active ? "on" : ""}" data-new>＋ New review</button>` +
    runs.map(r => `<button class="run-item ${r.run_id === active ? "on" : ""}" data-id="${r.run_id}">${esc(r.question.slice(0, 64))}…
      <small>${r.status}${r.metrics ? ` · ${r.metrics.included} studies` : ""}</small></button>`).join("");
  $("#runs").querySelectorAll("[data-id]").forEach(b => b.onclick = () => openRun(b.dataset.id));
  $("#runs [data-new]").onclick = () => { closeRun(); loadRuns(); };
  return runs;
}

// ------------------------------------------------------------ start
document.querySelectorAll(".examples button").forEach(b => b.onclick = () => $("#question").value = b.textContent);
$("#go").onclick = async () => {
  const question = $("#question").value.trim();
  if (!question) return $("#question").focus();
  $("#go").disabled = true;
  const r = await (await fetch("/api/reviews", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, hitl: $("#hitl").value, max_per_query: +$("#mpq").value, max_fulltext: +$("#mft").value, year_from: +$("#yf").value }) })).json();
  $("#go").disabled = false;
  openRun(r.run_id);
};

// ------------------------------------------------------------ run view
let RUN = null, ES = null, STATE = null, stageSeen = {}, refreshTimer = null, llmCalls = 0, curTab = "overview";
function closeRun() { RUN = null; ES?.close(); $("#start").style.display = ""; $("#runview").style.display = "none"; history.replaceState(null, "", "#"); }

function openRun(id) {
  RUN = id; ES?.close(); stageSeen = {}; llmCalls = 0; STATE = null;
  history.replaceState(null, "", "#run=" + id);
  show("review");
  $("#start").style.display = "none"; $("#runview").style.display = "";
  $("#rv-id").textContent = "Run " + id; $("#rv-q").textContent = ""; $("#feed").innerHTML = ""; $("#hitl-panel").innerHTML = "";
  renderStepper(); renderTabs(); loadRuns(id);
  ES = new EventSource(`/api/reviews/${id}/events`);
  ES.onmessage = m => onEvent(JSON.parse(m.data));
  refresh();
}

function onEvent(ev) {
  if (ev.kind === "replayed") return refresh();
  if (ev.kind === "run" && ev.question) $("#rv-q").textContent = ev.question;
  if (ev.kind === "llm_call") { llmCalls++; return; }
  if (ev.kind === "stage" && ev.status === "start") { stageSeen[ev.agent] = ev.t; renderStepper(); }
  feed(ev);
  if (["stage", "interrupt", "run", "error", "hitl"].includes(ev.kind) || (ev.kind === "decision" && ev.what)) {
    clearTimeout(refreshTimer); refreshTimer = setTimeout(refresh, 400);
  }
}

function feed(ev) {
  let d = "";
  if (ev.kind === "stage") d = `<b>${esc(ev.title)}</b>`;
  else if (ev.kind === "coordinator") d = `→ ${esc(ev.decision)} <span class="muted">(${esc(ev.reason)})</span>`;
  else if (ev.kind === "tool") d = `<span class="mono">${esc(ev.tool)}</span> ${ev.hits !== undefined ? `→ ${ev.hits} hits` : esc(ev.result || ev.chunks || "")}${ev.error ? ` <span class="exc">failed</span>` : ""}`;
  else if (ev.kind === "screening") d = `${ev.id} A:<span class="${ev.a === "include" ? "inc" : "exc"}">${ev.a}</span> B:<span class="${ev.b === "include" ? "inc" : "exc"}">${ev.b}</span> <span class="muted">${esc(ev.title.slice(0, 48))}…</span>`;
  else if (ev.kind === "decision") d = ev.what ? `${esc(ev.what)} ${ev.included !== undefined ? `· included ${ev.included}` : ""}${ev.kappa !== undefined ? ` · κ=${ev.kappa}` : ""}${ev.rate !== undefined ? ` · ${Math.round(ev.rate * 100)}% verified` : ""}${ev.unique !== undefined ? ` · ${ev.identified}→${ev.unique} unique` : ""}`
    : `${esc(ev.id || "")} ${esc(ev.final || ev.decision || ev.design || "")} ${ev.approve !== undefined ? (ev.approve ? "approved" : `${ev.issues} issues`) : ""} <span class="muted">${esc((ev.reason || "").slice(0, 90))}</span>`;
  else if (ev.kind === "hitl") d = `${esc(ev.what || "")} · ${esc(ev.status)} ${esc(ev.note || "")}`;
  else if (ev.kind === "interrupt") d = `<b style="color:#c2410c">Paused: waiting for a human decision</b>`;
  else if (ev.kind === "run") d = ev.status === "done" ? `<b class="inc">Review complete</b>` : `started · fingerprint ${esc(ev.fingerprint || "")}`;
  else if (ev.kind === "error") d = `<span class="exc">${esc(ev.message)}</span>`;
  else return;
  const el = document.createElement("div");
  el.className = "ev";
  el.innerHTML = `<span class="t">${esc(ev.ts || "")}</span><span><span class="a" style="color:${AGENT_COLOR[ev.agent] || "#18181b"}">${esc(ev.agent)}</span> <span class="d">${d}</span></span>`;
  const f = $("#feed"); f.prepend(el);
  while (f.children.length > 250) f.lastChild.remove();
}

function renderStepper() {
  const seen = STEPS.map(([a]) => a in stageSeen);
  const last = seen.lastIndexOf(true);
  const done = STATE?.status === "done";
  const tm = STATE?.values?.timings || {};
  const dur = k => Object.entries(tm).filter(([n]) => n.startsWith(k)).reduce((s, [, v]) => s + v, 0);
  const map = { planner: "protocol", search_strategist: "search_strategy", retrieval: "retrieval", screener: "ta_screening",
    adjudicator: "conflict_resolution", fulltext: "fulltext", extractor: "extraction", writer: "synthesis", critic: "critic", reporter: "report" };
  $("#stepper").innerHTML = STEPS.map(([a, label], i) => {
    const st = done || i < last ? "done" : i === last ? (STATE?.status === "waiting" ? "" : "active") : "";
    const t = dur(map[a]);
    return `<div class="step ${st}"><b>${label}</b><span>${st === "done" && t ? t.toFixed(0) + " s" : st === "active" ? "running…" : i === last && STATE?.status === "waiting" ? "awaiting you" : "—"}</span></div>`;
  }).join("");
}

async function refresh() {
  if (!RUN) return;
  const id = RUN;
  const s = await (await fetch(`/api/reviews/${id}/state`)).json();
  if (id !== RUN) return;
  STATE = s;
  const v = s.values || {};
  if (v.config) $("#rv-q").textContent = v.config.question;
  const st = $("#rv-status"); st.className = "status " + s.status;
  st.textContent = { running: "Running", waiting: "Waiting for you", done: "Complete", error: "Error", stopped: "Stopped" }[s.status] || s.status;
  renderStepper(); renderHitl(s.pending); renderPanels();
  if (s.status === "done") loadRuns(id);
}

// ------------------------------------------------------------ human in the loop
function renderHitl(p) {
  const box = $("#hitl-panel");
  if (!p || STATE.status !== "waiting") { box.innerHTML = ""; return; }
  if (p.type === "protocol_approval") {
    const pr = p.protocol;
    box.innerHTML = `<div class="hitl"><h3>Checkpoint 1 · Approve the review protocol</h3>
      <p class="muted" style="margin:0 0 10px">Nothing has been searched yet. Review the eligibility criteria (you can edit them), then approve.</p>
      <textarea id="prot-edit" style="width:100%;min-height:210px;font-family:'JetBrains Mono';font-size:12px;border:1px solid var(--line);border-radius:10px;padding:10px">${esc(JSON.stringify({ inclusion: pr.inclusion, exclusion: pr.exclusion }, null, 2))}</textarea>
      <div style="display:flex;gap:8px;margin-top:10px"><button class="btn accent" id="approve">Approve & start searching →</button></div></div>`;
    $("#approve").onclick = async () => {
      let edited = {};
      try { edited = JSON.parse($("#prot-edit").value); } catch (e) { return alert("Invalid JSON"); }
      await resume({ protocol: { ...pr, ...edited }, note: "approved in the web UI" });
    };
  } else if (p.type === "screening_conflicts") {
    box.innerHTML = `<div class="hitl"><h3>Checkpoint 2 · Resolve ${p.conflicts.length} reviewer disagreements</h3>
      <p class="muted" style="margin:0 0 10px">The two screening agents disagree on these records. Pre-selected: reviewer A (the more inclusive one).</p>
      <div class="scroll" style="max-height:380px"><table class="t"><thead><tr><th>ID</th><th>Title</th><th>Reviewer A</th><th>Reviewer B</th><th>Decision</th></tr></thead><tbody>
      ${p.conflicts.map(c => `<tr><td class="mono">${c.id}</td><td>${esc(c.title)}</td><td><span class="${c.a === "include" ? "inc" : "exc"}">${c.a}</span><div class="muted">${esc(c.a_reason)}</div></td>
        <td><span class="${c.b === "include" ? "inc" : "exc"}">${c.b}</span><div class="muted">${esc(c.b_reason)}</div></td>
        <td><select data-c="${c.id}"><option ${c.a === "include" ? "selected" : ""}>include</option><option ${c.a === "exclude" ? "selected" : ""}>exclude</option></select></td></tr>`).join("")}
      </tbody></table></div><div style="margin-top:10px"><button class="btn accent" id="resolve">Submit decisions →</button></div></div>`;
    $("#resolve").onclick = () => resume({ decisions: Object.fromEntries([...document.querySelectorAll("[data-c]")].map(s => [s.dataset.c, s.value])) });
  }
}
async function resume(value) {
  $("#hitl-panel").innerHTML = "";
  await fetch(`/api/reviews/${RUN}/resume`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ value }) });
  setTimeout(refresh, 300);
}

// ------------------------------------------------------------ tabs / panels
const TABS = [["overview", "Overview"], ["protocol", "Protocol"], ["search", "Search"], ["screening", "Screening"],
  ["evidence", "Evidence"], ["synthesis", "Synthesis"], ["report", "Report"]];
function renderTabs() {
  $("#tabs").innerHTML = TABS.map(([k, l]) => `<button class="tab ${k === curTab ? "on" : ""}" data-t="${k}">${l}</button>`).join("");
  $("#tabs").querySelectorAll(".tab").forEach(b => b.onclick = () => { curTab = b.dataset.t; renderTabs(); renderPanels(); });
}
const pct = x => x == null ? "—" : Math.round(x * 100) + "%";
const tile = (v, l) => `<div class="card tile"><div class="v">${v}</div><div class="l">${l}</div></div>`;
const art = f => `/runs/${RUN}/${f}`;

function renderPanels() {
  const v = STATE?.values || {}, P = $("#panels");
  const byid = Object.fromEntries((v.records || []).map(r => [r.id, r]));
  const T = {
    overview: () => {
      const m = v.metrics || {};
      return `<div class="tiles">${tile(Object.values(v.identified || {}).reduce((a, b) => a + b, 0) || "—", "records identified")}
        ${tile((v.screening || []).length || "—", "records screened (×2 agents)")}${tile(v.included ? v.included.length : "—", "studies included")}
        ${tile(v.kappa ?? "—", "Cohen's κ between screeners")}${tile(pct(v.grounding), "extracted quotes verified")}
        ${tile(pct(v.checklist_coverage), "PRISMA 2020 items reported")}${tile(m.pipeline_minutes ? m.pipeline_minutes + " min" : "—", "pipeline time")}
        ${tile(m.manual_estimate_hours ? m.manual_estimate_hours + " h" : "—", "manual-review estimate")}</div>
        ${v.report_dir ? `<div class="card box"><img class="fig" src="${art("prisma_flow.png")}" style="max-width:640px"></div>` : `<div class="empty-note">The PRISMA flow diagram appears when the report is generated.</div>`}`;
    },
    protocol: () => !v.protocol ? `<div class="empty-note">The planner is writing the protocol…</div>` : (p => `<div class="card box"><h3>${esc(p.title)}</h3><p class="muted">${esc(p.objective)}</p>
        <div class="kvs"><span>Framework</span><span>${p.framework}</span><span>Population</span><span>${esc(p.population)}</span>
        <span>Intervention / concept</span><span>${esc(p.intervention_or_concept)}</span><span>Comparator</span><span>${esc(p.comparator)}</span>
        <span>Outcomes</span><span>${esc(p.outcomes.join(" · "))}</span><span>Designs</span><span>${esc(p.study_designs.join(" · "))}</span>
        <span>Risk-of-bias domains</span><span>${esc(p.rob_domains.join(" · "))}</span><span>Approved by</span><span>${esc(v.protocol_approved_by || "pending")}</span></div></div>
        <div class="card box"><div class="sect">Eligibility criteria</div>${[...p.inclusion, ...p.exclusion].map(c => `<div class="crit"><b>${c.id}</b><span>${esc(c.text)}</span></div>`).join("")}</div>
        <div class="card box"><div class="sect">Synthesis plan</div>${esc(p.synthesis_plan)}</div>`)(v.protocol),
    search: () => !v.strategy ? `<div class="empty-note">Search strategy not designed yet.</div>` : `<div class="card box"><div class="sect">Concept blocks (AND between blocks, OR within)</div>
        ${v.strategy.concepts.map(c => `<div class="crit"><b>${esc(c.concept.slice(0, 4))}</b><span><b style="font-family:Inter;color:var(--ink)">${esc(c.concept)}</b>: ${esc(c.terms.join(" OR "))}${c.mesh.length ? ` · MeSH: ${esc(c.mesh.join(", "))}` : ""}</span></div>`).join("")}
        <p class="muted" style="margin-top:10px">${esc(v.strategy.rationale)}</p></div>
        <table class="t"><thead><tr><th>Round</th><th>Source</th><th>Query (compiled)</th><th>Hits</th></tr></thead><tbody>
        ${(v.search_log || []).map(x => `<tr><td>${x.round}</td><td>${x.source}</td><td class="mono" style="font-size:11.5px">${esc(x.query)}</td><td>${x.hits}${x.error ? ' <span class="exc">rate-limited</span>' : ""}</td></tr>`).join("")}</tbody></table>
        <p class="muted">${v.duplicates ?? 0} duplicates removed (DOI + fuzzy title) → ${(v.records || []).length} unique records.</p>`,
    screening: () => !v.screening ? `<div class="empty-note">${v.records ? `Two agents are screening ${v.records.length} records: the decisions appear live in the audit trail →` : "Screening has not started."}</div>` : `<p class="muted">Two independent agents checked every criterion of every record. Agreement κ = <b>${v.kappa}</b>. Disagreements: ${v.conflicts} → adjudicator / human.</p>
        <div class="scroll"><table class="t"><thead><tr><th>ID</th><th>Title</th><th>A</th><th>B</th><th>Final</th><th>Rationale</th></tr></thead><tbody>
        ${v.screening.map(x => `<tr><td class="mono">${x.id}</td><td>${esc(x.title)}</td><td class="${x.a === "include" ? "inc" : "exc"}">${x.a[0].toUpperCase()}</td>
          <td class="${x.b === "include" ? "inc" : "exc"}">${x.b[0].toUpperCase()}</td><td class="${x.final === "include" ? "inc" : "exc"}">${x.final || "…"}<div class="muted" style="font-weight:400">${x.final_by || ""}</div></td>
          <td class="muted">${esc(x.final_reason || x.a_reason)}</td></tr>`).join("")}</tbody></table></div>`,
    evidence: () => !v.extractions ? `<div class="empty-note">Evidence appears after extraction.</div>` : `${v.report_dir ? `<div class="card box"><img class="fig rob" src="${art("risk_of_bias.png")}"></div>` : ""}
        ${Object.entries(v.extractions).map(([i, e]) => `<div class="card box"><h3>${i} · ${esc(byid[i]?.title)}</h3><div class="muted" style="margin-bottom:8px">${esc(byid[i]?.authors?.slice(0, 3).join(", "))} · ${byid[i]?.year} · RoB: <b>${v.rob?.[i]?.overall}</b> · ${(v.fulltext || []).find(f => f.id === i)?.availability || ""}</div>
          <div class="kvs">${[["Design", "study_design"], ["Data", "population_or_dataset"], ["Method", "intervention_or_method"], ["Comparator", "comparator"], ["Main finding", "main_finding"], ["Limitations", "limitations"]]
            .map(([l, k]) => `<span>${l}</span><span>${esc(e[k].value)} ${e[k].quote ? `<div class="quote">“${esc(e[k].quote)}” <span class="${e[k].verified ? "ok-mark" : "no-mark"}">${e[k].verified ? "✓ verified" : "unverified"}</span> <span class="mono muted">${esc(e[k].chunk)}</span></div>` : ""}</span>`).join("")}
          <span>Results</span><span>${e.results.map(r => `${esc(r.metric)} = <b>${esc(r.value)}</b> <span class="muted">(${esc(r.outcome)})</span> <span class="${r.verified ? "ok-mark" : "no-mark"}">${r.verified ? "✓" : "·"}</span>`).join("<br>") || "—"}</span></div></div>`).join("")}`,
    synthesis: () => !v.synthesis ? `<div class="empty-note">Synthesis not written yet.</div>` : `<div class="card box"><div class="sect">Answer to the review question</div>${marked.parse(v.synthesis.overall_answer)}</div>
        ${v.synthesis.themes.map(t => `<div class="card theme"><h3>${esc(t.theme)} <span class="chip">certainty: ${t.certainty}</span></h3>${marked.parse(t.summary)}<p class="muted" style="margin:6px 0 0;font-size:12.5px">${esc(t.certainty_reason)}</p></div>`).join("")}
        <div class="card box"><div class="sect">Critic agent (${(v.critiques || []).length} round(s))</div>${(v.critiques || []).map(c => `<p><b>Round ${c.round}: ${c.approve ? '<span class="inc">approved</span>' : '<span class="exc">revision requested</span>'}</b></p><ul>${c.issues.map(i => `<li><b>[${i.severity}]</b> ${esc(i.section)}: ${esc(i.problem)}</li>`).join("")}</ul>`).join("")}</div>`,
    report: () => !v.report_dir ? `<div class="empty-note">The manuscript is generated at the end of the pipeline.</div>` : `<div class="dl">
        ${["report.md", "report.docx", "references.bib", "evidence_table.csv", "screening_log.csv", "protocol.json", "extractions.json", "trace.jsonl", "manifest.json", "prisma_checklist.json"].map(f => `<a class="btn" href="${art(f)}" download>${f}</a>`).join("")}</div>
        <div class="card box md" id="mdbox">Loading…</div>`,
  };
  P.innerHTML = T[curTab]();
  if (curTab === "report" && v.report_dir) fetch(art("report.md")).then(r => r.text()).then(t => {
    $("#mdbox").innerHTML = marked.parse(t.replace(/\]\(([\w_]+\.png)\)/g, `](${art("$1")})`));
  });
}

// ------------------------------------------------------------ architecture cards & docs
const AGENTS = [
  ["Coordinator", "Routes between stages and decides to loop back (broaden the search when too few records are found, revise the synthesis when the critic rejects it). Every routing decision is logged with its reason."],
  ["Planner", "Turns the question into a PRISMA 2020 protocol: PICO/PCC/SPIDER framing, numbered inclusion/exclusion criteria, risk-of-bias domains, synthesis plan. Human checkpoint #1."],
  ["Search strategist", "Designs concept blocks with synonyms and MeSH headings. A deterministic compiler turns them into PubMed, arXiv, OpenAlex and Semantic Scholar syntax."],
  ["Retrieval agent", "Queries four databases in parallel (cached, rate-limited), deduplicates by DOI and fuzzy title, then builds the hybrid RAG index from open-access full texts."],
  ["Screening agents ×2", "Two independent reviewers check every criterion of every record (yes / no / unclear) and justify the decision. Agreement is measured with Cohen's κ."],
  ["Adjudicator", "A different model family (gpt-oss:20b) settles disagreements, or the human does (checkpoint #2)."],
  ["Extraction & appraisal", "Fills a structured form from re-ranked full-text passages. Every value carries a verbatim quote and chunk id, verified automatically against the source. Then judges the risk of bias per domain."],
  ["Synthesis & writing", "Groups the evidence into themes with GRADE-style certainty and assesses meta-analysis feasibility. Writes only from the evidence table."],
  ["Critic / QA", "An independent model plus deterministic checks (citations exist, coverage) review the synthesis. Major issues trigger a revision loop."],
];
const TECH = [
  ["LangGraph", "Stateful orchestration", "The review is a StateGraph: typed state, conditional edges, revision loops, interrupt() for human approval, RetryPolicy, SQLite checkpointing (resume after a crash or a pause).", "prisma_agent/graph.py"],
  ["LangChain Core", "Agent building blocks", "Chat models, structured output (JSON-schema constrained decoding), callbacks for tracing, documents and retrievers.", "prisma_agent/llm.py"],
  ["Ollama · qwen3:8b + gpt-oss:20b", "Local LLM backbone", "All inference is local: private, free and reproducible (fixed seeds, temperature 0, model digests in the manifest). Two model families: one does the work, the other judges it.", "config.py"],
  ["Hybrid RAG", "Grounded reading", "FAISS dense vectors (nomic-embed-text) + BM25 sparse ranking, fused with Reciprocal Rank Fusion, re-ranked by a FlashRank cross-encoder, compressed to the most relevant sentences.", "prisma_agent/rag.py"],
  ["OpenAlex · PubMed · arXiv · Semantic Scholar", "Multi-source search", "Four open scholarly APIs, with polite rate limiting, retries and an on-disk response cache so that a search can be replayed exactly.", "prisma_agent/sources/"],
  ["PyMuPDF", "Full-text parsing", "Downloads open-access PDFs and extracts the text (references removed) for chunking and indexing.", "agents.fulltext()"],
  ["RapidFuzz", "Dedup & verification", "Fuzzy title matching for deduplication; verifies that every extracted quote really occurs in the source text.", "dedup.py · verify_quote()"],
  ["Pydantic v2", "Typed agent I/O", "Every agent answers through a schema (Protocol, ScreeningDecision, Extraction, RiskOfBias, Synthesis, Critique); invalid answers are retried with the validation error.", "schemas.py"],
  ["MCP (Model Context Protocol)", "Interoperability", "The pipeline is also exposed as an MCP server (search_literature, run_review, get_report), so any MCP client can drive it.", "prisma_agent/mcp_server.py"],
  ["FastAPI + SSE", "Live web app", "Runs reviews in the background, streams the audit trail live, and resumes the graph after human decisions.", "prisma_agent/server.py"],
  ["matplotlib · python-docx", "Outputs", "PRISMA 2020 flow diagram, risk-of-bias traffic light, evidence map, results plot; manuscript in Markdown and DOCX; BibTeX.", "prisma_agent/report.py"],
  ["rough.js · Playwright · pytest", "Docs & quality", "Hand-drawn architecture diagrams; headless-browser end-to-end demo and video; unit tests.", "static/diagrams.js · tests/"],
];
$("#agent-cards").innerHTML = AGENTS.map(([n, d], i) => `<div class="card cardx"><div class="k">${i}· ${n}</div><p>${d}</p></div>`).join("");
$("#techs").innerHTML = TECH.map(([n, tag, p, w]) => `<div class="card cardx"><div style="display:flex;justify-content:space-between;gap:8px"><h3>${n}</h3><span class="tag">${tag}</span></div><p>${p}</p><div class="where">${w}</div></div>`).join("");

// ------------------------------------------------------------ evaluation
async function loadEval() {
  const r = await fetch("/static/evaluation.json");
  if (!r.ok) { $("#eval-body").innerHTML = '<div class="empty-note">Run <code>python scripts/evaluate.py RUN_ID</code> to generate the evaluation.</div>'; return; }
  const e = await r.json();
  $("#eval-body").innerHTML = `<p class="muted">Demonstration run <span class="mono">${esc(e.run_id)}</span>: “${esc(e.question)}”</p>
    <div class="tiles">${e.tiles.map(([v, l]) => tile(v, l)).join("")}</div>
    ${e.sections.map(s => `<h2>${esc(s.title)}</h2><p class="muted">${esc(s.note || "")}</p><table class="t"><thead><tr>${s.head.map(h => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${s.rows.map(r => `<tr>${r.map(c => `<td>${esc(c)}</td>`).join("")}</tr>`).join("")}</tbody></table>`).join("")}
    ${e.figures.map(f => `<div class="card box" style="margin-top:14px"><img class="fig" src="${f}"></div>`).join("")}`;
}

// ------------------------------------------------------------ boot
(async () => {
  await loadRuns();
  const m = location.hash.match(/run=([\w.-]+)/);
  if (m) openRun(m[1]);
  else if (location.hash.length > 1) show(location.hash.slice(1));
})();
