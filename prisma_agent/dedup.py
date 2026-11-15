"""Deduplication across sources: exact DOI match, then fuzzy normalised-title match (RapidFuzz)."""
from __future__ import annotations

import re

from rapidfuzz import fuzz

from .sources.base import Record

SOURCE_PRIORITY = {"pubmed": 0, "openalex": 1, "semanticscholar": 2, "arxiv": 3}


def norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", t.lower()).strip()


def deduplicate(records: list[Record], threshold: int = 93) -> tuple[list[Record], list[dict]]:
    """Returns (unique records with ids S001.., list of merge decisions for the audit log)."""
    records = sorted(records, key=lambda r: (SOURCE_PRIORITY.get(r.source, 9), -len(r.abstract)))
    kept: list[Record] = []
    by_doi: dict[str, Record] = {}
    merges = []
    for r in records:
        if not r.title:
            continue
        dup, why = None, ""
        if r.doi and r.doi in by_doi:
            dup, why = by_doi[r.doi], "same DOI"
        else:
            nt = norm_title(r.title)
            for k in kept:
                if abs(len(nt) - len(norm_title(k.title))) < 25 and fuzz.ratio(nt, norm_title(k.title)) >= threshold:
                    dup, why = k, f"title similarity ≥ {threshold}"
                    break
        if dup:
            dup.also_in.append(r.source)
            if not dup.abstract and r.abstract:
                dup.abstract = r.abstract
            if not dup.pdf_url and r.pdf_url:
                dup.pdf_url = r.pdf_url
            if not dup.doi and r.doi:
                dup.doi = r.doi
            merges.append({"kept": dup.title[:80], "dropped_source": r.source, "reason": why})
            continue
        kept.append(r)
        if r.doi:
            by_doi[r.doi] = r
    kept.sort(key=lambda r: (-(r.year or 0), r.title))
    for i, r in enumerate(kept, 1):
        r.id = f"S{i:03d}"
    return kept, merges
