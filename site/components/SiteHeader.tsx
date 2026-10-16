import Link from "next/link";

export const REPO = "https://github.com/rsh-119/TwinLM";

const NAV: [string, string][] = [
  ["Savings", "/#savings"],
  ["Integrate", "/#integrate"],
  ["Deploy", "/#deploy"],
  ["Pricing", "/#pricing"],
  ["Docs", "/docs/"],
];

export default function SiteHeader({ wide = false }: { wide?: boolean }) {
  return (
    <header className="sticky top-0 z-40 border-b border-ink bg-paper/95 backdrop-blur-sm">
      <nav aria-label="Main"
        className={`mx-auto flex h-16 items-center gap-8 px-4 sm:px-8 ${wide ? "max-w-[1440px]" : "max-w-[1280px]"}`}>
        <Link href="/" className="figure text-[1.75rem] leading-none">
          TwinLM
        </Link>
        <div className="hidden items-center gap-7 md:flex">
          {NAV.map(([label, href]) => (
            <Link key={href} href={href} className="label text-sm text-ink-2 hover:text-ink">
              {label}
            </Link>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-5">
          <Link href="/docs/" className="label text-sm hover:underline md:hidden">
            Docs
          </Link>
          <a href={REPO} className="label hidden text-sm hover:underline sm:inline">
            GitHub
          </a>
          <Link href="/docs/quickstart/"
            className="label inline-flex h-10 items-center bg-ink px-4 text-sm text-paper transition-colors hover:bg-signal hover:text-on-signal">
            Get started
          </Link>
        </div>
      </nav>
    </header>
  );
}
