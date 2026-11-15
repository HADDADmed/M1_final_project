"""Pydantic schemas: every agent answers through one of these (JSON-schema constrained decoding)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------- protocol (Planner)
class Criterion(BaseModel):
    id: str = Field(description="I1, I2... for inclusion, E1, E2... for exclusion")
    text: str


class Protocol(BaseModel):
    title: str = Field(description="title of the systematic review")
    objective: str
    framework: Literal["PICO", "PCC", "SPIDER"] = Field(description="PICO for interventions, PCC for scoping-type, SPIDER for qualitative")
    population: str
    intervention_or_concept: str
    comparator: str = Field(description="comparator, or 'not applicable'")
    outcomes: list[str]
    context: str = Field(description="setting / context, or 'any'")
    inclusion: list[Criterion]
    exclusion: list[Criterion]
    study_designs: list[str]
    languages: list[str]
    rob_domains: list[str] = Field(description="4-6 risk-of-bias domains suited to the study designs of this field")
    synthesis_plan: str


# ---------------------------------------------------------------- search strategy
class ConceptBlock(BaseModel):
    concept: str
    terms: list[str] = Field(description="3-6 synonyms / spelling variants, short phrases")
    mesh: list[str] = Field(default_factory=list, description="MeSH headings if any apply, else empty")


class SearchStrategy(BaseModel):
    concepts: list[ConceptBlock] = Field(description="2-3 concept blocks combined with AND")
    keyword_queries: list[str] = Field(description="3 short natural-language queries (4-8 words) for relevance-ranked engines")
    rationale: str


# ---------------------------------------------------------------- screening
class CriterionCheck(BaseModel):
    id: str
    met: Literal["yes", "no", "unclear"]


class ScreeningDecision(BaseModel):
    checks: list[CriterionCheck] = Field(description="one check per inclusion and exclusion criterion")
    decision: Literal["include", "exclude"]
    reason: str = Field(description="one sentence citing the decisive criterion ids")
    confidence: float = Field(ge=0, le=1)


class Adjudication(BaseModel):
    decision: Literal["include", "exclude"]
    reason: str


class FullTextDecision(BaseModel):
    decision: Literal["include", "exclude"]
    exclusion_code: str = Field(description="criterion id that excludes the study (e.g. E2), or '' if included")
    reason: str
    evidence_chunks: list[str] = Field(description="chunk ids supporting the decision")


# ---------------------------------------------------------------- extraction & appraisal
class Evidence(BaseModel):
    value: str = Field(description="the extracted information in your own words, max 25 words (never a chunk id)")
    quote: str = Field(description="ONE verbatim sentence or clause (max 40 words) from a passage that supports the value, or ''")
    chunk: str = Field(description="chunk id of the quote, e.g. S012#ft3, or ''")


class Result(BaseModel):
    outcome: str
    metric: str = Field(description="e.g. accuracy, recall, sensitivity, F1, odds ratio, kappa, time saved")
    value: str = Field(description="the number as reported, with unit or % (e.g. '97.7%'), never a chunk id")
    comparator: str = Field(description="what it is compared with, or ''")
    quote: str
    chunk: str


class Extraction(BaseModel):
    study_design: Evidence
    population_or_dataset: Evidence
    intervention_or_method: Evidence
    comparator: Evidence
    outcomes: list[str]
    results: list[Result] = Field(description="up to 4 key quantitative results")
    main_finding: Evidence
    limitations: Evidence


class DomainJudgement(BaseModel):
    domain: str
    judgement: Literal["low", "some concerns", "high"]
    justification: str


class RiskOfBias(BaseModel):
    domains: list[DomainJudgement]
    overall: Literal["low", "some concerns", "high"]


# ---------------------------------------------------------------- synthesis & QA
class Theme(BaseModel):
    theme: str
    summary: str = Field(description="3-5 sentences; cite studies as [S001]")
    studies: list[str]
    certainty: Literal["high", "moderate", "low", "very low"]
    certainty_reason: str


class Synthesis(BaseModel):
    themes: list[Theme] = Field(description="3-5 themes")
    overall_answer: str = Field(description="direct answer to the review question in 4-6 sentences: what the evidence shows, "
                                            "with the key numbers and study ids [S001]; do not discuss meta-analysis here")
    research_gaps: list[str]
    meta_analysis_feasible: bool
    meta_analysis_note: str


class Issue(BaseModel):
    severity: Literal["minor", "major"]
    section: str
    problem: str
    fix: str


class Critique(BaseModel):
    issues: list[Issue]
    approve: bool = Field(description="true if there is no major issue")
