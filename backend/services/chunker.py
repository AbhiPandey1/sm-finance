"""Chunk section text with stable metadata."""

from __future__ import annotations

import hashlib
from typing import Any

from .parser import SectionKey

Chunk = dict[str, Any]


def _chunk_id(ticker: str, year: int, section: str, index: int) -> str:
    raw = f"{ticker}|{year}|{section}|{index}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def chunk_sections(
    *,
    company: str,
    ticker: str,
    year: int,
    filing_type: str,
    sections: dict[SectionKey, str],
    max_chars: int = 1600,
    overlap: int = 200,
) -> list[Chunk]:
    chunks: list[Chunk] = []
    for section_key, body in sections.items():
        body = (body or "").strip()
        if not body:
            continue
        start = 0
        idx = 0
        while start < len(body):
            end = min(len(body), start + max_chars)
            piece = body[start:end].strip()
            if piece:
                cid = _chunk_id(ticker, year, section_key, idx)
                chunks.append(
                    {
                        "company": company,
                        "ticker": ticker.upper(),
                        "year": year,
                        "filing_type": filing_type,
                        "section": section_key,
                        "chunk_id": cid,
                        "text": piece,
                    }
                )
                idx += 1
            if end >= len(body):
                break
            start = max(0, end - overlap)
    return chunks
