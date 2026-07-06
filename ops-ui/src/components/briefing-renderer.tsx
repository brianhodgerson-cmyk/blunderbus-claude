"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * Strip Obsidian-specific syntax that doesn't render in plain markdown:
 *  - Collapsed callouts (`> [!question]- ...`) — these are "details" sections
 *    the user can expand in Obsidian; we don't show them inline.
 *  - Footer `<sub>` timing line.
 */
function cleanForRender(md: string): string {
  const lines = md.split("\n");
  const out: string[] = [];
  let inCallout = false;
  for (const raw of lines) {
    const line = raw;
    // Detect start of any callout block
    if (/^>\s*\[!/.test(line)) {
      inCallout = true;
      continue;
    }
    // Continuation of a callout (any line starting with >)
    if (inCallout) {
      if (line.trim() === "" || !line.startsWith(">")) {
        inCallout = false;
        if (line.trim() === "") continue; // swallow trailing blank
      } else {
        continue;
      }
    }
    // Strip the footer timing
    if (/^<sub>/.test(line.trim())) continue;
    if (line.trim() === "---") continue;
    out.push(line);
  }
  return out.join("\n").trim();
}

export function BriefingRenderer({ markdown }: { markdown: string }) {
  const cleaned = cleanForRender(markdown);
  return (
    <div className="prose-brief">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children }) => (
            <h2 className="mt-4 mb-2 text-base font-semibold text-slate-100">
              {children}
            </h2>
          ),
          h2: ({ children }) => (
            <h2 className="mt-4 mb-2 text-base font-semibold text-slate-100">
              {children}
            </h2>
          ),
          h3: ({ children }) => (
            <h3 className="mt-4 mb-2 text-base font-semibold text-slate-100">
              {children}
            </h3>
          ),
          p: ({ children }) => (
            <p className="mb-3 text-sm leading-relaxed text-slate-300">
              {children}
            </p>
          ),
          ol: ({ children }) => (
            <ol className="mb-3 ml-5 list-decimal space-y-1.5 text-sm text-slate-300 marker:text-slate-500">
              {children}
            </ol>
          ),
          ul: ({ children }) => (
            <ul className="mb-3 ml-5 list-disc space-y-1.5 text-sm text-slate-300 marker:text-slate-600">
              {children}
            </ul>
          ),
          li: ({ children }) => <li className="leading-relaxed">{children}</li>,
          strong: ({ children }) => (
            <strong className="font-semibold text-slate-100">{children}</strong>
          ),
          em: ({ children }) => <em className="text-slate-400">{children}</em>,
          code: ({ children }) => (
            <code className="rounded bg-slate-800 px-1 py-0.5 font-mono text-xs text-slate-200">
              {children}
            </code>
          ),
          a: ({ href, children }) => (
            <a
              href={href}
              className="text-sky-400 underline-offset-2 hover:underline"
            >
              {children}
            </a>
          ),
          hr: () => <hr className="my-3 border-slate-800" />,
        }}
      >
        {cleaned}
      </ReactMarkdown>
    </div>
  );
}
