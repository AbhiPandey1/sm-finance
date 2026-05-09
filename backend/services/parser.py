"""Strip HTML and approximate 10-K sections from local filings."""

from __future__ import annotations

import re
from typing import Literal

from bs4 import BeautifulSoup

SectionKey = Literal["business", "risk_factors", "md_and_a", "financial_statements"]

SECTION_PATTERNS: list[tuple[SectionKey, re.Pattern[str]]] = [
    ("business", re.compile(r"\bITEM\s*1\.?\s+BUSINESS\b", re.I)),
    ("risk_factors", re.compile(r"\bITEM\s*1A\.?\s+RISK\s+FACTORS\b", re.I)),
    ("md_and_a", re.compile(r"\bITEM\s*7\.?\s+MANAGEMENT'?S\s+DISCUSSION\b", re.I)),
    (
        "financial_statements",
        re.compile(
            r"\bITEM\s*8\.?\s+FINANCIAL\s+STATEMENTS\b",
            re.I,
        ),
    ),
]


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines)


def extract_sections(text: str) -> dict[SectionKey, str]:
    """Split full text by ITEM headers; tolerant of noisy SEC HTML."""
    upper_blocks: list[tuple[int, SectionKey | None]] = []
    for m in re.finditer(r"^ITEM\s*\d+[A-Z]?\..*$", text, re.I | re.MULTILINE):
        line = m.group(0)
        key: SectionKey | None = None
        for sk, pat in SECTION_PATTERNS:
            if pat.search(line):
                key = sk
                break
        if key:
            upper_blocks.append((m.start(), key))

    if not upper_blocks:
        return _fallback_whole_document(text)

    upper_blocks.sort(key=lambda x: x[0])
    out: dict[SectionKey, str] = {k: "" for k, t in SECTION_PATTERNS}
    for i, (start, key) in enumerate(upper_blocks):
        end = upper_blocks[i + 1][0] if i + 1 < len(upper_blocks) else len(text)
        chunk = text[start:end].strip()
        if key:
            out[key] = chunk
    return out


def _fallback_whole_document(text: str) -> dict[SectionKey, str]:
    """If headers not found, keep text in business and risk as weak demo fallback."""
    return {
        "business": text[: max(1, len(text) // 4)],
        "risk_factors": text[len(text) // 4 : len(text) // 2],
        "md_and_a": text[len(text) // 2 : 3 * len(text) // 4],
        "financial_statements": text[3 * len(text) // 4 :],
    }


def parse_filing_html(html: str) -> dict[SectionKey, str]:
    text = html_to_text(html)
    return extract_sections(text)
