"use client";

import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

type AnnualRow = {
  period_end: string;
  fiscal_year?: number | null;
  revenue: number | null;
  gross_profit?: number | null;
  operating_income?: number | null;
  net_income?: number | null;
  eps_diluted?: number | null;
  gross_margin?: number | null;
  operating_margin?: number | null;
  net_margin?: number | null;
  operating_cash_flow?: number | null;
  free_cash_flow?: number | null;
  total_debt?: number | null;
  shareholders_equity?: number | null;
  roe?: number | null;
};

type MetricsPayload = {
  ticker: string;
  entity_name?: string | null;
  currency?: string;
  source?: string;
  annual?: AnnualRow[];
  latest?: AnnualRow | null;
  yoy_revenue_growth?: number | null;
  error?: string;
};

function formatUsdCompact(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  const abs = Math.abs(n);
  if (abs >= 1e12) return `$${(n / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
  return `$${n.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

function formatPct(n: number | null | undefined, digits = 1): string {
  if (n == null || Number.isNaN(n)) return "—";
  return `${(n * 100).toFixed(digits)}%`;
}

function formatEPS(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

type Props = { ticker: string };

export function FinancialMetrics({ ticker }: Props) {
  const [data, setData] = useState<MetricsPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setErr(null);
    fetch(`${API}/api/metrics/${encodeURIComponent(ticker)}`)
      .then(async (r) => {
        const j = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(j.detail ?? r.statusText);
        return j as MetricsPayload;
      })
      .then((j) => {
        if (!cancelled) {
          setData(j);
          if (j.error) setErr(j.error);
        }
      })
      .catch((e) => {
        if (!cancelled) {
          setData(null);
          setErr(e instanceof Error ? e.message : "Failed to load metrics");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [ticker]);

  const latest = data?.latest;
  const annual = data?.annual ?? [];

  return (
    <section className="mb-10 rounded-2xl border border-surface-border bg-surface-card p-6 shadow-lg shadow-black/40">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between mb-6">
        <div>
          <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">
            SEC XBRL · annual 10-K
          </p>
          <h2 className="text-xl font-semibold text-white">Financial metrics</h2>
          <p className="text-xs text-slate-500 mt-1 max-w-xl">
            Pulled live from SEC Company Facts when you open this page — independent of{" "}
            <span className="text-slate-400">Ingest</span> /{" "}
            <span className="text-slate-400">Run analysis</span> (those use your cached HTML).
          </p>
          {data?.entity_name && (
            <p className="text-sm text-slate-400 mt-1">{data.entity_name}</p>
          )}
        </div>
        {typeof data?.yoy_revenue_growth === "number" && !Number.isNaN(data.yoy_revenue_growth) && (
          <div className="rounded-xl bg-accent/10 border border-accent/30 px-4 py-2 text-sm">
            <span className="text-slate-400">YoY revenue </span>
            <span className="text-accent font-medium">
              {formatPct(data.yoy_revenue_growth, 1)}
            </span>
          </div>
        )}
      </div>

      {loading && (
        <div className="space-y-3 animate-pulse">
          <div className="h-10 bg-slate-800 rounded-lg" />
          <div className="h-32 bg-slate-800/80 rounded-lg" />
        </div>
      )}

      {!loading && err && (
        <p className="text-sm text-red-400">{err}</p>
      )}

      {!loading && !err && latest && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
            <Kpi label="Revenue (FY)" value={formatUsdCompact(latest.revenue)} />
            <Kpi label="Net income" value={formatUsdCompact(latest.net_income)} />
            <Kpi label="EPS (diluted)" value={formatEPS(latest.eps_diluted)} />
            <Kpi label="Operating cash flow" value={formatUsdCompact(latest.operating_cash_flow)} />
            <Kpi label="Gross margin" value={formatPct(latest.gross_margin)} />
            <Kpi label="Operating margin" value={formatPct(latest.operating_margin)} />
            <Kpi label="Net margin" value={formatPct(latest.net_margin)} />
            <Kpi label="ROE" value={formatPct(latest.roe)} />
            <Kpi label="Total debt" value={formatUsdCompact(latest.total_debt)} />
            <Kpi label="Shareholders’ equity" value={formatUsdCompact(latest.shareholders_equity)} />
            <Kpi
              label="Free cash flow"
              value={
                latest.free_cash_flow != null
                  ? formatUsdCompact(latest.free_cash_flow)
                  : "—"
              }
            />
            <Kpi
              label="Period end"
              value={latest.period_end || "—"}
              sub={latest.fiscal_year != null ? `SEC FY ${latest.fiscal_year}` : undefined}
            />
          </div>

          {annual.length > 0 && (
            <div className="overflow-x-auto rounded-xl border border-surface-border">
              <table className="min-w-full text-left text-xs text-slate-300">
                <thead className="bg-slate-900/80 text-[10px] uppercase tracking-wider text-slate-500">
                  <tr>
                    <th className="px-3 py-2 font-medium">FY</th>
                    <th className="px-3 py-2 font-medium">End</th>
                    <th className="px-3 py-2 font-medium text-right">Revenue</th>
                    <th className="px-3 py-2 font-medium text-right">Net inc.</th>
                    <th className="px-3 py-2 font-medium text-right">EPS</th>
                    <th className="px-3 py-2 font-medium text-right">Gross</th>
                    <th className="px-3 py-2 font-medium text-right">Op.</th>
                    <th className="px-3 py-2 font-medium text-right">Net</th>
                    <th className="px-3 py-2 font-medium text-right">OCF</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-surface-border">
                  {annual.map((row) => (
                    <tr key={row.period_end} className="hover:bg-white/[0.02]">
                      <td className="px-3 py-2 text-slate-400 whitespace-nowrap">
                        {row.fiscal_year ?? "—"}
                      </td>
                      <td className="px-3 py-2 whitespace-nowrap">{row.period_end}</td>
                      <td className="px-3 py-2 text-right font-mono">
                        {formatUsdCompact(row.revenue)}
                      </td>
                      <td className="px-3 py-2 text-right font-mono">
                        {formatUsdCompact(row.net_income)}
                      </td>
                      <td className="px-3 py-2 text-right font-mono">
                        {formatEPS(row.eps_diluted)}
                      </td>
                      <td className="px-3 py-2 text-right">{formatPct(row.gross_margin)}</td>
                      <td className="px-3 py-2 text-right">{formatPct(row.operating_margin)}</td>
                      <td className="px-3 py-2 text-right">{formatPct(row.net_margin)}</td>
                      <td className="px-3 py-2 text-right font-mono">
                        {formatUsdCompact(row.operating_cash_flow)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <p className="mt-4 text-[10px] text-slate-600">
            Data: SEC Company Facts (US-GAAP), annual fiscal periods. Set{" "}
            <code className="text-slate-500">SEC_USER_AGENT</code> on the server with your contact
            info for production traffic.
          </p>
        </>
      )}

      {!loading && !err && !latest && data && (
        <p className="text-sm text-slate-400">No annual metrics parsed for this ticker.</p>
      )}
    </section>
  );
}

function Kpi({
  label,
  value,
  sub,
}: {
  label: string;
  value: string;
  sub?: string;
}) {
  return (
    <div className="rounded-xl border border-surface-border bg-slate-900/40 px-3 py-2.5">
      <p className="text-[10px] uppercase tracking-wider text-slate-500">{label}</p>
      <p className="text-sm font-semibold text-white mt-0.5">{value}</p>
      {sub && <p className="text-[10px] text-slate-500 mt-0.5">{sub}</p>}
    </div>
  );
}
