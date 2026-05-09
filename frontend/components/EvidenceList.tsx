export type EvidenceItem = {
  ticker?: string;
  year?: number;
  section?: string;
  chunk_id?: string;
  snippet?: string;
};

function formatSection(section?: string): string {
  if (!section || section === "unknown") return "—";
  return section
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export function EvidenceList({ items, compact }: { items: EvidenceItem[]; compact?: boolean }) {
  if (!items?.length) {
    return (
      <p className="text-xs text-slate-500 mt-3">
        No evidence snippets yet — run analysis or ask a question.
      </p>
    );
  }

  return (
    <ul className={`${compact ? "mt-2 space-y-2" : "mt-3 space-y-3"}`}>
      {items.map((e, i) => (
        <li
          key={`${e.chunk_id ?? i}-${e.year}`}
          className="rounded-xl border border-surface-border bg-gradient-to-b from-black/25 to-black/10 overflow-hidden"
        >
          <div className="flex flex-wrap items-stretch gap-0 border-b border-surface-border/80 bg-slate-900/50 px-3 py-2">
            <div className="flex min-w-0 flex-1 flex-wrap gap-2">
              {e.year != null && (
                <div className="flex items-center gap-1.5 rounded-md bg-accent/15 px-2 py-1 ring-1 ring-accent/25">
                  <span className="text-[9px] font-medium uppercase tracking-wider text-slate-500">
                    Year
                  </span>
                  <span className="text-xs font-semibold tabular-nums text-accent">{e.year}</span>
                </div>
              )}
              <div className="flex items-center gap-1.5 rounded-md bg-slate-800/90 px-2 py-1">
                <span className="text-[9px] font-medium uppercase tracking-wider text-slate-500">
                  Section
                </span>
                <span className="max-w-[10rem] truncate text-[11px] font-medium uppercase tracking-wide text-slate-300">
                  {formatSection(e.section)}
                </span>
              </div>
              {e.chunk_id && (
                <div className="flex min-w-0 items-center gap-1.5 rounded-md bg-black/30 px-2 py-1">
                  <span className="shrink-0 text-[9px] font-medium uppercase tracking-wider text-slate-600">
                    Source
                  </span>
                  <span className="truncate font-mono text-[10px] text-slate-500" title={e.chunk_id}>
                    {e.chunk_id}
                  </span>
                </div>
              )}
            </div>
          </div>
          <div className="px-3 py-2.5">
            <p className="text-[13px] leading-relaxed text-slate-300 border-l-2 border-accent/40 pl-3">
              {e.snippet}
            </p>
          </div>
        </li>
      ))}
    </ul>
  );
}
