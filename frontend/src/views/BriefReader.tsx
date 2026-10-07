import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Empty, StatusChip } from "../components/ui";
import { ApiError, fetchBrief } from "../lib/api";
import { annotate } from "../lib/annotate";

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
          remarkPlugins={[remarkGfm]}
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
            table: ({ children }) => (
              <div className="my-2 overflow-x-auto">
                <table className="w-full border-collapse text-xs">{children}</table>
              </div>
            ),
            thead: ({ children }) => <thead className="text-zinc-400">{children}</thead>,
            th: ({ children }) => (
              <th className="border border-zinc-800 px-2 py-1 text-left font-semibold">{children}</th>
            ),
            td: ({ children }) => (
              <td className="border border-zinc-800 px-2 py-1">{wrap(children)}</td>
            ),
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
