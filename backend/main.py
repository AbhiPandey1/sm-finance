"""Filings Memory Agent — FastAPI backend."""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from services.chunker import chunk_sections
from services import chat_session
from services import hydradb_client
from services.filing_loader import list_cached_filings, read_filing_html
from services import pipeshift_client
from services.parser import parse_filing_html
from services.sec_facts import fetch_company_metrics

app = FastAPI(title="Filings Memory Agent", version="0.1.0")


@app.on_event("startup")
def _configure_hydradb_logging() -> None:
    """HydraDB client logs to services.hydradb_client — set LOG_LEVEL=DEBUG for recall details."""
    level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    logging.getLogger("services.hydradb_client").setLevel(level)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AskBody(BaseModel):
    ticker: str = Field(..., min_length=1)
    question: str = Field(..., min_length=1)
    conversation_id: Optional[str] = Field(
        None,
        description="Client-generated UUID; reuse for follow-up questions in the same thread",
    )


class ChatResetBody(BaseModel):
    conversation_id: str = Field(..., min_length=4)


def _chat_search_query(question: str) -> str:
    """Boost lexical/Hydra retrieval for common numeric filing questions."""
    q = question.strip()
    ql = q.lower()
    if any(
        k in ql
        for k in (
            "cash flow",
            "financing activities",
            "financing cash",
            "operating activities",
            "investing activities",
            "free cash flow",
            "fcf",
            "statement of cash flows",
        )
    ):
        return (
            f"{q} consolidated statements of cash flows "
            "financing operating investing activities"
        )
    if any(
        k in ql
        for k in (
            "revenue",
            "y/y",
            "year over year",
            "year-over-year",
            "net sales",
            "sales growth",
            "top line",
        )
    ):
        return f"{q} net sales total revenue consolidated statements of operations"
    return q


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "filings-memory-agent"}


@app.get("/api/filings/{ticker}")
def filings_index(ticker: str) -> dict[str, Any]:
    """List locally cached raw filing files for a ticker (years discovered from filenames)."""
    t = ticker.upper()
    refs = list_cached_filings(t)
    years = sorted({r.year for r in refs}, reverse=True)
    return {
        "ticker": t,
        "years": years,
        "files": [r.path.name for r in refs],
        "count": len(refs),
    }


@app.post("/api/ingest")
def ingest(ticker: str = "AAPL") -> dict[str, Any]:
    """Chunk and upsert filings for ``ticker`` (GET query param). Defaults to AAPL."""
    t = ticker.upper()
    all_chunks: list[dict[str, Any]] = []
    refs = list_cached_filings(t)
    if not refs:
        raise HTTPException(
            status_code=404,
            detail=f"No cached {t} filings in data/raw — use TICKER_YEAR_10K.html or {t.lower()}-YYYYMMDD.html",
        )

    for ref in refs:
        html = read_filing_html(ref)
        sections = parse_filing_html(html)
        chunks = chunk_sections(
            company=ref.company,
            ticker=ref.ticker,
            year=ref.year,
            filing_type=ref.filing_type,
            sections=sections,
        )
        all_chunks.extend(chunks)

    store = hydradb_client.upsert_chunks(all_chunks)
    mem = hydradb_client.get_company_memory(t)
    return {
        "ticker": t,
        "ingested_files": len(refs),
        "chunks": len(all_chunks),
        "storage": store,
        "memory": mem,
    }


@app.get("/api/metrics/{ticker}")
def metrics(ticker: str) -> dict[str, Any]:
    """Annual financial metrics from SEC XBRL company facts (data.sec.gov)."""
    return fetch_company_metrics(ticker, max_years=5)


