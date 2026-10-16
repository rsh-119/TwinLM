// Sidebar order is reading order; the pager at the bottom of each page follows it.
export const DOCS_NAV: { section: string; pages: [title: string, href: string][] }[] = [
  {
    section: "Get started",
    pages: [
      ["Introduction", "/docs/"],
      ["Quickstart", "/docs/quickstart/"],
      ["Deployment", "/docs/deploy/"],
    ],
  },
  {
    section: "Use it",
    pages: [
      ["Integrate your apps", "/docs/integrate/"],
      ["OpenAI compatibility", "/docs/compatibility/"],
      ["Providers", "/docs/providers/"],
      ["Routing and fallback", "/docs/routing/"],
    ],
  },
  {
    section: "Cut costs",
    pages: [
      ["Caching", "/docs/caching/"],
      ["Output caps", "/docs/output-caps/"],
      ["Usage and savings", "/docs/usage-and-savings/"],
      ["Keys and rate limits", "/docs/keys/"],
      ["Budgets and alerts", "/docs/budgets/"],
      ["Teams and members", "/docs/teams/"],
    ],
  },
  {
    section: "Configuration",
    pages: [
      ["gateway.toml reference", "/docs/config/"],
      ["Environment variables", "/docs/environment/"],
    ],
  },
  {
    section: "API reference",
    pages: [
      ["Gateway API (/v1)", "/docs/api/"],
      ["Admin API", "/docs/admin-api/"],
    ],
  },
  {
    section: "Optional features",
    pages: [
      ["Conversations and memories", "/docs/stateful/"],
      ["Guardrails and structured output", "/docs/guardrails/"],
    ],
  },
  {
    section: "Operate",
    pages: [
      ["Operations runbook", "/docs/operations/"],
      ["Monitoring", "/docs/monitoring/"],
      ["Running several instances", "/docs/scaling/"],
      ["Performance and limits", "/docs/performance/"],
      ["Troubleshooting", "/docs/troubleshooting/"],
    ],
  },
];

export const DOCS_PAGES = DOCS_NAV.flatMap((s) => s.pages);
