"""Fast unit tests (no LLM, no network)."""
from prisma_agent.agents import cohen_kappa, compile_queries, verify_quote
from prisma_agent.dedup import deduplicate
from prisma_agent.graph import build_graph
from prisma_agent.rag import chunk, compress
from prisma_agent.report import PRISMA_ITEMS, bibtex, prisma_counts
from prisma_agent.schemas import ConceptBlock, SearchStrategy
from prisma_agent.sources.base import Record
from prisma_agent.sources.clients import _openalex_abstract


def rec(**kw):
    base = dict(source="openalex", source_id="x", title="Large language models for screening", abstract="")
    return Record(**{**base, **kw})


def test_dedup_by_doi_and_fuzzy_title():
    recs = [rec(doi="10.1/abc", source="pubmed"), rec(doi="10.1/abc", source="openalex", abstract="longer"),
            rec(title="Large Language Models for Screening.", source="arxiv"), rec(title="Something else entirely")]
    unique, merges = deduplicate(recs)
    assert len(unique) == 2 and len(merges) == 2
    kept = next(r for r in unique if r.doi == "10.1/abc")
    assert kept.source == "pubmed" and set(kept.also_in) == {"openalex", "arxiv"} and kept.abstract == "longer"
    assert [r.id for r in unique] == ["S001", "S002"]


def test_cohen_kappa():
    assert cohen_kappa(["include", "exclude"] * 5, ["include", "exclude"] * 5) == 1.0
    assert cohen_kappa(["include", "include", "exclude", "exclude"], ["include", "exclude", "include", "exclude"]) == 0.0


def test_query_compiler():
    s = SearchStrategy(concepts=[ConceptBlock(concept="LLM", terms=["large language model", "GPT"], mesh=["Natural Language Processing"]),
                                 ConceptBlock(concept="screening", terms=["abstract screening"])],
                       keyword_queries=["llm screening systematic review"], rationale="")
    q = compile_queries(s)
    assert '"large language model"[tiab]' in q["pubmed"][0] and '"Natural Language Processing"[MeSH Terms]' in q["pubmed"][0]
    assert ' AND ' in q["pubmed"][0] and 'all:"large language model"' in q["arxiv"][0] and "all:GPT" in q["arxiv"][0]


def test_quote_verification():
    src = "Methods. " + "filler text " * 3000 + "GPT-4 reached a sensitivity of 0.96 (95% CI 0.93-0.98) on 2,000 abstracts."
    assert verify_quote("GPT-4 reached a sensitivity of 0.96 (95% CI 0.93–0.98)", src)
    assert not verify_quote("GPT-4 reached a sensitivity of 0.50 on ten abstracts only", src)
    assert not verify_quote("", src)


def test_openalex_abstract_reconstruction():
    assert _openalex_abstract({"Hello": [0], "world": [1], "again": [3], "big": [2]}) == "Hello world big again"


def test_chunk_and_compress():
    text = " ".join(f"Sentence number {i} talks about recall." for i in range(200))
    parts = chunk(text, 500, 100)
    assert all(len(p) <= 520 for p in parts) and len(parts) > 10
    out = compress("recall of models", "Cats sleep. Recall of models was high. Dogs bark. The sky is blue. Grass is green.", 1)
    assert out == "Recall of models was high."


def test_graph_structure():
    g = build_graph().get_graph()
    nodes = set(g.nodes)
    assert {"planner", "protocol_review", "search_strategist", "retrieval", "screening", "conflict_resolution",
            "fulltext", "extraction", "synthesis", "critic", "report"} <= nodes
    edges = {(e.source, e.target) for e in g.edges}
    assert ("critic", "synthesis") in edges and ("retrieval", "broaden") in edges and ("broaden", "search_strategist") in edges


def test_prisma_counts_and_bibtex():
    st = {"identified": {"pubmed": 10, "arxiv": 5}, "duplicates": 3, "screening": [{}] * 12, "ta_included": ["S1", "S2", "S3"],
          "ft_sought": ["S1", "S2"], "fulltext": [{"decision": "include", "exclusion_code": "", "availability": "full text"},
                                                  {"decision": "exclude", "exclusion_code": "E2", "availability": "abstract only"}],
          "included": ["S1"]}
    c = prisma_counts(st)
    assert (c["identified_total"], c["screened"], c["excluded_ta"], c["not_retrieved"], c["included"]) == (15, 12, 9, 1, 1)
    assert c["reasons"] == {"E2": 1} and c["abstract_only"] == 1
    b = bibtex({"authors": ["Ada Lovelace"], "year": 2024, "title": "Notes on engines", "venue": "J", "doi": "10.1/x", "url": ""})
    assert b.startswith("@article{lovelace2024notes") and "doi = {10.1/x}" in b
    assert len(PRISMA_ITEMS) == 27
