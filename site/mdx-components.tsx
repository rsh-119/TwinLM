import type { MDXComponents } from "mdx/types";
import type { ComponentPropsWithoutRef, ReactElement, ReactNode } from "react";
import Link from "next/link";
import { CodeBlock } from "@/components/Code";

function A({ href = "", children, ...rest }: ComponentPropsWithoutRef<"a">) {
  const cls = "font-medium text-ink underline decoration-signal underline-offset-[0.2em] hover:decoration-2";
  if (href.startsWith("/")) {
    return (
      <Link href={href} className={cls}>
        {children}
      </Link>
    );
  }
  const external = /^https?:/.test(href);
  return (
    <a href={href} className={cls} {...(external ? { target: "_blank", rel: "noreferrer" } : {})} {...rest}>
      {children}
    </a>
  );
}

function Pre({ children }: { children?: ReactNode }) {
  const code = children as ReactElement<{ className?: string; children?: ReactNode }>;
  const lang = code?.props?.className?.replace("language-", "") ?? "";
  return (
    <div className="my-6">
      <CodeBlock title={lang || "code"}>{code?.props?.children}</CodeBlock>
    </div>
  );
}

export function Note({ title = "Note", children }: { title?: string; children: ReactNode }) {
  return (
    <div className="my-6 border-t-2 border-ink bg-paper-2 px-5 py-4">
      <p className="label mb-1">{title}</p>
      <div className="text-[0.9375rem] leading-relaxed text-ink-2 [&>p]:my-1">{children}</div>
    </div>
  );
}

const components: MDXComponents = {
  h1: (p) => <h1 className="display mb-4 text-[clamp(2.25rem,4vw,3.25rem)]" {...p} />,
  h2: (p) => <h2 className="display mb-4 mt-14 scroll-mt-24 border-t border-ink pt-6 text-[1.875rem]" {...p} />,
  h3: (p) => <h3 className="display mb-3 mt-10 scroll-mt-24 text-xl" {...p} />,
  p: (p) => <p className="my-4 leading-[1.7] text-ink-2 [&>strong]:text-ink" {...p} />,
  ul: (p) => <ul className="my-4 list-disc space-y-1.5 pl-6 leading-[1.7] text-ink-2 marker:text-ink" {...p} />,
  ol: (p) => <ol className="my-4 list-decimal space-y-1.5 pl-6 leading-[1.7] text-ink-2 marker:text-ink" {...p} />,
  strong: (p) => <strong className="font-semibold text-ink" {...p} />,
  a: A,
  code: (p) => <code className="bg-paper-2 px-1 py-0.5 font-code text-[0.85em] text-ink" {...p} />,
  pre: Pre,
  blockquote: (p) => <blockquote className="my-6 border-y border-ink py-3 text-ink" {...p} />,
  hr: () => <hr className="my-10 border-ink" />,
  table: (p) => (
    <div className="my-6 overflow-x-auto">
      <table className="w-full border-collapse text-left text-[0.9375rem]" {...p} />
    </div>
  ),
  th: (p) => <th className="label border-b-2 border-ink py-2 pr-6 align-bottom text-ink" {...p} />,
  td: (p) => <td className="border-b border-rule py-2.5 pr-6 align-top leading-relaxed text-ink-2" {...p} />,
  Note,
};

export function useMDXComponents(): MDXComponents {
  return components;
}
