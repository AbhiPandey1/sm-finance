"""Persist chat turns to disk so conversations survive API restarts."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

_MAX_MESSAGES = 64  # total messages (user + assistant)

_lock = Lock()


def _backend_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _sessions_dir() -> Path:
    d = _backend_root() / "data" / "cache" / "chat_sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _file_for(conversation_id: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", conversation_id)
    if len(safe) > 180:
        safe = safe[:180]
    return _sessions_dir() / f"{safe}.json"


def _load_raw(conversation_id: str) -> dict[str, Any]:
    path = _file_for(conversation_id)
    if not path.exists():
        return {"messages": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"messages": []}


def _save_raw(conversation_id: str, ticker: str, messages: list[dict[str, Any]]) -> None:
    path = _file_for(conversation_id)
    payload = {
        "conversation_id": conversation_id,
        "ticker": ticker.upper(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "messages": messages[-_MAX_MESSAGES:],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def get_messages(conversation_id: str) -> list[dict[str, Any]]:
    with _lock:
        data = _load_raw(conversation_id)
        return list(data.get("messages", []))


def get_session(conversation_id: str) -> dict[str, Any]:
    """Full session including ticker for validation."""
    with _lock:
        data = _load_raw(conversation_id)
        data.setdefault("messages", [])
        data.setdefault("conversation_id", conversation_id)
        return data


def append_message(
    conversation_id: str,
    role: str,
    content: str,
    *,
    ticker: str = "AAPL",
    evidence: list[dict[str, Any]] | None = None,
) -> None:
    with _lock:
        data = _load_raw(conversation_id)
        msgs: list[dict[str, Any]] = list(data.get("messages", []))
        row: dict[str, Any] = {"role": role, "content": content}
        if role == "assistant" and evidence:
            row["evidence"] = evidence
        msgs.append(row)
        msgs = msgs[-_MAX_MESSAGES:]
        _save_raw(conversation_id, ticker, msgs)


def clear_session(conversation_id: str) -> None:
    with _lock:
        path = _file_for(conversation_id)
        if path.exists():
            path.unlink()


def touch_ticker(conversation_id: str, ticker: str) -> None:
    """Ensure session file exists with ticker when starting a new thread."""
    with _lock:
        data = _load_raw(conversation_id)
        if not data.get("messages"):
            _save_raw(conversation_id, ticker, [])


def list_threads_for_ticker(ticker: str, *, limit: int = 80) -> list[dict[str, Any]]:
    """List persisted sessions for a ticker (newest mtime first)."""
    t = ticker.upper()
    want = limit * 3  # scan extra; filter by ticker
    paths = sorted(
        _sessions_dir().glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:want]
    out: list[dict[str, Any]] = []
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if (data.get("ticker") or "").upper() != t:
            continue
        msgs = list(data.get("messages") or [])
        cid = str(data.get("conversation_id") or path.stem)
        preview = ""
        for m in msgs:
            if m.get("role") == "user":
                preview = str(m.get("content", "")).strip().replace("\n", " ")[:100]
                break
        out.append(
            {
                "conversation_id": cid,
                "updated_at": data.get("updated_at"),
                "message_count": len(msgs),
                "preview": preview or "(empty)",
            }
        )
        if len(out) >= limit:
            break
    return out
