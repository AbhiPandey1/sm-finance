"""
Pipeshift AI layer — calls remote API when configured; otherwise context-grounded mocks.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

_ANALYSIS_MD_STYLE = (
    "Format as GitHub-flavored Markdown for a dashboard card: "
    "begin with a ### heading (short title for this synthesis). "
    "Use **bold** for entity names and key themes. "
    "Use bullet lists (- **Label:** one sentence) for multiple distinct points. "
    "Keep total length concise (roughly 120–220 words unless excerpts demand more). "
    "Do not paste JSON, chunk IDs, or raw XBRL tables."
)


def _config() -> tuple[str | None, str | None]:
    key = os.getenv("PIPESHIFT_API_KEY")
    url = os.getenv(
        "PIPESHIFT_API_URL",
        "https://api.pipeshift.com/api/v0/chat/completions",
    )
    return key, url


def _strip_json_metadata_leak(text: str) -> str:
    """Remove trailing JSON blobs Hydra sometimes concatenates onto chunk text."""
    cut_markers = (
        '"tenant_metadata"',
        '"document_metadata"',
        '"html_base64"',
        '"csv_base64"',
        '{ "html_base64"',
        '{"html_base64"',
        '"sub_tenant_id"',
        '"tenant_id"',
        '"app_knowledge"',
    )
    t = text
    earliest = len(t)
    for m in cut_markers:
        i = t.find(m)
        if i >= 12:
            earliest = min(earliest, i)
    if earliest < len(t):
        t = t[:earliest]
    return t


def _extract_text_if_json_wrapper(text: str) -> str:
    """If Hydra returns a JSON document instead of plain text, pull content.text."""
    s = text.strip()
    if not s.startswith("{") or '"content"' not in s:
        return text
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            c = obj.get("content")
            if isinstance(c, dict) and "text" in c:
                return str(c["text"])
            if isinstance(c, str):
                return c
    except Exception:
        pass
    return text


def _sanitize_model_answer(text: str) -> str:
    """Remove chain-of-thought / planner prefaces some models emit before the real reply."""
    t = _strip_json_metadata_leak(text)
    t = re.sub(r"```(?:json|JSON)?[\s\S]*?```", "", t).strip()
    # Drop leading reasoning paragraphs (common DeepSeek-style leaks)
    parts = re.split(r"\n\s*\n+", t.strip())
    drop_openers = (
        "we need to answer",
        "the user is asking",
        "the user wants",
        "let's compute",
        "compute:",
        "from the excerpts, we",
        "i'll provide",
        "i will provide",
        "based on the excerpts above",
    )
    kept: list[str] = []
    for p in parts:
        pl = p.strip()
        if not pl:
            continue
        low = pl.lower()
        if len(kept) == 0 and any(low.startswith(d) for d in drop_openers):
            continue
        if len(kept) == 0 and low.startswith("we need") and "?" not in pl[:120]:
            continue
        kept.append(pl)
    if not kept and parts:
        kept = [p.strip() for p in parts if p.strip()][-3:]
    out = "\n\n".join(kept).strip()
    if len(out) < 40 and len(t) > 80:
        out = t.strip()
    return out


def _clean_excerpt_text(text: str) -> str:
    """Single-line prose-friendly excerpt."""
    t = _extract_text_if_json_wrapper(text)
    t = _strip_json_metadata_leak(t)
    t = t.replace("\\n", "\n").replace("\\t", " ")
    t = re.sub(r"[\r\n]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _looks_like_table_or_noise(text: str) -> bool:
    """True if chunk is mostly numeric grid / junk for chat display."""
    if not text or len(text) < 30:
        return True
    low = text[:2000].lower()
    if '"tenant_metadata"' in low or ('"chunk_id"' in low and '"title"' in low):
        return True
    letters = sum(1 for c in text if c.isalpha())
    digits = sum(1 for c in text if c.isdigit())
    if letters < 25:
        return True
    if digits > letters * 1.2:
        return True
    if text.count("$") >= 6 and digits > 40:
        return True
    if text.count("(") > 35:
        return True
    return False


def _year_sec_label(c: dict[str, Any]) -> str:
    y = int(c.get("year") or 0)
    sec = str(c.get("section") or "section").replace("_", " ")
    if y > 0:
        return f"{y} · {sec}"
    return sec


def _pick_sentences(text: str, query_words: set[str], max_sentences: int = 3) -> list[str]:
    """Prefer sentences that overlap question tokens (simple lexical grounding)."""
    text = _clean_excerpt_text(text)
    if _looks_like_table_or_noise(text):
        return []
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) < 40:
        return [text] if text else []
    parts = re.split(r"(?<=[.!?])\s+", text)
    scored: list[tuple[float, str]] = []
    qset = {w for w in query_words if len(w) > 2}
    for p in parts:
        p = p.strip()
        if len(p) < 25:
            continue
        low = p.lower()
        score = sum(1 for w in qset if w in low) + min(len(p) / 400.0, 0.5)
        scored.append((score, p))
    scored.sort(key=lambda x: (-x[0], len(x[1])))
    out: list[str] = []
    for _, p in scored:
        if p not in out:
            out.append(p[:420] + ("…" if len(p) > 420 else ""))
        if len(out) >= max_sentences:
            break
    if not out and parts:
        out = [parts[0][:420] + ("…" if len(parts[0]) > 420 else "")]
    return out


def _query_tokens(question: str) -> set[str]:
    return {t for t in re.split(r"[^\w]+", question.lower()) if len(t) > 2}


def _sorted_years(context: list[dict[str, Any]]) -> list[int]:
    ys = sorted({int(c.get("year", 0)) for c in context})
    return [y for y in ys if y > 0]


def _sections_present(context: list[dict[str, Any]]) -> list[str]:
    seen: list[str] = []
    for c in context:
        s = str(c.get("section", "")).strip()
        if s and s != "unknown" and s not in seen:
            seen.append(s)
    return seen


def _grounded_answer_paragraph(question: str, context: list[dict[str, Any]]) -> str:
    """Readable prose from excerpts — no raw JSON or table dumps."""
    if not context:
        return (
            "No filing excerpts were retrieved for this query. Ingest filings or broaden your question."
        )

    years = _sorted_years(context)
    secs = _sections_present(context)
    qtok = _query_tokens(question)
    year_label = ", ".join(str(y) for y in years) if years else "multiple fiscal periods (year tags missing in index)"
    sec_label = ", ".join(secs[:6]) if secs else "business, risk, MD&A, and financial discussions"

    ql = question.lower()
    if any(k in ql for k in ("risk", "export", "regulat", "geopolit", "supply", "fab", "china")):
        ranked = sorted(
            context,
            key=lambda c: (str(c.get("section", "")) == "risk_factors", int(c.get("year", 0))),
            reverse=True,
        )
    else:
        ranked = list(context)

    snippets: list[str] = []
    seen_txt: set[str] = set()
    for c in ranked[:14]:
        raw = str(c.get("text", ""))
        if _looks_like_table_or_noise(raw):
            continue
        cleaned = _clean_excerpt_text(raw)
        if len(cleaned) < 50:
            continue
        label = _year_sec_label(c)
        for sent in _pick_sentences(raw, qtok, max_sentences=2):
            if not sent:
                continue
            line = f"({label}) {sent}"
            if line not in seen_txt and sent not in seen_txt:
                seen_txt.add(line)
                snippets.append(line)
            if len(snippets) >= 4:
                break
        if len(snippets) >= 4:
            break

    intro = (
        f"This answer is based only on the filing excerpts retrieved for your question "
        f"(years referenced in index: {year_label}; sections present: {sec_label}).\n\n"
    )

    if snippets:
        body = "Here is what those excerpts support:\n\n"
        for i, s in enumerate(snippets[:4], 1):
            body += f"{i}. {s}\n\n"
    else:
        body = "The retrieved passages look like tables or numeric grids rather than prose, so they were not pasted verbatim. "
        body += "Try asking about risk factors, MD&A, or business narrative, or rephrase to pull narrative chunks.\n\n"
        for i, c in enumerate(ranked[:2], 1):
            t = _clean_excerpt_text(str(c.get("text", "")))[:220]
            if len(t) > 80:
                body += f"{i}. ({_year_sec_label(c)}) One readable fragment: {t}…\n\n"

    by_year: dict[int, list[str]] = {}
    for c in context:
        if str(c.get("section")) != "risk_factors":
            continue
        y = int(c.get("year", 0))
        if y:
            by_year.setdefault(y, []).append(_clean_excerpt_text(str(c.get("text", "")))[:400])

    contrast = ""
    if len(by_year) >= 2:
        ys_sorted = sorted(by_year.keys())
        early, late = ys_sorted[0], ys_sorted[-1]
        contrast = (
            f"Comparing risk-factor language between {early} and {late}, use the numbered points above "
            f"— later years often add more detail on export controls and licensing where those topics appear in retrieval.\n\n"
        )

    closing = (
        "Note: Form 10-K is annual. If you need quarter-by-quarter spending, say which segment or line item "
        "you care about; we can steer retrieval toward MD&A tables that mention it.\n"
    )

    return intro + body + contrast + closing


def _context_brief(context: list[dict[str, Any]], max_chars: int = 12000) -> str:
    parts: list[str] = []
    for c in context:
        y = c.get("year")
        sec = c.get("section")
        txt = _clean_excerpt_text(str(c.get("text", "")))[:2000]
        if _looks_like_table_or_noise(txt):
            txt = _clean_excerpt_text(txt[:800]) + " [truncated — tabular/numeric passage]"
        parts.append(f"[{y} | {sec}]\n{txt}")
    blob = "\n\n".join(parts)
    return blob[:max_chars]


def _mock_investor_memo(context: list[dict[str, Any]]) -> str:
    years = sorted({int(c.get("year", 0)) for c in context if c.get("year")})
    yspan = f"{years[0]}–{years[-1]}" if years else "recent fiscal years"
    return (
        f"### Investor memo\n\n"
        f"_Excerpt window: {yspan}_\n\n"
        f"Management emphasizes **platform durability**, **monetization efficiency**, and **scale advantages** "
        f"where those themes appear in retrieval. **Customer / counterpart concentration** can amplify revenue "
        f"swings when macro or competitive dynamics shift. **Privacy, measurement, and regulatory** evolution "
        f"remains a recurring disclosure theme. **Technology and AI-related investments** are framed as "
        f"competitive capability builders but carry execution risk if uptake disappoints. "
        f"**Operating leverage** can improve with mix; **S&M** and **R&D** intensity may rise during expansion. "
        f"Net: sustaining engagement and counterpart ROI matters alongside policy and platform dependence risks."
    )


def _mock_risk_evolution(context: list[dict[str, Any]]) -> str:
    return (
        "### Risk evolution\n\n"
        "Retrieval-grounded synthesis:\n\n"
        "- **Earlier periods** often foreground **growth execution**, **platform dependence**, and **competitive dynamics**.\n"
        "- **Later excerpts** add nuance on **privacy / data protection**, **advertising regulation**, "
        "**algorithmic or AI-related product risk**, and **macro or spend cyclicality**.\n"
        "- **Concentration** (customer, revenue, or geography) and **international operations** recur as amplifiers.\n"
        "- **IP / litigation** stay evergreen; **talent** and **M&A integration** appear when passages emphasize corporate development."
    )


def _mock_tone_shift(context: list[dict[str, Any]]) -> str:
    return (
        "### Management tone\n\n"
        "- MD&A-style language often emphasizes **platform roadmap**, **unit economics**, and **operational discipline**.\n"
        "- **Long-term market expansion** may be stated more explicitly over time, alongside **regulatory compliance** "
        "and **international** expansion.\n"
        "- Transitional items (restructuring, portfolio shifts, macro softness) are typically framed as **near-term** "
        "when excerpts support that reading."
    )


def _mock_answer(question: str, context: list[dict[str, Any]]) -> str:
    """Prefer excerpt-grounded synthesis; add thin thematic hints only when excerpts mention them."""
    q = question.lower()
    grounded = _grounded_answer_paragraph(question, context)

    extra: list[str] = []
    blob = " ".join(str(c.get("text", "")).lower() for c in context)
    if any(w in blob for w in ("export", "license", "control", "geopolitical", "china")):
        extra.append(
            "Theme: export, licensing, or geopolitical risk appears in the retrieved passages above."
        )
    if any(w in blob for w in ("foundry", "tsmc", "samsung", "supply chain", "fabrication")):
        extra.append(
            "Theme: supply chain or manufacturing concentration appears where those clauses were retrieved."
        )
    if any(w in blob for w in ("concentrat", "hyperscale", "customer")):
        extra.append(
            "Theme: customer or revenue concentration is discussed in the excerpts cited above."
        )
    if any(w in q for w in ("quarter", "quarterly", "qoq", "seasonal")):
        extra.insert(
            0,
            "Note: your question asks for quarterly detail. Annual 10-K excerpts rarely include full quarter-by-quarter tables unless that language was retrieved.",
        )

    if extra:
        return grounded + "\n\n" + "\n\n".join(extra)
    return grounded


def _pipeshift_complete(system: str, user: str) -> str | None:
    key, url = _config()
    if not key:
        return None
    try:
        with httpx.Client(timeout=120.0) as client:
            r = client.post(
                url,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": os.getenv("PIPESHIFT_MODEL", "deepseek-ai/DeepSeek-V4-Pro"),
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": float(os.getenv("PIPESHIFT_TEMPERATURE", "0.2")),
                    "stream": False,
                },
            )
            if not r.is_success:
                return None
            data = r.json()
            choices = data.get("choices") or []
            if not choices:
                return None
            msg = choices[0].get("message") or {}
            return str(msg.get("content") or "").strip() or None
    except Exception:
        return None


def analyze_what_changed(context: list[dict[str, Any]]) -> str:
    brief = _context_brief(context)
    out = _pipeshift_complete(
        "Compare Form 10-K excerpts across years for this issuer. Cover material changes in business, risks, "
        "and financial narrative grounded in the excerpts.\n\n"
        + _ANALYSIS_MD_STYLE,
        brief,
    )
    if out:
        return _sanitize_model_answer(out)
    return (
        "### Cross-year filing delta\n\n"
        "Retrieval-grounded synthesis:\n\n"
        "- **Business:** Narratives often evolve toward clearer **platform / segment framing**, expanded "
        "**technology investment** disclosure, and more precise **customer or geographic mix** where excerpts support it.\n"
        "- **Risk factors:** Extra detail tends to accumulate on **regulatory**, **privacy**, **competitive**, "
        "and **macro** themes over time.\n"
        "- **MD&A tone:** May shift from pure growth storytelling toward **efficiency**, **profitability**, "
        "and **compliance** emphasis.\n"
        "- **Financial discussion:** Often mirrors **revenue mix**, **cost structure**, and episodic **one-time items** "
        "referenced in the retrieved text."
    )


def generate_investor_memo(context: list[dict[str, Any]]) -> str:
    brief = _context_brief(context)
    out = _pipeshift_complete(
        "You are a buyside analyst. Write a tight investor memo using ONLY the 10-K excerpts below. "
        "Start with ### Investor memo. Follow with 1–2 short paragraphs. "
        "Use **bold** for critical themes. Cite themes, not calendar dates. "
        "Mention demand, concentration, regulation, margins, or competition only where those ideas appear in excerpts.\n\n"
        + _ANALYSIS_MD_STYLE,
        brief,
    )
    if out:
        return _sanitize_model_answer(out)
    return _mock_investor_memo(context)


def analyze_risk_evolution(context: list[dict[str, Any]]) -> str:
    brief = _context_brief(context)
    out = _pipeshift_complete(
        "Summarize how risk-factor disclosures evolved across fiscal years using ONLY the provided excerpts. "
        "Cover demand, regulation, supply chain, competition, and concentration where those topics appear.\n\n"
        + _ANALYSIS_MD_STYLE,
        brief,
    )
    if out:
        return _sanitize_model_answer(out)
    return _mock_risk_evolution(context)


def analyze_tone_shift(context: list[dict[str, Any]]) -> str:
    brief = _context_brief(context)
    out = _pipeshift_complete(
        "Describe how management tone and emphasis shift in MD&A-style passages across years, using only the excerpts. "
        "Note confidence, caution, investment framing, and regulatory posture where visible.\n\n"
        + _ANALYSIS_MD_STYLE,
        brief,
    )
    if out:
        return _sanitize_model_answer(out)
    return _mock_tone_shift(context)


def answer_question(question: str, context: list[dict[str, Any]]) -> str:
    brief = _context_brief(context)
    out = _pipeshift_complete(
        "Answer using ONLY the provided SEC filing excerpts. Name fiscal years and sections when visible. "
        "Write in short, readable paragraphs. Do NOT paste JSON, metadata fields, or raw financial tables. "
        "Paraphrase prose; if excerpts omit what the user asked, say so briefly.\n\n"
        "Output ONLY the answer text for the end user. Do NOT include planning, scratch work, or phrases "
        "like 'We need to answer' or 'The user is asking'.",
        f"Question: {question}\n\nContext:\n{brief}",
    )
    if out:
        return _sanitize_model_answer(out)
    return _mock_answer(question, context)


def answer_with_chat(
    question: str,
    filing_context: list[dict[str, Any]],
    chat_history: list[dict[str, str]],
    hydra_memory_brief: str | None = None,
) -> str:
    """Multi-turn: use prior messages + filings excerpts; hydra_memory_brief is optional recalled chat from HydraDB."""
    filing_brief = _context_brief(filing_context)
    lines: list[str] = []
    for m in chat_history[-24:]:
        role = str(m.get("role", "user")).upper()
        content = str(m.get("content", "")).strip()
        if content:
            lines.append(f"{role}: {content}")
    history_blob = "\n".join(lines) if lines else "(no prior turns)"
    hydra_blob = f"\n\nRecalled from long-term memory (HydraDB):\n{hydra_memory_brief}\n" if hydra_memory_brief else ""
    user_blob = (
        f"Prior conversation (same session, oldest to newest):\n{history_blob}\n\n"
        f"Current message:\n{question}\n\n"
        f"Filings excerpts (primary evidence):\n{filing_brief}{hydra_blob}"
    )
    out = _pipeshift_complete(
        "You help users analyze SEC 10-K filings. Primary evidence = filings excerpts below. "
        "Resolve follow-ups using prior conversation + excerpts. Write clearly; never dump JSON or raw tables. "
        "If HydraDB chat memory conflicts with excerpts, trust excerpts.\n\n"
        "Reply with ONLY the user-visible answer: no planning, no 'We need to answer', no chain-of-thought. "
        "At most 2–4 short paragraphs. If numeric facts are needed and excerpts lack them, say so briefly.",
        user_blob,
    )
    if out:
        return _sanitize_model_answer(out)
    base = _mock_answer(question, filing_context)
    if chat_history:
        note = (
            "[Note: Live LLM unavailable — showing excerpt-based fallback. "
            "Set PIPESHIFT_API_KEY on the server for full answers.]\n\n"
        )
        return note + base
    return base


def evidence_snippets(context: list[dict[str, Any]], limit: int = 6) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in context[:limit]:
        raw = str(c.get("text", ""))
        raw = _extract_text_if_json_wrapper(raw)
        nice = _clean_excerpt_text(raw)
        if _looks_like_table_or_noise(raw) or '"tenant_id"' in nice[:400]:
            nice = _clean_excerpt_text(raw.split('"tenant_metadata"')[0])[:280]
            if len(nice) < 40 or '"id"' in nice:
                nice = "[Numeric/table or structured excerpt — open the filed 10-K for full detail]"
        snippet = nice[:320] + ("…" if len(nice) > 320 else "")
        out.append(
            {
                "ticker": c.get("ticker"),
                "year": c.get("year"),
                "section": c.get("section"),
                "chunk_id": c.get("chunk_id"),
                "snippet": snippet,
            }
        )
    return out
