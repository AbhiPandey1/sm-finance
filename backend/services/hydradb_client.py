"""
HydraDB context layer with full local JSON fallback for demos without credentials.

Preferred path (Python 3.10+): official SDK — https://docs.hydradb.com/api-reference/sdks
  - client.upload.knowledge → POST /ingestion/upload_knowledge
  - client.recall.full_recall → POST /recall/full_recall

Fallback: same endpoints via httpx (e.g. Python 3.9 or if hydra-db-python is not installed).

API key: HYDRADB_API_KEY (docs also mention HYDRA_DB_API_KEY).
Requires HYDRADB_TENANT_ID — use the **tenant slug** you chose at creation (e.g. ``smfinancial``),
not necessarily the short internal id shown in the dashboard (e.g. ``tqsan7ycxi``), unless Hydra
support confirms otherwise.

Optional: HYDRADB_SUB_TENANT_ID, or HYDRADB_DEFAULT_SUB_TENANT_ID (e.g. ``filings``) when you want
an explicit sub-tenant on first ingest. The dashboard lists sub-tenants that **already have indexed
data** ([sub_tenant_ids API](https://docs.hydradb.com/api-reference/endpoint/list-sub-tenant-ids.md));
seeing **0** usually means ingest never succeeded for that tenant_id or indexing is not finished yet.
"""

from __future__ import annotations

import json
import logging
import os
import re
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

_CHUNK_PATH: Path | None = None

_DEFAULT_BASE = "https://api.hydradb.com"
_UPLOAD_BATCH = 20


def _backend_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _chunks_file() -> Path:
    global _CHUNK_PATH
    if _CHUNK_PATH is None:
        _CHUNK_PATH = _backend_root() / "data" / "cache" / "chunks.json"
        _CHUNK_PATH.parent.mkdir(parents=True, exist_ok=True)
    return _CHUNK_PATH


def _hydradb_config() -> tuple[str | None, str]:
    api_key = (
        os.getenv("HYDRADB_API_KEY")
        or os.getenv("HYDRA_DB_API_KEY")
        or os.getenv("HYDRA_API_KEY")
    )
    base_url = (
        os.getenv("HYDRADB_BASE_URL")
        or os.getenv("HYDRA_BASE_URL")
        or os.getenv("HYDRADB_URL")
        or _DEFAULT_BASE
    )
    return api_key, base_url.rstrip("/")


def _hydradb_tenant() -> tuple[str | None, str]:
    tenant_id = os.getenv("HYDRADB_TENANT_ID") or os.getenv("HYDRA_TENANT_ID")
    sub = (
        (os.getenv("HYDRADB_SUB_TENANT_ID") or os.getenv("HYDRA_SUB_TENANT_ID") or "").strip()
        or (os.getenv("HYDRADB_DEFAULT_SUB_TENANT_ID") or "").strip()
    )
    return tenant_id, sub


