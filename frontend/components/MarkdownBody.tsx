"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type Props = {
  content: string;
  className?: string;
};

/**
 * Renders markdown (GFM) with dark-theme styling for assistant/analysis copy.
 */
export function MarkdownBody({ content, className = "" }: Props) {
  const trimmed = content?.trim() ?? "";
  if (!trimmed) return null;

  return (
    <div className={`markdown-body ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children }) => (
            <h4 className="text-base font-semibold text-white mt-4 mb-2 first:mt-0">{children}</h4>
          ),
          h2: ({ children }) => (
            <h4 className="text-base font-semibold text-white mt-4 mb-2 first:mt-0">{children}</h4>
          ),
          h3: ({ children }) => (
            <h5 className="text-sm font-semibold text-white mt-3 mb-1.5">{children}</h5>
          ),
          h4: ({ children }) => (
            <h6 className="text-sm font-semibold text-slate-100 mt-2 mb-1">{children}</h6>
          ),
          p: ({ children }) => (
            <p className="mb-3 last:mb-0 leading-relaxed text-slate-200">{children}</p>
          ),
          ul: ({ children }) => (
            <ul className="my-2 ml-4 list-disc space-y-2 marker:text-accent/70 text-slate-200">{children}</ul>
          ),
          ol: ({ children }) => (
            <ol className="my-2 ml-4 list-decimal space-y-2 marker:text-slate-400 text-slate-200">{children}</ol>
          ),
          li: ({ children }) => (
            <li className="leading-relaxed [&>p]:mb-2 [&>p:last-child]:mb-0 pl-0.5">{children}</li>
          ),
          strong: ({ children }) => <strong className="font-semibold text-white">{children}</strong>,
          em: ({ children }) => <em className="italic text-slate-100">{children}</em>,
          a: ({ href, children }) => (
            <a
              href={href}
              className="text-accent underline-offset-2 hover:underline"
              target="_blank"
              rel="noopener noreferrer"
            >
              {children}
            </a>
          ),
          hr: () => <hr className="my-4 border-surface-border" />,
          blockquote: ({ children }) => (
            <blockquote className="border-l-2 border-accent/50 pl-3 my-3 text-slate-300 italic">{children}</blockquote>
          ),
          code: ({ className, children }) => {
            const isBlock = Boolean(className);
            if (!isBlock) {
              return (
                <code className="rounded bg-black/40 px-1.5 py-0.5 text-[0.85em] font-mono text-accent/95">
                  {children}
                </code>
              );
            }
            return <code className={className}>{children}</code>;
          },
          pre: ({ children }) => (
            <pre className="my-2 overflow-x-auto rounded-lg bg-black/40 border border-surface-border p-3 text-xs font-mono text-slate-200">
              {children}
            </pre>
          ),
          table: ({ children }) => (
            <div className="my-3 overflow-x-auto rounded-lg border border-surface-border">
              <table className="min-w-full border-collapse text-left text-xs">{children}</table>
            </div>
          ),
          thead: ({ children }) => <thead className="bg-slate-900/90 text-slate-300">{children}</thead>,
          tbody: ({ children }) => <tbody className="divide-y divide-surface-border">{children}</tbody>,
          tr: ({ children }) => <tr className="border-surface-border">{children}</tr>,
          th: ({ children }) => (
            <th className="px-3 py-2 font-medium border-b border-surface-border">{children}</th>
          ),
          td: ({ children }) => <td className="px-3 py-2 text-slate-300">{children}</td>,
        }}
      >
        {trimmed}
      </ReactMarkdown>
    </div>
  );
}
