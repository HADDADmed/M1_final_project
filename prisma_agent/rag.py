"""Hybrid RAG over the review corpus.

dense (FAISS inner product on normalised nomic-embed-text vectors) ┐
                                  ├─ Reciprocal Rank Fusion ─ FlashRank cross-encoder re-ranking ─ sentence-level compression
sparse (Okapi BM25, rank_bm25)                                      ┘
Every passage keeps its study id and chunk id, so generated claims can be traced back to the source text.
"""
from __future__ import annotations

import os
import re

import faiss
import numpy as np
from langchain_core.documents import Document
from langchain_ollama import OllamaEmbeddings
from rank_bm25 import BM25Okapi

from .config import CACHE_DIR

_ranker = None


def ranker():
    global _ranker
    if _ranker is None:
        from flashrank import Ranker
        _ranker = Ranker(model_name="ms-marco-MiniLM-L-12-v2", cache_dir=os.path.join(CACHE_DIR, "flashrank"))
    return _ranker


def chunk(text: str, size: int = 1100, overlap: int = 200) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= size:
        return [text] if text else []
    out, i = [], 0
    while i < len(text):
        end = min(len(text), i + size)
        cut = text.rfind(". ", i + size // 2, end)          # prefer to cut at a sentence boundary
        end = cut + 1 if cut > 0 and end < len(text) else end
        out.append(text[i:end].strip())
        if end >= len(text):
            break
        i = max(end - overlap, i + 1)
    return out


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+(?:[.-][a-z0-9]+)*", text.lower())


class HybridIndex:
    def __init__(self, embed_model: str = "nomic-embed-text"):
        self.emb = OllamaEmbeddings(model=embed_model)
        self.docs: list[Document] = []

    def add_study(self, study_id: str, text: str, kind: str):
        for j, c in enumerate(chunk(text)):
            self.docs.append(Document(page_content=c, metadata={"study": study_id, "chunk": f"{study_id}#{kind}{j}", "kind": kind}))

    def build(self):
        vecs = np.array(self.emb.embed_documents([f"search_document: {d.page_content}" for d in self.docs]), dtype="float32")
        faiss.normalize_L2(vecs)
        self.faiss = faiss.IndexFlatIP(vecs.shape[1])          # cosine similarity
        self.faiss.add(vecs)
        self.bm25 = BM25Okapi([tokens(d.page_content) for d in self.docs])
        self.by_study: dict[str, list[int]] = {}
        for i, d in enumerate(self.docs):
            self.by_study.setdefault(d.metadata["study"], []).append(i)
        return self

    def search(self, query: str, k: int = 6, study: str | None = None, fetch: int = 30, compress_ctx: bool = True) -> list[dict]:
        allowed = set(self.by_study.get(study, [])) if study else None
        qv = np.array([self.emb.embed_query(f"search_query: {query}")], dtype="float32")
        faiss.normalize_L2(qv)
        _, ids = self.faiss.search(qv, len(self.docs) if study else min(fetch * 4, len(self.docs)))
        dense = [i for i in ids[0] if i >= 0 and (allowed is None or i in allowed)][:fetch]
        bm = self.bm25.get_scores(tokens(query))
        cand = allowed if allowed is not None else range(len(self.docs))
        sparse = sorted((i for i in cand if bm[i] > 0), key=lambda i: -bm[i])[:fetch]
        # Reciprocal Rank Fusion
        scores: dict[int, float] = {}
        for ranking in (dense, sparse):
            for rank, i in enumerate(ranking):
                scores[i] = scores.get(i, 0) + 1 / (60 + rank)
        fused = sorted(scores, key=lambda i: -scores[i])[:20]
        if not fused:
            return []
        # Cross-encoder re-ranking
        from flashrank import RerankRequest
        reranked = ranker().rerank(RerankRequest(query=query, passages=[
            {"id": i, "text": self.docs[i].page_content} for i in fused]))[:k]
        return [{"chunk": self.docs[r["id"]].metadata["chunk"], "study": self.docs[r["id"]].metadata["study"],
                 "score": round(float(r["score"]), 3), "text": compress(query, r["text"]) if compress_ctx else r["text"]} for r in reranked]


def compress(query: str, text: str, max_sentences: int = 4) -> str:
    """Context compression: keep the sentences sharing most terms with the query, in original order."""
    sents = re.split(r"(?<=[.!?])\s+", text)
    if len(sents) <= max_sentences:
        return text
    q = set(re.findall(r"[a-z0-9]{3,}", query.lower()))
    scored = sorted(range(len(sents)), key=lambda i: -len(q & set(re.findall(r"[a-z0-9]{3,}", sents[i].lower()))))
    keep = sorted(scored[:max_sentences])
    return " … ".join(sents[i] for i in keep)
