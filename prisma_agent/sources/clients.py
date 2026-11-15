"""Academic search clients: OpenAlex, PubMed (E-utilities), arXiv and Semantic Scholar."""
from __future__ import annotations

import xml.etree.ElementTree as ET

from .base import CachedClient, Record, clean

_clients: dict[str, CachedClient] = {}


def client(name: str, interval: float) -> CachedClient:
    if name not in _clients:
        _clients[name] = CachedClient(name, interval)
    return _clients[name]


# ----------------------------------------------------------------------------- OpenAlex
def _openalex_abstract(inv: dict | None) -> str:
    if not inv:
        return ""
    pos = sorted((p, w) for w, ps in inv.items() for p in ps)
    return " ".join(w for _, w in pos)


async def search_openalex(query: str, n: int, year_from: int) -> list[Record]:
    data = await client("openalex", 1.0).get("https://api.openalex.org/works", {
        "search": query, "per-page": n, "filter": f"from_publication_date:{year_from}-01-01,type:article|preprint|review",
        "select": "id,doi,title,publication_year,authorships,abstract_inverted_index,primary_location,open_access"})
    out = []
    for w in data.get("results", []):
        loc = w.get("primary_location") or {}
        out.append(Record(
            source="openalex", source_id=w["id"].rsplit("/", 1)[-1], title=clean(w.get("title")),
            abstract=_openalex_abstract(w.get("abstract_inverted_index")),
            authors=[a["author"]["display_name"] for a in w.get("authorships", [])[:12] if a.get("author")],
            year=w.get("publication_year"), venue=((loc.get("source") or {}).get("display_name") or ""),
            doi=(w.get("doi") or "").replace("https://doi.org/", "").lower(),
            url=w.get("doi") or w["id"], pdf_url=(w.get("open_access") or {}).get("oa_url") or "", query=query))
    return out


# ----------------------------------------------------------------------------- PubMed
async def search_pubmed(query: str, n: int, year_from: int) -> list[Record]:
    c = client("pubmed", 0.4)
    ids = (await c.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi", {
        "db": "pubmed", "term": query, "retmode": "json", "retmax": n, "mindate": year_from, "maxdate": 3000,
        "datetype": "pdat", "sort": "relevance"}))["esearchresult"]["idlist"]
    if not ids:
        return []
    xml = await c.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
                      {"db": "pubmed", "id": ",".join(ids), "retmode": "xml"}, as_json=False)
    out = []
    for art in ET.fromstring(xml).iter("PubmedArticle"):
        pmid = art.findtext(".//PMID", "")
        a = art.find(".//Article")
        if a is None:
            continue
        abstract = " ".join("".join(t.itertext()) for t in a.findall(".//AbstractText"))
        authors = [f"{x.findtext('ForeName', '')} {x.findtext('LastName', '')}".strip()
                   for x in a.findall(".//Author") if x.findtext("LastName")]
        year = art.findtext(".//PubDate/Year") or (art.findtext(".//PubDate/MedlineDate") or "")[:4]
        doi = next((e.text for e in art.findall(".//ArticleId") if e.get("IdType") == "doi" and e.text), "")
        out.append(Record(source="pubmed", source_id=pmid, title=clean("".join(a.find("ArticleTitle").itertext())),
                          abstract=clean(abstract), authors=authors[:12], year=int(year) if year.isdigit() else None,
                          venue=a.findtext(".//Journal/Title", ""), doi=doi.lower(),
                          url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/", query=query))
    return out


# ----------------------------------------------------------------------------- arXiv
ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


async def search_arxiv(query: str, n: int, year_from: int) -> list[Record]:
    xml = await client("arxiv", 3.0).get("https://export.arxiv.org/api/query", {
        "search_query": query, "max_results": n, "sortBy": "relevance"}, as_json=False)
    out = []
    for e in ET.fromstring(xml).findall("a:entry", ATOM):
        year = int(e.findtext("a:published", "0000", ATOM)[:4])
        if year < year_from:
            continue
        aid = e.findtext("a:id", "", ATOM).rsplit("/abs/", 1)[-1]
        pdf = next((l.get("href") for l in e.findall("a:link", ATOM) if l.get("title") == "pdf"), "")
        out.append(Record(source="arxiv", source_id=aid, title=clean(e.findtext("a:title", "", ATOM)),
                          abstract=clean(e.findtext("a:summary", "", ATOM)),
                          authors=[x.findtext("a:name", "", ATOM) for x in e.findall("a:author", ATOM)][:12],
                          year=year, venue="arXiv", doi=(e.findtext("arxiv:doi", "", ATOM) or "").lower(),
                          url=f"https://arxiv.org/abs/{aid}", pdf_url=pdf, query=query))
    return out


# ----------------------------------------------------------------------------- Semantic Scholar
async def search_semanticscholar(query: str, n: int, year_from: int) -> list[Record]:
    data = await client("semanticscholar", 1.2).get("https://api.semanticscholar.org/graph/v1/paper/search", {
        "query": query, "limit": n, "year": f"{year_from}-",
        "fields": "title,abstract,year,authors,venue,externalIds,openAccessPdf,url"}, retries=3)
    out = []
    for p in data.get("data", []):
        ext = p.get("externalIds") or {}
        out.append(Record(source="semanticscholar", source_id=p["paperId"], title=clean(p.get("title")),
                          abstract=clean(p.get("abstract")), authors=[a["name"] for a in p.get("authors", [])][:12],
                          year=p.get("year"), venue=p.get("venue") or "", doi=(ext.get("DOI") or "").lower(),
                          url=p.get("url") or "", pdf_url=(p.get("openAccessPdf") or {}).get("url") or "", query=query))
    return out


SEARCHERS = {"openalex": search_openalex, "pubmed": search_pubmed, "arxiv": search_arxiv,
             "semanticscholar": search_semanticscholar}
