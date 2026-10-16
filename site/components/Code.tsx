"use client";

import { useRef, useState, type ReactNode } from "react";
import { Check, Copy } from "lucide-react";

export function CodeBlock({ title, children, tone = "paper" }: {
  title?: string;
  children: ReactNode;
  tone?: "paper" | "signal";
}) {
  const pre = useRef<HTMLPreElement>(null);
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    if (!pre.current) return;
    try {
      await navigator.clipboard.writeText(pre.current.innerText);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard blocked: the text stays selectable */
    }
  };
  const frame = tone === "signal" ? "border-on-signal" : "border-ink bg-paper";
  return (
    <div className={`border ${frame}`}>
      <div className={`flex items-center justify-between border-b px-4 py-2 ${tone === "signal" ? "border-on-signal" : "border-ink"}`}>
        <span className="font-code text-xs">{title}</span>
        <button
          type="button"
          onClick={copy}
          className="label inline-flex items-center gap-1.5 text-xs hover:underline"
          aria-label={copied ? "Copied" : `Copy ${title ?? "code"}`}
        >
          {copied ? <Check size={14} strokeWidth={2} /> : <Copy size={14} strokeWidth={2} />}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre ref={pre} className="overflow-x-auto p-4 font-code text-[0.8125rem] leading-[1.7]">
        {children}
      </pre>
    </div>
  );
}

export function CodeTabs({ tabs }: { tabs: { label: string; title: string; code: ReactNode }[] }) {
  const [active, setActive] = useState(0);
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const onKey = (e: React.KeyboardEvent, i: number) => {
    const d = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
    if (!d) return;
    e.preventDefault();
    const next = (i + d + tabs.length) % tabs.length;
    setActive(next);
    refs.current[next]?.focus();
  };
  return (
    <div>
      <div role="tablist" aria-label="Language" className="flex border-b border-ink">
        {tabs.map((t, i) => (
          <button
            key={t.label}
            ref={(el) => {
              refs.current[i] = el;
            }}
            role="tab"
            id={`tab-${i}`}
            aria-selected={i === active}
            aria-controls={`panel-${i}`}
            tabIndex={i === active ? 0 : -1}
            onClick={() => setActive(i)}
            onKeyDown={(e) => onKey(e, i)}
            className={`label -mb-px px-4 py-2.5 text-sm transition-colors ${
              i === active ? "bg-ink text-paper" : "text-ink-2 hover:text-ink"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      {tabs.map((t, i) => (
        <div key={t.label} role="tabpanel" id={`panel-${i}`} aria-labelledby={`tab-${i}`} hidden={i !== active}
          className="-mt-px">
          <CodeBlock title={t.title}>{t.code}</CodeBlock>
        </div>
      ))}
    </div>
  );
}
