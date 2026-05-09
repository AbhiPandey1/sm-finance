"use client";

import { useEffect, useMemo, useState } from "react";
import { AskBox } from "@/components/AskBox";
import { FinancialMetrics } from "@/components/FinancialMetrics";
import { InsightCard } from "@/components/InsightCard";
import type { EvidenceItem } from "@/components/EvidenceList";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

type Analysis = {
  what_changed: string;
  risk_evolution: string;
  tone_shift: string;
  investor_memo: string;
  evidence_snippets: EvidenceItem[];
};

export default function Home() {
  const companies = useMemo(
    () => [
      { value: "AAPL", label: "Apple (AAPL)" },
      { value: "APP", label: "AppLovin (APP)" },
    ],
    []
  );
  const [company, setCompany] = useState("AAPL");
  const [cachedYears, setCachedYears] = useState<number[]>([]);
  const [ingestStatus, setIngestStatus] = useState<string | null>(null);
  const [ingestLoading, setIngestLoading] = useState(false);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [analysisError, setAnalysisError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(`${API}/api/filings/${encodeURIComponent(company)}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data: { years?: number[] } | null) => {
        if (!cancelled && data?.years?.length) setCachedYears(data.years);
        else if (!cancelled) setCachedYears([]);
      })
      .catch(() => {
        if (!cancelled) setCachedYears([]);
      });
    return () => {
      cancelled = true;
    };
  }, [company]);

  async function ingest() {
    setIngestLoading(true);
    setIngestStatus(null);
    try {
      const r = await fetch(
        `${API}/api/ingest?ticker=${encodeURIComponent(company)}`,
        { method: "POST" }
      );
      const data = r.ok ? await r.json() : null;
      if (!r.ok) {
        setIngestStatus(data?.detail ?? (await r.text()) ?? "Ingest failed");
        return;
      }
      setIngestStatus(
        `[${data.ticker ?? company}] Ingested ${data.chunks} chunks from ${data.ingested_files} filings (${data.storage?.backend}).`
      );
    } catch {
      setIngestStatus("Could not reach API. Is the backend running on :8000?");
    } finally {
      setIngestLoading(false);
    }
  }

  async function runAnalysis() {
    setAnalysisLoading(true);
    setAnalysisError(null);
    try {
      const r = await fetch(`${API}/api/analysis/${company}`);
      if (!r.ok) {
        const err = await r.json().catch(async () => ({ detail: await r.text() }));
        throw new Error(err.detail ?? r.statusText);
      }
      const data = await r.json();
      setAnalysis({
        what_changed: data.what_changed,
        risk_evolution: data.risk_evolution,
        tone_shift: data.tone_shift,
        investor_memo: data.investor_memo,
        evidence_snippets: data.evidence_snippets ?? [],
      });
    } catch (e) {
      setAnalysis(null);
      setAnalysisError(e instanceof Error ? e.message : "Analysis failed");
    } finally {
      setAnalysisLoading(false);
    }
  }

  const sharedEvidence = analysis?.evidence_snippets ?? [];

  return (
    <main className="min-h-screen bg-gradient-to-b from-[#070a0f] via-[#0a0f18] to-[#070a0f]">
      <div className="mx-auto max-w-6xl px-4 py-12 sm:px-6 lg:px-8">
        <header className="mb-10 flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="text-xs font-mono uppercase tracking-[0.2em] text-accent mb-2">
              Hackathon MVP
            </p>
            <h1 className="text-4xl sm:text-5xl font-bold tracking-tight text-white">
              Filings Memory Agent
            </h1>
            <p className="mt-3 max-w-2xl text-slate-400 text-lg leading-relaxed">
              Long-context AI that compares years of 10-K filings and finds what
              changed.
            </p>
            <div className="mt-4 flex flex-wrap gap-2">
              {["HydraDB Context", "Pipeshift AI", "Render Deployable"].map((b) => (
                <span
                  key={b}
                  className="rounded-full border border-surface-border bg-surface-card px-3 py-1 text-[11px] text-slate-400"
                >
                  {b}
                </span>
              ))}
            </div>
          </div>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <div className="flex flex-col gap-1">
              <label className="text-[10px] uppercase text-slate-500">Company</label>
              <select
                value={company}
                onChange={(e) => setCompany(e.target.value)}
                className="rounded-xl border border-surface-border bg-surface-card px-4 py-2.5 text-sm text-white focus:outline-none focus:ring-2 focus:ring-accent/40"
              >
                {companies.map((c) => (
                  <option key={c.value} value={c.value}>
                    {c.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-[10px] uppercase text-slate-500">Filings</label>
              <div className="flex flex-wrap gap-1">
                {(cachedYears.length ? cachedYears : ["—"]).map((y) => (
                  <span
                    key={y}
                    className="rounded-lg bg-slate-800/80 px-2 py-1 text-xs text-slate-300"
                  >
                    {y}
                  </span>
                ))}
              </div>
            </div>
          </div>
        </header>

        <FinancialMetrics ticker={company} />

        <div className="mb-10 flex flex-wrap gap-3">
          <button
            type="button"
            onClick={ingest}
            disabled={ingestLoading}
            className="rounded-xl border border-accent/40 bg-accent/10 px-5 py-2.5 text-sm font-medium text-accent hover:bg-accent/15 disabled:opacity-50"
          >
            {ingestLoading ? "Ingesting…" : `Ingest ${company} filings`}
          </button>
          <button
            type="button"
            onClick={runAnalysis}
            disabled={analysisLoading}
            className="rounded-xl bg-white/10 px-5 py-2.5 text-sm font-medium text-white border border-surface-border hover:bg-white/15 disabled:opacity-50"
          >
            {analysisLoading ? "Running…" : "Run analysis"}
          </button>
        </div>
        {ingestStatus && (
          <p className="mb-8 text-sm text-slate-400 font-mono">{ingestStatus}</p>
        )}
        {analysisError && (
          <p className="mb-8 text-sm text-red-400">{analysisError}</p>
        )}

        <div className="grid gap-6 md:grid-cols-2">
          <InsightCard
            title="What Changed"
            body={analysis?.what_changed ?? null}
            loading={analysisLoading}
            evidence={sharedEvidence}
          />
          <InsightCard
            title="Risk Evolution"
            body={analysis?.risk_evolution ?? null}
            loading={analysisLoading}
            evidence={sharedEvidence}
          />
          <InsightCard
            title="Management Tone Shift"
            body={analysis?.tone_shift ?? null}
            loading={analysisLoading}
            evidence={sharedEvidence}
          />
          <InsightCard
            title="Investor Memo"
            body={analysis?.investor_memo ?? null}
            loading={analysisLoading}
            evidence={sharedEvidence}
          />
        </div>

        <div className="mt-8">
          <AskBox ticker={company} />
        </div>

        <footer className="mt-16 text-center text-[11px] text-slate-600">
          Demo uses cached SEC-style HTML under{" "}
          <code className="text-slate-500">backend/data/raw</code>. Configure{" "}
          <code className="text-slate-500">HYDRADB_*</code> and{" "}
          <code className="text-slate-500">PIPESHIFT_API_KEY</code> for live services.
        </footer>
      </div>
    </main>
  );
}
