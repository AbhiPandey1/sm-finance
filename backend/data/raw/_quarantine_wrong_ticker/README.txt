Files moved here were labeled with the wrong ticker vs their actual issuer.

Restore correct filings under `backend/data/raw/` using names like `TICKER_YYYY_10K.html` or
`ticker-YYYYMMDD.html`, then run `POST /api/ingest?ticker=TICKER`.

You can delete this folder once you no longer need the mistaken downloads.