def _fetch_sub_tenant_ids_with_data(tenant_id: str) -> dict[str, Any]:
    """GET /tenants/sub_tenant_ids — sub-tenants that already have indexed data (may be [] until ingest works)."""
    api_key, base = _hydradb_config()
    if not api_key or not tenant_id:
        return {"ok": False, "error": "missing api key or tenant_id"}
    url = f"{base}/tenants/sub_tenant_ids"
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.get(
                url,
                params={"tenant_id": tenant_id},
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if not r.is_success:
                log.warning(
                    "HydraDB sub_tenant_ids lookup failed: status=%s body=%s",
                    r.status_code,
                    r.text[:500],
                )
                return {
                    "ok": False,
                    "http_status": r.status_code,
                    "error": r.text[:800],
                }
            data = r.json()
            ids = data.get("sub_tenant_ids") or []
            log.info(
                "HydraDB sub_tenant_ids for tenant_id=%s: %s",
                tenant_id,
                ids,
            )
            return {"ok": True, "sub_tenant_ids": ids, "message": data.get("message")}
    except Exception as e:
        log.exception("HydraDB sub_tenant_ids request error: %s", e)
        return {"ok": False, "error": str(e)[:800]}


def _hydradb_ready_for_remote() -> bool:
    api_key, _ = _hydradb_config()
    tenant_id, _ = _hydradb_tenant()
    return bool(api_key and tenant_id)


def _hydradb_sdk_available() -> bool:
    try:
        from hydra_db import HydraDB  # type: ignore[import-untyped]  # noqa: F401

        return True
    except ImportError:
        return False


def _sdk_client():
    from hydra_db import HydraDB  # type: ignore[import-untyped]

    token = (
        os.getenv("HYDRADB_API_KEY")
        or os.getenv("HYDRA_DB_API_KEY")
        or os.getenv("HYDRA_API_KEY")
    )
    if not token:
        return None
    return HydraDB(token=token)


def _vector_chunk_to_dict(h: Any) -> dict[str, Any]:
    if hasattr(h, "model_dump"):
        return h.model_dump()
    if hasattr(h, "dict"):
        return h.dict()
    if isinstance(h, dict):
        return h
    return dict(h)


def _load_local() -> dict[str, Any]:
    path = _chunks_file()
    if not path.exists():
        return {"chunks": [], "backend": "local_json"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if "chunks" not in data:
            data = {"chunks": [], "backend": "local_json"}
        return data
    except json.JSONDecodeError:
        return {"chunks": [], "backend": "local_json"}


def _save_local(chunks: list[dict[str, Any]]) -> None:
    path = _chunks_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"chunks": chunks, "backend": "local_json"}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _chunk_to_app_knowledge(
    c: dict[str, Any],
    tenant_id: str,
    sub_tenant_id: str,
) -> dict[str, Any]:
    ticker = str(c.get("ticker", ""))
    year = c.get("year")
    section = c.get("section")
    return {
        "id": str(c.get("chunk_id")),
        "tenant_id": tenant_id,
        "sub_tenant_id": sub_tenant_id,
        "title": f"{ticker} {year} 10-K — {section}",
        "source": "filings_memory_agent",
        "description": f"SEC 10-K chunk: {ticker} {year} {section}",
        "content": {"text": str(c.get("text", ""))},
        "metadata": {
            "ticker": ticker,
            "year": year,
            "section": section,
            "filing_type": c.get("filing_type"),
            "company": c.get("company"),
            "chunk_id": c.get("chunk_id"),
        },
        "additional_metadata": {
            "ingested_by": "filings_memory_agent",
        },
    }


def _file_metadata_row(c: dict[str, Any]) -> dict[str, Any]:
    """Per hydra-db-python README: id + tenant_metadata / document_metadata."""
    return {
        "id": str(c.get("chunk_id")),
        "tenant_metadata": {
            "ticker": str(c.get("ticker", "")),
            "year": c.get("year"),
            "section": c.get("section"),
            "chunk_id": c.get("chunk_id"),
            "company": c.get("company"),
            "filing_type": c.get("filing_type"),
        },
    }


def _hydradb_upsert_sdk(chunks: list[dict[str, Any]]) -> tuple[bool, str | None]:
    tenant_id, sub_tenant_id = _hydradb_tenant()
    client = _sdk_client()
    if not client or not tenant_id:
        log.warning("HydraDB SDK upsert skipped: client=%s tenant_id=%s", bool(client), bool(tenant_id))
        return False, None

    try:
        n_batches = (len(chunks) + _UPLOAD_BATCH - 1) // _UPLOAD_BATCH
        for i in range(0, len(chunks), _UPLOAD_BATCH):
            batch = chunks[i : i + _UPLOAD_BATCH]
            files: list[tuple[str, BytesIO, str]] = []
            meta_rows: list[dict[str, Any]] = []
            for c in batch:
                cid = str(c.get("chunk_id"))
                body = str(c.get("text", "")).encode("utf-8")
                files.append((f"{cid}.txt", BytesIO(body), "text/plain"))
                meta_rows.append(_file_metadata_row(c))
            client.upload.knowledge(
                tenant_id=tenant_id,
                sub_tenant_id=sub_tenant_id or "",
                files=files,
                file_metadata=json.dumps(meta_rows),
                upsert=True,
            )
        log.info(
            "HydraDB SDK upload_knowledge ok: tenant_id=%s chunks=%s batches=%s",
            tenant_id,
            len(chunks),
            n_batches,
        )
        return True, None
    except Exception as e:
        log.exception("HydraDB SDK upload_knowledge failed: %s", e)
        return False, str(e)[:800]


def _hydradb_upsert_http(chunks: list[dict[str, Any]]) -> tuple[bool, str | None]:
    api_key, base = _hydradb_config()
    tenant_id, sub_tenant_id = _hydradb_tenant()
    if not api_key:
        return False, "Missing HYDRADB_API_KEY"
    if not tenant_id:
        return (
            False,
            "Missing HYDRADB_TENANT_ID — create a tenant in the HydraDB dashboard and set this env var.",
        )

    url = f"{base}/ingestion/upload_knowledge"
    headers = {"Authorization": f"Bearer {api_key}"}
    last_err: str | None = None

    try:
        with httpx.Client(timeout=120.0) as client:
            for i in range(0, len(chunks), _UPLOAD_BATCH):
                batch = chunks[i : i + _UPLOAD_BATCH]
                app_knowledge = [
                    _chunk_to_app_knowledge(c, tenant_id, sub_tenant_id) for c in batch
                ]
                # Multipart: tenant_id + app_knowledge; optional sub_tenant_id (see multi-tenant docs)
                files_mp: dict[str, Any] = {
                    "tenant_id": (None, tenant_id),
                    "app_knowledge": (None, json.dumps(app_knowledge)),
                }
                if sub_tenant_id:
                    files_mp["sub_tenant_id"] = (None, sub_tenant_id)
                r = client.post(url, headers=headers, files=files_mp)
                if not r.is_success:
                    last_err = f"HTTP {r.status_code}: {r.text[:800]}"
                    log.error(
                        "HydraDB HTTP upload_knowledge failed: url=%s status=%s body=%s",
                        url,
                        r.status_code,
                        r.text[:1200],
                    )
                    return False, last_err
        log.info(
            "HydraDB HTTP upload_knowledge ok: tenant_id=%s chunks=%s url=%s",
            tenant_id,
            len(chunks),
            url,
        )
        return True, None
    except Exception as e:
        log.exception("HydraDB HTTP upload_knowledge error: %s", e)
        return False, str(e)[:800]


def _hydradb_upsert_remote(chunks: list[dict[str, Any]]) -> tuple[bool, str | None, str]:
    api_key, _ = _hydradb_config()
    tenant_id, _ = _hydradb_tenant()
    if not api_key:
        return False, "Missing HYDRADB_API_KEY", ""
    if not tenant_id:
        return (
            False,
            "Missing HYDRADB_TENANT_ID — create a tenant in the HydraDB dashboard and set this env var.",
            "",
        )

    if _hydradb_sdk_available():
        ok, err = _hydradb_upsert_sdk(chunks)
        if ok:
            return True, None, "sdk"
        if err:
            log.warning("HydraDB SDK ingest failed, falling back to HTTP: %s", err)
        else:
            log.warning("HydraDB SDK ingest failed with no message; falling back to HTTP")

    ok, err = _hydradb_upsert_http(chunks)
    if not ok and err:
        log.error("HydraDB HTTP ingest failed after SDK attempt: %s", err)
    return ok, err, "http"


def _map_recall_chunk(raw: dict[str, Any]) -> dict[str, Any]:
    """Map Hydra recall row to our chunk shape.

    **Important:** Do not substitute ``requested_ticker`` when metadata omits ticker.
    Otherwise unrelated filings indexed in the same tenant can be mis-attributed if ticker metadata is missing.
    get labeled as the company you asked for and poison analysis.
    """
    tm = raw.get("tenant_metadata") or {}
    dm = raw.get("document_metadata") or {}
    merged = {**dm, **tm}
    year = merged.get("year")
    try:
        year_i = int(year) if year is not None else 0
    except (TypeError, ValueError):
        year_i = 0

    blob = " ".join(
        str(raw.get(k) or "")
        for k in ("source_title", "document_metadata", "chunk_content", "title", "description")
    )

    raw_ticker = merged.get("ticker")
    if not raw_ticker:
        # Upload path sets titles like "AAPL 2024 10-K — business"
        m = re.search(r"\b([A-Z]{1,5})\s+(20\d{2})\s+10-K", blob)
        if m:
            raw_ticker = m.group(1)

    ticker_val = str(raw_ticker or "").strip().upper()

    if year_i <= 0:
        m = re.search(r"(20\d{2})", blob)
        if m:
            try:
                year_i = int(m.group(1))
            except ValueError:
                pass
    sec = str(merged.get("section") or "").strip() or "unknown"
    if sec == "unknown":
        low = str(raw.get("chunk_content", ""))[:600].lower()
        if "risk factor" in low or "item 1a" in low:
            sec = "risk_factors"
        elif "management" in low and "discussion" in low:
            sec = "md_and_a"
        elif "financial statement" in low or "item 8" in low:
            sec = "financial_statements"
        elif "item 1" in low and "business" in low:
            sec = "business"

    return {
        "company": merged.get("company"),
        "ticker": ticker_val,
        "year": year_i,
        "section": sec,
        "filing_type": merged.get("filing_type", "10-K"),
        "chunk_id": str(merged.get("chunk_id") or raw.get("source_id") or raw.get("chunk_uuid", "")),
        "text": str(raw.get("chunk_content", "")),
    }


def _filter_recall_chunks(
    raw_chunks: list[Any],
    ticker: str,
    years: list[int] | None,
    sections: list[str] | None,
    top_k: int,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    ticker_u = ticker.upper()
    for h in raw_chunks:
        raw = _vector_chunk_to_dict(h)
        if not isinstance(raw, dict):
            continue
        c = _map_recall_chunk(raw)
        ct = str(c.get("ticker", "")).upper().strip()
        if not ct:
            continue
        if ct != ticker_u:
            continue
        if years and int(c.get("year", 0)) not in years:
            continue
        if sections:
            sec_set = {s.lower() for s in sections}
            if str(c.get("section", "")).lower() not in sec_set:
                continue
        out.append(c)
        if len(out) >= top_k:
            break
    return out


def _hydradb_search_sdk(
    query: str,
    ticker: str,
    years: list[int] | None,
    sections: list[str] | None,
    top_k: int,
) -> list[dict[str, Any]] | None:
    if not _hydradb_sdk_available():
        return None
    tenant_id, sub_tenant_id = _hydradb_tenant()
    client = _sdk_client()
    if not client or not tenant_id:
        return None
    try:
        results = client.recall.full_recall(
            tenant_id=tenant_id,
            sub_tenant_id=sub_tenant_id or "",
            query=query,
            alpha=0.8,
            recency_bias=0,
            max_results=min(max(top_k * 3, top_k), 40),
        )
        raw_chunks = list(results.chunks or [])
    except Exception as e:
        log.exception("HydraDB SDK full_recall failed: %s", e)
        return None
    filtered = _filter_recall_chunks(raw_chunks, ticker, years, sections, top_k)
    log.debug(
        "HydraDB SDK full_recall: raw=%s filtered=%s ticker=%s",
        len(raw_chunks),
        len(filtered),
        ticker,
    )
    return filtered


def _hydradb_search_http(
    query: str,
    ticker: str,
    years: list[int] | None,
    sections: list[str] | None,
    top_k: int,
) -> list[dict[str, Any]] | None:
    api_key, base = _hydradb_config()
    tenant_id, sub_tenant_id = _hydradb_tenant()
    if not api_key or not tenant_id:
        return None

    url = f"{base}/recall/full_recall"
    body: dict[str, Any] = {
        "tenant_id": tenant_id,
        "sub_tenant_id": sub_tenant_id,
        "query": query,
        "mode": "fast",
        "max_results": min(max(top_k * 3, top_k), 40),
    }

    try:
        with httpx.Client(timeout=60.0) as client:
            r = client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=body,
            )
            if not r.is_success:
                log.error(
                    "HydraDB HTTP full_recall failed: status=%s body=%s",
                    r.status_code,
                    r.text[:1200],
                )
                return None
            data = r.json()
            raw_chunks = data.get("chunks") or []
    except Exception as e:
        log.exception("HydraDB HTTP full_recall error: %s", e)
        return None

    filtered = _filter_recall_chunks(raw_chunks, ticker, years, sections, top_k)
    log.debug(
        "HydraDB HTTP full_recall: raw=%s filtered=%s",
        len(raw_chunks),
        len(filtered),
    )
    return filtered


def _hydradb_search_remote(
    query: str,
    ticker: str,
    years: list[int] | None,
    sections: list[str] | None,
    top_k: int,
) -> list[dict[str, Any]] | None:
    if not _hydradb_ready_for_remote():
        return None

    if _hydradb_sdk_available():
        got = _hydradb_search_sdk(query, ticker, years, sections, top_k)
        if got is not None:
            return got
        log.warning("HydraDB SDK search unavailable or failed; trying HTTP full_recall")

    return _hydradb_search_http(query, ticker, years, sections, top_k)


def upsert_chunks(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    """Store chunks in HydraDB when configured; always mirror to local JSON for reliable reads."""
    if not chunks:
        return {"stored": 0, "backend": "none"}

    remote_ok, hydradb_error, hydradb_transport = _hydradb_upsert_remote(chunks)
    if remote_ok:
        log.info(
            "Ingest: HydraDB write succeeded (%s), %s chunks; local mirror updated",
            hydradb_transport,
            len(chunks),
        )
    else:
        log.warning(
            "Ingest: HydraDB write failed (%s): %s — local mirror still updated",
            hydradb_transport or "none",
            hydradb_error or "unknown error",
        )

    data = _load_local()
    existing: list[dict[str, Any]] = data.get("chunks", [])
    keyfn = lambda c: (c.get("ticker"), c.get("year"), c.get("section"), c.get("chunk_id"))
    index = {keyfn(c): c for c in existing}
    for c in chunks:
        index[keyfn(c)] = c
    merged = list(index.values())
    _save_local(merged)

    tenant_id, sub_used = _hydradb_tenant()
    result: dict[str, Any] = {
        "stored": len(chunks),
        "total": len(merged),
        "hydradb_ingest_ok": remote_ok,
        "hydradb_error": hydradb_error if not remote_ok else None,
        "hydradb_transport": hydradb_transport or None,
        "hydradb_sdk_available": _hydradb_sdk_available(),
        "hydradb_tenant_id_used": tenant_id,
        "hydradb_sub_tenant_id_used": sub_used or None,
    }
    if _hydradb_ready_for_remote() and tenant_id:
        diag = _fetch_sub_tenant_ids_with_data(tenant_id)
        result["hydradb_diagnostics"] = diag
        if remote_ok and diag.get("ok") and not diag.get("sub_tenant_ids"):
            result["hydradb_hint"] = (
                "Ingest returned OK but no sub-tenants with indexed data yet — "
                "vectors may still be processing; wait and refresh the dashboard, or call verify_processing if you have source ids. "
                "If this persists, confirm HYDRADB_TENANT_ID matches your API tenant slug (e.g. smfinancial) "
                "and try HYDRADB_SUB_TENANT_ID=filings or HYDRADB_DEFAULT_SUB_TENANT_ID=filings."
            )
    if remote_ok:
        result["backend"] = "hydradb"
    else:
        result["backend"] = "local_json"
        api_key, _ = _hydradb_config()
        if api_key and not tenant_id:
            result["hydradb_hint"] = (
                "Set HYDRADB_TENANT_ID (tenant slug from creation, e.g. smfinancial), then ingest again. "
                "Chunks are mirrored in backend/data/cache/chunks.json until HydraDB ingest succeeds."
            )
        elif api_key and tenant_id and not remote_ok:
            prev = result.get("hydradb_hint")
            extra = (
                " Check hydradb_error and server logs. Try HYDRADB_TENANT_ID=smfinancial (your slug) "
                "if you currently use the dashboard internal id. Set HYDRADB_DEFAULT_SUB_TENANT_ID=filings for an explicit sub-tenant."
            )
            result["hydradb_hint"] = (prev + extra) if prev else extra.strip()
    return result


def _tokenize(q: str) -> set[str]:
    return {t for t in re.split(r"[^\w]+", q.lower()) if len(t) > 2}


def _score_chunk(query_tokens: set[str], text: str) -> float:
    if not query_tokens:
        return 0.0
    words = re.split(r"[^\w]+", text.lower())
    bag: dict[str, int] = {}
    for w in words:
        if len(w) <= 2:
            continue
        bag[w] = bag.get(w, 0) + 1
    score = 0.0
    for t in query_tokens:
        score += min(3.0, 0.35 * bag.get(t, 0))
    return score


def search_context(
    query: str,
    ticker: str,
    years: list[int] | None = None,
    sections: list[str] | None = None,
    top_k: int = 8,
) -> list[dict[str, Any]]:
    """Prefer HydraDB full_recall when tenant + key are set; else lexical search on local cache.

    If Hydra returns no rows after strict ticker filtering, fall back to local JSON so we never
    prefer an empty remote result over correctly tagged cached chunks.
    """
    remote = _hydradb_search_remote(query, ticker, years, sections, top_k)
    if remote:
        return remote[:top_k]

    data = _load_local()
    chunks = data.get("chunks", [])
    ticker_u = ticker.upper()
    filtered = [c for c in chunks if str(c.get("ticker", "")).upper() == ticker_u]
    if years:
        filtered = [c for c in filtered if int(c.get("year", 0)) in years]
    if sections:
        sec_set = {s.lower() for s in sections}
        filtered = [c for c in filtered if str(c.get("section", "")).lower() in sec_set]

    q_tokens = _tokenize(query)
    ranked: list[tuple[float, dict[str, Any]]] = []
    for c in filtered:
        text = str(c.get("text", ""))
        s = _score_chunk(q_tokens, text)
        ranked.append((s, c))
    ranked.sort(key=lambda x: x[0], reverse=True)
    out = []
    for s, c in ranked[:top_k]:
        item = dict(c)
        item["_score"] = round(s, 4)
        out.append(item)
    if not out and filtered:
        for c in filtered[:top_k]:
            out.append(dict(c))
    return out


def get_chunks_for_ticker(ticker: str, years: list[int] | None = None) -> list[dict[str, Any]]:
    """Return all cached chunks for a ticker from local mirror."""
    data = _load_local()
    chunks = [
        c
        for c in data.get("chunks", [])
        if str(c.get("ticker", "")).upper() == ticker.upper()
    ]
    if years:
        chunks = [c for c in chunks if int(c.get("year", 0)) in years]
    chunks.sort(key=lambda c: (int(c.get("year", 0)), str(c.get("section", "")), str(c.get("chunk_id", ""))))
    return chunks


def append_chat_turn_hydradb(
    conversation_id: str,
    ticker: str,
    user_text: str,
    assistant_text: str,
) -> tuple[bool, str | None]:
    """
    Store a user/assistant turn in HydraDB user memory (POST /memories/add_memory).
    Best-effort: failures do not block chat; check logs.
    """
    if not _hydradb_ready_for_remote():
        return False, "hydra not configured"

    tenant_id, sub_tenant_id = _hydradb_tenant()
    assert tenant_id

    if _hydradb_sdk_available():
        try:
            from hydra_db import HydraDB  # type: ignore[import-untyped]

            token = (
                os.getenv("HYDRADB_API_KEY")
                or os.getenv("HYDRA_DB_API_KEY")
                or os.getenv("HYDRA_API_KEY")
            )
            client = HydraDB(token=token or "")
            # SDK: user_memory.add (see https://docs.hydradb.com/api-reference/sdks)
            client.user_memory.add(
                tenant_id=tenant_id,
                sub_tenant_id=sub_tenant_id or "",
                upsert=True,
                memories=[
                    {
                        "user_assistant_pairs": [{"user": user_text, "assistant": assistant_text}],
                        "infer": False,
                        "user_name": f"filings_chat:{conversation_id}",
                        "custom_instructions": f"SEC filings chat; ticker={ticker}; session={conversation_id}",
                    }
                ],
            )
            log.info("HydraDB add_memory (chat) ok session=%s", conversation_id[:8])
            return True, None
        except Exception as e:
            log.warning("HydraDB SDK add_memory (chat) failed: %s", e)

    api_key, base = _hydradb_config()
    url = f"{base}/memories/add_memory"
    body: dict[str, Any] = {
        "tenant_id": tenant_id,
        "sub_tenant_id": sub_tenant_id or "",
        "upsert": True,
        "memories": [
            {
                "user_assistant_pairs": [{"user": user_text, "assistant": assistant_text}],
                "infer": False,
                "user_name": f"filings_chat:{conversation_id}",
                "custom_instructions": f"SEC filings chat; ticker={ticker}; session={conversation_id}",
            }
        ],
    }
    try:
        with httpx.Client(timeout=60.0) as client:
            r = client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=body,
            )
            if not r.is_success:
                err = f"HTTP {r.status_code}: {r.text[:600]}"
                log.warning("HydraDB HTTP add_memory (chat) failed: %s", err)
                return False, err
        log.info("HydraDB HTTP add_memory (chat) ok session=%s", conversation_id[:8])
        return True, None
    except Exception as e:
        log.warning("HydraDB HTTP add_memory (chat) error: %s", e)
        return False, str(e)[:600]


def recall_chat_memories_brief(conversation_id: str, query: str, top_k: int = 4) -> str | None:
    """Pull relevant past chat turns from HydraDB user memory (recall_preferences)."""
    if not _hydradb_ready_for_remote():
        return None

    tenant_id, sub_tenant_id = _hydradb_tenant()
    api_key, base = _hydradb_config()
    if not api_key or not tenant_id:
        return None

    q = f"{query}\n(session: {conversation_id})"

    if _hydradb_sdk_available():
        try:
            from hydra_db import HydraDB  # type: ignore[import-untyped]

            token = api_key
            client = HydraDB(token=token)
            results = client.recall.recall_preferences(
                tenant_id=tenant_id,
                sub_tenant_id=sub_tenant_id or "",
                query=q,
                alpha=0.8,
                recency_bias=0.2,
                max_results=top_k,
            )
            raw = list(results.chunks or [])
        except Exception as e:
            log.debug("HydraDB SDK recall_preferences failed: %s", e)
            raw = []
    else:
        url = f"{base}/recall/recall_preferences"
        body = {
            "tenant_id": tenant_id,
            "sub_tenant_id": sub_tenant_id or "",
            "query": q,
            "mode": "fast",
            "max_results": top_k,
            "additional_context": f"Filings chat session {conversation_id}",
        }
        raw = []
        try:
            with httpx.Client(timeout=45.0) as client:
                r = client.post(
                    url,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json=body,
                )
                if r.is_success:
                    raw = r.json().get("chunks") or []
        except Exception as e:
            log.debug("HydraDB HTTP recall_preferences failed: %s", e)

    if not raw:
        return None
    parts: list[str] = []
    for h in raw[:top_k]:
        d = _vector_chunk_to_dict(h)
        if not isinstance(d, dict):
            continue
        txt = str(d.get("chunk_content") or d.get("content") or "")
        if txt.strip():
            parts.append(txt.strip()[:1500])
    if not parts:
        return None
    return "\n---\n".join(parts)


def get_company_memory(ticker: str) -> dict[str, Any]:
    """Summary stats for a ticker (works on local fallback)."""
    chunks = get_chunks_for_ticker(ticker)
    years: dict[int, int] = {}
    sections: dict[str, int] = {}
    for c in chunks:
        y = int(c.get("year", 0))
        years[y] = years.get(y, 0) + 1
        sec = str(c.get("section", "unknown"))
        sections[sec] = sections.get(sec, 0) + 1

    api_key, base = _hydradb_config()
    tenant_id, sub_used = _hydradb_tenant()
    ready = bool(api_key and tenant_id)

    return {
        "ticker": ticker.upper(),
        "chunk_count": len(chunks),
        "years": dict(sorted(years.items())),
        "sections": sections,
        "hydradb_api_configured": bool(api_key),
        "hydradb_tenant_configured": bool(tenant_id),
        "hydradb_sub_tenant_id_used": sub_used or None,
        "hydradb_base_url": base,
        "hydradb_ready_for_remote": ready,
        "hydradb_sdk_available": _hydradb_sdk_available(),
    }
