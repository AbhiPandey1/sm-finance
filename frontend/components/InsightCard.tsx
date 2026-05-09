import { EvidenceList, type EvidenceItem } from "./EvidenceList";
import { MarkdownBody } from "./MarkdownBody";

type Props = {
  title: string;
  body: string | null;
  loading?: boolean;
  evidence?: EvidenceItem[];
};

export function InsightCard({ title, body, loading, evidence }: Props) {
  return (
    <div className="rounded-2xl border border-surface-border bg-surface-card p-6 shadow-lg shadow-black/40 flex flex-col h-full">
      <h3 className="text-lg font-semibold text-white mb-3 tracking-tight">{title}</h3>
      {loading ? (
        <div className="space-y-2 animate-pulse">
          <div className="h-3 bg-slate-800 rounded w-11/12" />
          <div className="h-3 bg-slate-800 rounded w-full" />
          <div className="h-3 bg-slate-800 rounded w-4/5" />
        </div>
      ) : (
        <div className="text-sm text-slate-300 leading-relaxed flex-1 min-h-0 [&_.markdown-body]:max-w-none [&_h4]:text-white [&_h5]:text-slate-100">
          {body ? (
            <div className="rounded-xl bg-black/15 border border-white/[0.06] px-4 py-3">
              <MarkdownBody content={body} />
            </div>
          ) : (
            <span className="text-slate-500">—</span>
          )}
        </div>
      )}
      {evidence && evidence.length > 0 && (
        <div className="mt-5 border-t border-surface-border pt-4">
          <p className="text-[10px] uppercase tracking-[0.15em] text-slate-500 mb-2 font-medium">
            Evidence
          </p>
          <EvidenceList items={evidence} compact />
        </div>
      )}
    </div>
  );
}
