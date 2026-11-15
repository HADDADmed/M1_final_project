// Hand-drawn architecture diagrams (rough.js + Caveat), drawn live in the browser.
(function () {
  const INK = "#1f2328", MUTED = "#6b6b6b", NS = "http://www.w3.org/2000/svg";
  const C = { yellow: "#ffe8a3", blue: "#c7e9ff", green: "#d9f7be", pink: "#ffd6e7", orange: "#ffe7ba",
              violet: "#e8e1ff", grey: "#eeeeee", cream: "#fff1b8", red: "#ffd5d5" };

  function Sketch(svg, seed = 7) {
    svg.innerHTML = "";
    const rc = rough.svg(svg, { options: { seed, roughness: 1.15, bowing: 1.1, stroke: INK, strokeWidth: 1.5 } });
    const add = n => (svg.appendChild(n), n);
    const text = (x, y, s, o = {}) => {
      const t = document.createElementNS(NS, "text");
      t.setAttribute("x", x); t.setAttribute("y", y);
      t.setAttribute("text-anchor", o.anchor || "middle");
      t.setAttribute("font-family", "Caveat, cursive");
      t.setAttribute("font-size", o.size || 22); t.setAttribute("font-weight", o.weight || 700);
      t.setAttribute("fill", o.color || INK);
      t.textContent = s; return add(t);
    };
    const box = (x, y, w, h, title, sub = [], fill = C.grey, o = {}) => {
      add(rc.rectangle(x, y, w, h, { fill, fillStyle: "hachure", hachureGap: 7, fillWeight: 1.1, hachureAngle: -41,
                                     strokeWidth: o.strong ? 2.2 : 1.6, strokeLineDash: o.dashed ? [8, 6] : undefined }));
      const lines = (Array.isArray(sub) ? sub : [sub]).filter(Boolean);
      const top = y + h / 2 - (lines.length * 9) + 4;
      text(x + w / 2, top, title, { size: o.size || 25 });
      lines.forEach((l, i) => text(x + w / 2, top + 22 + i * 19, l, { size: 18, weight: 500, color: "#3a3a3a" }));
    };
    const pill = (cx, cy, w, h, label, fill = INK) => {
      add(rc.ellipse(cx, cy, w, h, { fill, fillStyle: "solid", strokeWidth: 1.6 }));
      text(cx, cy + 7, label, { size: 21, color: fill === INK ? "#fff" : INK });
    };
    const head = (p, q) => {
      const a = Math.atan2(q[1] - p[1], q[0] - p[0]), L = 13, s = 0.45;
      add(rc.linearPath([[q[0] - L * Math.cos(a - s), q[1] - L * Math.sin(a - s)], q,
                         [q[0] - L * Math.cos(a + s), q[1] - L * Math.sin(a + s)]], { strokeWidth: 1.6, roughness: 0.6 }));
    };
    const arrow = (pts, o = {}) => {
      const opt = { strokeWidth: o.w || 1.5, strokeLineDash: o.dashed ? [7, 6] : undefined, stroke: o.color || INK };
      add(pts.length > 2 && o.curve !== false ? rc.curve(pts, opt) : rc.linearPath(pts, opt));
      if (!o.noHead) head(pts[pts.length - 2], pts[pts.length - 1]);
      if (o.both) head(pts[1], pts[0]);
      if (o.label) text(o.lx ?? (pts[0][0] + pts[pts.length - 1][0]) / 2, o.ly ?? (pts[0][1] + pts[pts.length - 1][1]) / 2 - 8,
                        o.label, { size: o.size || 19, weight: 500, color: o.lcolor || "#2b2b2b", anchor: o.anchor });
    };
    const badge = (x, y, n) => {
      add(rc.circle(x, y, 24, { fill: "#4338ca", fillStyle: "solid", stroke: "#4338ca", roughness: 0.8 }));
      text(x, y + 6, n, { size: 17, color: "#fff" });
    };
    return { rc, add, text, box, pill, arrow, badge };
  }


  function system() {
    const s = Sketch(document.getElementById("d-system"), 4);
    s.add(s.rc.rectangle(12, 10, 1076, 480, { strokeLineDash: [10, 8], stroke: "#9a9a9a", strokeWidth: 1.2, roughness: 1.6 }));
    s.text(30, 478, "local machine · open-weight models · nothing sent to an LLM provider", { anchor: "start", size: 19, color: MUTED, weight: 500 });
    s.box(30, 60, 180, 90, "Researcher", ["question · approvals"], C.violet);
    s.box(30, 210, 180, 100, "Web UI / CLI / MCP", ["live pipeline", "human checkpoints"], C.violet);
    s.box(280, 200, 200, 120, "FastAPI", ["SSE audit stream", "resume interrupts"], C.green);
    s.box(550, 150, 250, 220, "LangGraph", ["8 agents · coordinator", "typed ReviewState", "interrupt() · RetryPolicy", "critic loop"], C.yellow, { strong: true, size: 30 });
    s.box(870, 30, 200, 90, "Ollama", ["qwen3:8b · gpt-oss:20b", "nomic-embed-text"], C.blue);
    s.box(870, 150, 200, 95, "Scholarly APIs", ["OpenAlex · PubMed", "arXiv · Semantic Scholar"], C.orange);
    s.box(870, 275, 200, 90, "Hybrid RAG", ["FAISS + BM25 + RRF", "FlashRank rerank"], C.pink);
    s.box(870, 390, 200, 70, "SQLite checkpoints", "resume · time travel", C.grey, { size: 22 });
    s.box(550, 400, 250, 62, "Audit & artefacts", "trace.jsonl · manifest · report", C.cream, { size: 22 });
    s.arrow([[120, 150], [120, 210]], { both: true });
    s.arrow([[210, 260], [280, 260]], { both: true, label: "HTTP", ly: 248 });
    s.arrow([[480, 260], [550, 260]], { both: true, label: "astream", ly: 248 });
    s.arrow([[800, 190], [835, 110], [870, 80]], { both: true });
    s.arrow([[800, 230], [870, 200]], { both: true });
    s.arrow([[800, 290], [870, 318]], { both: true });
    s.arrow([[800, 350], [835, 400], [870, 425]], { both: true });
    s.arrow([[675, 370], [675, 400]]);
  }

  function graph() {
    const s = Sketch(document.getElementById("d-graph"), 9);
    const W = 150, H = 66;
    const N = {
      start: [30, 40], planner: [150, 22], review: [340, 22], strategist: [530, 22], retrieval: [720, 22],
      screening: [720, 170], conflicts: [530, 170], fulltext: [340, 170], extraction: [150, 170],
      synthesis: [150, 330], critic: [340, 330], report: [530, 330], end: [740, 362] };
    s.pill(62, 55, 80, 40, "START");
    const B = (k, title, sub, fill, o = {}) => s.box(N[k][0], N[k][1], W, H, title, sub, fill, { size: 22, ...o });
    B("planner", "Planner", "protocol", C.yellow);
    B("review", "Human ✋ #1", "approve protocol", C.red, { dashed: true });
    B("strategist", "Search strategist", "concepts · MeSH", C.blue);
    B("retrieval", "Retrieval", "4 APIs · dedup", C.blue);
    B("screening", "Screeners A ‖ B", "every criterion", C.green);
    B("conflicts", "Adjudicator / ✋ #2", "resolve conflicts", C.red, { dashed: true });
    B("fulltext", "Full text + RAG", "PDF · hybrid index", C.pink);
    B("extraction", "Extract & RoB", "verified quotes", C.pink);
    B("synthesis", "Synthesis", "themes · GRADE", C.violet);
    B("critic", "Critic (QA)", "independent model", C.orange);
    B("report", "Report", "PRISMA · DOCX · Bib", C.cream);
    s.pill(782, 398, 80, 40, "END");
    const R = k => [N[k][0] + W, N[k][1] + H / 2], L = k => [N[k][0], N[k][1] + H / 2],
          T = k => [N[k][0] + W / 2, N[k][1]], Bo = k => [N[k][0] + W / 2, N[k][1] + H];
    s.arrow([[102, 55], L("planner")]);
    s.arrow([R("planner"), L("review")]); s.arrow([R("review"), L("strategist")]); s.arrow([R("strategist"), L("retrieval")]);
    s.arrow([Bo("retrieval"), T("screening")], { dashed: true, label: "≥ 20 records", lx: 840, ly: 140 });
    s.arrow([[870, 40], [960, 30], [990, 100], [960, 130], [870, 70]], { dashed: true, label: "too few → broaden", lx: 1000, ly: 160, anchor: "middle" });
    s.arrow([L("screening"), R("conflicts")]); s.arrow([L("conflicts"), R("fulltext")], { dashed: true, label: "≥3", ly: 194 });
    s.arrow([L("fulltext"), R("extraction")]);
    s.arrow([Bo("extraction"), T("synthesis")]); s.arrow([R("synthesis"), L("critic")]);
    s.arrow([R("critic"), L("report")], { dashed: true, label: "ok", ly: 354 });
    s.arrow([Bo("critic"), [415, 450], [225, 450], Bo("synthesis")], { dashed: true, label: "major issues → revise", lx: 320, ly: 478 });
    s.arrow([R("report"), [782, 398 - 20 + 2]]);
    s.add(s.rc.rectangle(900, 250, 180, 200, { fill: "#fbfaf7", fillStyle: "solid", stroke: "#bdbdbd", strokeWidth: 1 }));
    s.text(990, 278, "shared state", { size: 22 });
    ["protocol", "records", "screening", "included", "extractions", "rob", "synthesis", "critiques"].forEach((k, i) =>
      s.text(990, 304 + i * 18, k, { size: 17, weight: 500, color: "#3a3a3a" }));
    s.text(990, 470, "checkpointed after every node", { size: 16, weight: 500, color: MUTED });
  }

  function rag() {
    const s = Sketch(document.getElementById("d-rag"), 13);
    s.box(20, 120, 150, 80, "Open-access PDFs", "PyMuPDF", C.grey, { size: 21 });
    s.box(210, 120, 130, 80, "Chunks", "1100 chars · overlap", C.cream, { size: 22 });
    s.box(390, 30, 170, 80, "Dense", ["FAISS", "nomic-embed-text"], C.blue, { size: 22 });
    s.box(390, 210, 170, 80, "Sparse", ["BM25", "exact terms, numbers"], C.green, { size: 22 });
    s.box(610, 120, 140, 80, "RRF fusion", "1 / (60 + rank)", C.yellow, { size: 22 });
    s.box(790, 120, 140, 80, "FlashRank", "cross-encoder", C.pink, { size: 22 });
    s.box(965, 120, 120, 80, "Compress", "top sentences", C.violet, { size: 22 });
    s.arrow([[170, 160], [210, 160]]);
    s.arrow([[340, 150], [390, 75]]); s.arrow([[340, 170], [390, 250]]);
    s.arrow([[560, 70], [610, 150]]); s.arrow([[560, 250], [610, 175]]);
    s.arrow([[750, 160], [790, 160]]); s.arrow([[930, 160], [965, 160]]);
    s.text(1025, 240, "→ extraction agent", { size: 20, weight: 500 });
    s.text(1025, 262, "with chunk ids", { size: 18, weight: 500, color: MUTED });
    s.text(560, 318, "every quote is then checked against the source text (RapidFuzz): no verification, no ✓", { size: 20, weight: 500, color: MUTED });
  }

  let drawn = false;
  window.drawDiagrams = async function () {
    if (drawn) return;
    drawn = true;
    await document.fonts.load("700 22px Caveat");
    await document.fonts.load("500 18px Caveat");
    if (document.getElementById("d-system")) system();
    if (document.getElementById("d-graph")) graph();
    if (document.getElementById("d-rag")) rag();
  };
})();
