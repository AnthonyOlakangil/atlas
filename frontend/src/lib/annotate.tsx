import type { ReactNode } from "react";

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
