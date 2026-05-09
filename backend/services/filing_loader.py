"""Load local SEC 10-K HTML filings from disk."""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple


class FilingRef(NamedTuple):
    company: str
    ticker: str
    year: int
    filing_type: str
    path: Path


def _backend_root() -> Path:
    return Path(__file__).resolve().parent.parent


def raw_dir() -> Path:
    return _backend_root() / "data" / "raw"


def display_company_name(ticker: str) -> str:
    """Human-readable issuer name for chunk metadata (extend as you add tickers)."""
    t = ticker.upper()
    known = {
        "AAPL": "Apple Inc.",
        "APP": "AppLovin Corporation",
    }
    return known.get(t, f"{t} Corporation")


def list_cached_filings(ticker: str = "AAPL") -> list[FilingRef]:
    """Return known cached 10-K HTML files for a ticker (.html or .htm).

    Supported filenames:
    - ``TICKER_YEAR_10K.html`` (e.g. ``AAPL_2024_10K.html``)
    - ``ticker-YYYYMMDD.html`` (SEC-style, e.g. ``aapl-20250927.html`` — year taken from period-end date prefix).
    """
    t = ticker.upper()
    company = display_company_name(t)
    base = raw_dir()
    refs: list[FilingRef] = []

    paths = set(base.glob(f"{t}_*_10K.html")) | set(base.glob(f"{t}_*_10K.htm"))
    for path in sorted(paths):
        stem = path.stem
        parts = stem.split("_")
        if len(parts) >= 2 and parts[-1] == "10K":
            try:
                year = int(parts[1])
            except ValueError:
                continue
            refs.append(
                FilingRef(
                    company=company,
                    ticker=t,
                    year=year,
                    filing_type="10-K",
                    path=path,
                )
            )

    tl = t.lower()
    dash_paths = set(base.glob(f"{tl}-*.html")) | set(base.glob(f"{tl}-*.htm"))
    for path in sorted(dash_paths):
        stem = path.stem
        if not stem.startswith(f"{tl}-"):
            continue
        suffix = stem[len(tl) + 1 :]
        if len(suffix) != 8 or not suffix.isdigit():
            continue
        try:
            year = int(suffix[:4])
        except ValueError:
            continue
        refs.append(
            FilingRef(
                company=company,
                ticker=t,
                year=year,
                filing_type="10-K",
                path=path,
            )
        )

    # Dedupe by path (same file matched twice should not happen)
    seen: set[Path] = set()
    unique: list[FilingRef] = []
    for r in sorted(refs, key=lambda x: (x.year, x.path.name)):
        if r.path in seen:
            continue
        seen.add(r.path)
        unique.append(r)
    return unique


def read_filing_html(ref: FilingRef) -> str:
    return ref.path.read_text(encoding="utf-8", errors="replace")
