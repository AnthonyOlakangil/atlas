import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import Markdown from "react-markdown";
import { Empty, StatusChip } from "../components/ui";
import { ApiError, fetchBrief } from "../lib/api";

/** Wrap glossary terms found in plain text with a hover-tooltip span. */
export function annotate(text: string, glossary: Record<string, string>): ReactNode[] {
  const terms = Object.keys(glossary).sort((a, b) => b.length - a.length);
  if (terms.length === 0) return [text];
  const pattern = new RegExp(
    `(${terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`,
    "gi",
  );
  return text.split(pattern).map((part, i) => {
    const def = glossary[part.toLowerCase()] ?? glossary[part];
    return def ? (
      <span key={i} className="glossary-term" title={def} data-testid="glossary-term">
        {part}
      </span>
    ) : (
      part
    );
  });
}

export default function BriefReader() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["brief"],
    queryFn: fetchBrief,
    retry: false,
  });
  if (isLoading) return <Empty message="Loading brief…" />;
  if (error instanceof ApiError && error.status === 404)
    return <Empty message="No brief yet." hint="Run: uv run atlas cycle" />;
  if (!data) return <Empty message="Backend unreachable" hint="Run: uv run atlas serve" />;

  const gloss = Object.fromEntries(
    Object.entries(data.glossary).map(([k, v]) => [k.toLowerCase(), v]),
  );
  const wrap = (children: ReactNode): ReactNode =>
    Array.isArray(children)
      ? children.map((c) => (typeof c === "string" ? annotate(c, gloss) : c))
      : typeof children === "string"
        ? annotate(children, gloss)
        : children;

  return (
    <>
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold">Brief — {data.date}</h1>
        <div className="flex items-center gap-2 text-xs text-zinc-500">
          critic: <StatusChip status={data.status} /> ({data.critic_rounds} round
          {data.critic_rounds === 1 ? "" : "s"})
        </div>
      </div>
      <article className="card prose-headings:font-bold prose-headings:text-zinc-100 space-y-3 text-sm leading-relaxed text-zinc-300">
        <Markdown
          components={{
            h1: ({ children }) => <h1 className="text-lg font-bold text-zinc-100">{children}</h1>,
            h2: ({ children }) => (
              <h2 className="mt-4 border-b border-zinc-800 pb-1 text-base font-bold text-zinc-100">
                {children}
              </h2>
            ),
            h3: ({ children }) => <h3 className="mt-2 font-semibold text-zinc-200">{children}</h3>,
            p: ({ children }) => <p>{wrap(children)}</p>,
            li: ({ children }) => <li className="ml-5 list-disc">{wrap(children)}</li>,
            table: ({ children }) => <table className="text-xs">{children}</table>,
            code: ({ children }) => (
              <code className="rounded bg-zinc-800 px-1 text-sky-200">{children}</code>
            ),
          }}
        >
          {data.markdown}
        </Markdown>
      </article>
      <p className="caption">
        Dotted-underline terms are glossary entries — hover for the definition.
      </p>
    </>
  );
}