@app.get("/api/analysis/{ticker}")
def analysis(ticker: str) -> dict[str, Any]:
    t = ticker.upper()
    chunks = hydradb_client.get_chunks_for_ticker(t)
    if not chunks:
        raise HTTPException(
            status_code=400,
            detail=f"No chunks for {t}. Run POST /api/ingest first.",
        )

    queries = [
        "business strategy data center AI revenue",
        "risk factors export controls supply chain customer concentration",
        "management discussion gross margin inventory competition",
        "financial statements segment revenue",
    ]
    seen_ids: set[str] = set()
    retrieved: list[dict[str, Any]] = []
    for q in queries:
        for c in hydradb_client.search_context(q, t, top_k=4):
            cid = str(c.get("chunk_id", ""))
            if cid in seen_ids:
                continue
            seen_ids.add(cid)
            retrieved.append({k: v for k, v in c.items() if not str(k).startswith("_")})
    if len(retrieved) < 10:
        for c in chunks:
            cid = str(c.get("chunk_id", ""))
            if cid in seen_ids:
                continue
            seen_ids.add(cid)
            retrieved.append(c)
            if len(retrieved) >= 20:
                break

    ctx = retrieved[:20] if retrieved else chunks[:20]

    what_changed = pipeshift_client.analyze_what_changed(ctx)
    risk_evolution = pipeshift_client.analyze_risk_evolution(ctx)
    tone_shift = pipeshift_client.analyze_tone_shift(ctx)
    investor_memo = pipeshift_client.generate_investor_memo(ctx)
    evidence = pipeshift_client.evidence_snippets(ctx, limit=8)

    return {
        "ticker": t,
        "what_changed": what_changed,
        "risk_evolution": risk_evolution,
        "tone_shift": tone_shift,
        "investor_memo": investor_memo,
        "evidence_snippets": evidence,
        "context_backend": hydradb_client.get_company_memory(t),
    }


@app.post("/api/ask")
def ask(body: AskBody) -> dict[str, Any]:
    t = body.ticker.upper()
    chunks = hydradb_client.get_chunks_for_ticker(t)
    if not chunks:
        raise HTTPException(status_code=400, detail=f"No chunks for {t}. Ingest first.")

    conv = (body.conversation_id or "").strip() or str(uuid.uuid4())
    raw_prior = chat_session.get_messages(conv)
    prior = [
        {"role": str(m.get("role", "")), "content": str(m.get("content", ""))}
        for m in raw_prior
        if m.get("role") and m.get("content") is not None
    ]

    hydra_chat_brief: str | None = None
    mem_info = hydradb_client.get_company_memory(t)
    if mem_info.get("hydradb_ready_for_remote"):
        hydra_chat_brief = hydradb_client.recall_chat_memories_brief(conv, body.question, top_k=4)

    search_q = _chat_search_query(body.question)
    retrieved = hydradb_client.search_context(search_q, t, top_k=12)
    ctx = retrieved if retrieved else chunks[:12]
    answer = pipeshift_client.answer_with_chat(
        body.question,
        ctx,
        prior,
        hydra_memory_brief=hydra_chat_brief,
    )
    evidence = pipeshift_client.evidence_snippets(ctx, limit=6)

    chat_session.append_message(conv, "user", body.question, ticker=t)
    chat_session.append_message(conv, "assistant", answer, ticker=t, evidence=evidence)
    if mem_info.get("hydradb_ready_for_remote"):
        ok, err = hydradb_client.append_chat_turn_hydradb(
            conv, t, body.question, answer
        )
        if not ok and err:
            logging.getLogger(__name__).debug("HydraDB chat memory append: %s", err)

    return {
        "answer": answer,
        "evidence": evidence,
        "conversation_id": conv,
        "chat_turns": len(chat_session.get_messages(conv)),
        "hydradb_chat_memory": bool(hydra_chat_brief),
    }


@app.get("/api/chat/threads/{ticker}")
def list_chat_threads(ticker: str) -> dict[str, Any]:
    """All persisted chat sessions on disk for this ticker (for thread picker)."""
    rows = chat_session.list_threads_for_ticker(ticker.upper())
    return {"ticker": ticker.upper(), "threads": rows}


@app.get("/api/chat/{conversation_id}")
def get_chat_history(conversation_id: str, ticker: str = "AAPL") -> dict[str, Any]:
    """Restore persisted chat for this conversation + ticker."""
    cid = conversation_id.strip()
    data = chat_session.get_session(cid)
    stored_ticker = (data.get("ticker") or "").upper()
    if stored_ticker and stored_ticker != ticker.upper():
        raise HTTPException(
            status_code=404,
            detail="This conversation belongs to a different ticker.",
        )
    return {
        "conversation_id": cid,
        "ticker": ticker.upper(),
        "messages": data.get("messages", []),
    }


@app.post("/api/chat/reset")
def chat_reset(body: ChatResetBody) -> dict[str, str]:
    chat_session.clear_session(body.conversation_id.strip())
    return {"status": "ok", "conversation_id": body.conversation_id.strip()}
