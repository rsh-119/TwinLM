import type { Metadata } from "next";
import SiteHeader from "@/components/SiteHeader";
import { MobileNav, Pager, Sidebar, Toc } from "@/components/DocsChrome";

export const metadata: Metadata = {
  title: { default: "TwinLM documentation", template: "%s · TwinLM docs" },
  description: "Run, configure and operate TwinLM, the open-source OpenAI-compatible gateway that cuts AI spend.",
};

export default function DocsLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <SiteHeader wide />
      <MobileNav />
      <div className="mx-auto grid max-w-[1440px] gap-10 px-4 sm:px-8 lg:grid-cols-[240px_minmax(0,1fr)] xl:grid-cols-[240px_minmax(0,1fr)_200px]">
        <Sidebar />
        <main id="main" className="min-w-0 py-10 lg:py-14">
          <article className="docs max-w-[72ch]">{children}</article>
          <div className="max-w-[72ch]">
            <Pager />
          </div>
        </main>
        <Toc />
      </div>
      <footer className="border-t border-ink">
        <div className="mx-auto flex max-w-[1440px] flex-wrap gap-x-8 gap-y-2 px-4 py-6 text-sm text-ink-2 sm:px-8">
          <span>TwinLM · MIT licensed</span>
          <a href="https://github.com/rsh-119/TwinLM/issues" className="hover:text-ink hover:underline sm:ml-auto">
            Found a mistake? Open an issue
          </a>
        </div>
      </footer>
    </>
  );
}
