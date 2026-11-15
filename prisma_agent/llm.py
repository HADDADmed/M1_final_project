"""Local LLMs (Ollama) with structured output, retries and tracing."""
from __future__ import annotations

from langchain_ollama import ChatOllama
from pydantic import BaseModel

from .config import ReviewConfig
from .trace import LLMTracer, Trace


def make_llm(cfg: ReviewConfig, role: str = "worker", seed_offset: int = 0) -> ChatOllama:
    if role == "judge":
        return ChatOllama(model=cfg.judge_model, temperature=0, seed=cfg.seed + seed_offset, num_ctx=16384)
    return ChatOllama(model=cfg.worker_model, temperature=cfg.temperature, seed=cfg.seed + seed_offset,
                      reasoning=False, num_ctx=16384)


async def structured(llm: ChatOllama, schema: type[BaseModel], prompt: str, trace: Trace, agent: str,
                     retries: int = 2) -> BaseModel:
    """JSON-schema constrained call. Retries with the validation error appended (self-correction)."""
    runnable = llm.with_structured_output(schema, method="json_schema")
    last = None
    for _ in range(retries + 1):
        try:
            out = await runnable.ainvoke(prompt if last is None else
                                         f"{prompt}\n\nYour previous answer was invalid ({last}). Answer again with valid JSON.",
                                         config={"callbacks": [LLMTracer(trace, agent)]})
            if out is not None:
                return out
            last = "empty output"
        except Exception as e:  # validation or transport error
            last = str(e)[:200]
    trace.log("error", agent, message=f"structured output failed: {last}")
    raise RuntimeError(f"{agent}: structured output failed: {last}")
