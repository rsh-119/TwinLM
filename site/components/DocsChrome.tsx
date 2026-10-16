"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { ArrowLeft, ArrowRight, Menu } from "lucide-react";
import { DOCS_NAV, DOCS_PAGES } from "@/app/docs/nav";

const norm = (p: string) => (p.endsWith("/") ? p : `${p}/`);

/** usePathname() excludes basePath, so it compares directly with nav hrefs. */
function useCurrent() {
  return norm(usePathname() || "/");
}

function NavList({ onNavigate }: { onNavigate?: () => void }) {
  const current = useCurrent();
  return (
    <nav aria-label="Documentation">
      {DOCS_NAV.map((s) => (
        <div key={s.section} className="mb-7">
          <p className="label mb-2 text-ink">{s.section}</p>
          <ul className="border-l border-rule">
            {s.pages.map(([title, href]) => {
              const active = current === href;
              return (
                <li key={href}>
                  <Link
                    href={href}
                    onClick={onNavigate}
                    aria-current={active ? "page" : undefined}
                    className={`-ml-px block border-l py-1.5 pl-4 text-[0.9375rem] transition-colors ${
                      active ? "border-signal font-semibold text-ink" : "border-transparent text-ink-2 hover:text-ink"
                    }`}
                  >
                    {title}
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}

export function Sidebar() {
  return (
    <aside className="hidden lg:block">
      <div className="sticky top-16 max-h-[calc(100vh-4rem)] overflow-y-auto py-10 pr-6">
        <NavList />
      </div>
    </aside>
  );
}

export function MobileNav() {
  const [open, setOpen] = useState(false);
  const current = useCurrent();
  const title = DOCS_PAGES.find(([, href]) => href === current)?.[0] ?? "Documentation";
  return (
    <div className="border-b border-ink lg:hidden">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="label flex w-full items-center gap-3 px-4 py-3 text-sm sm:px-8"
      >
        <Menu size={18} strokeWidth={2} aria-hidden />
        {title}
      </button>
      {open && (
        <div className="max-h-[70vh] overflow-y-auto border-t border-rule px-4 pt-6 sm:px-8">
          <NavList onNavigate={() => setOpen(false)} />
        </div>
      )}
    </div>
  );
}

export function Pager() {
  const current = useCurrent();
  const i = DOCS_PAGES.findIndex(([, href]) => href === current);
  if (i < 0) return null;
  const prev = DOCS_PAGES[i - 1];
  const next = DOCS_PAGES[i + 1];
  return (
    <div className="mt-16 grid gap-4 border-t border-ink pt-6 sm:grid-cols-2">
      {prev ? (
        <Link href={prev[1]} className="group block py-2">
          <span className="label flex items-center gap-1.5 text-ink-2">
            <ArrowLeft size={14} strokeWidth={2} aria-hidden /> Previous
          </span>
          <span className="display mt-1 block text-xl group-hover:underline">{prev[0]}</span>
        </Link>
      ) : (
        <span />
      )}
      {next && (
        <Link href={next[1]} className="group block py-2 sm:text-right">
          <span className="label flex items-center gap-1.5 text-ink-2 sm:justify-end">
            Next <ArrowRight size={14} strokeWidth={2} aria-hidden />
          </span>
          <span className="display mt-1 block text-xl group-hover:underline">{next[0]}</span>
        </Link>
      )}
    </div>
  );
}

/** "On this page": built from the article's h2/h3 after render. */
export function Toc() {
  const current = useCurrent();
  const [items, setItems] = useState<{ id: string; text: string; level: number }[]>([]);
  const [active, setActive] = useState<string>("");

  useEffect(() => {
    const heads = Array.from(document.querySelectorAll<HTMLElement>("article h2[id], article h3[id]"));
    setItems(heads.map((h) => ({ id: h.id, text: h.textContent ?? "", level: h.tagName === "H2" ? 2 : 3 })));
    const obs = new IntersectionObserver(
      (entries) => {
        const visible = entries.filter((e) => e.isIntersecting);
        if (visible[0]) setActive(visible[0].target.id);
      },
      { rootMargin: "-80px 0px -70% 0px" },
    );
    heads.forEach((h) => obs.observe(h));
    return () => obs.disconnect();
  }, [current]);

  if (items.length < 2) return null;
  return (
    <aside className="hidden xl:block">
      <div className="sticky top-16 py-10">
        <p className="label mb-3">On this page</p>
        <ul className="space-y-1.5 border-l border-rule">
          {items.map((it) => (
            <li key={it.id}>
              <a
                href={`#${it.id}`}
                className={`-ml-px block border-l text-sm leading-snug transition-colors ${
                  it.level === 3 ? "pl-7" : "pl-4"
                } ${active === it.id ? "border-signal text-ink" : "border-transparent text-ink-2 hover:text-ink"}`}
              >
                {it.text}
              </a>
            </li>
          ))}
        </ul>
      </div>
    </aside>
  );
}
