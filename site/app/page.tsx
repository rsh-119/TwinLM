import type { ReactNode } from "react";
import { ArrowUpRight, Plus } from "lucide-react";
import Estimator from "@/components/Estimator";
import { CodeBlock, CodeTabs } from "@/components/Code";
import SiteHeader, { REPO } from "@/components/SiteHeader";

const CONTACT = `${REPO}/issues/new?title=${encodeURIComponent("Managed setup enquiry")}&body=${encodeURIComponent(
  "Company:\nWhat you use AI for:\nApprox. monthly AI spend:\nWhere you host:",
)}`;

/* code tokens: two inks only */
const K = ({ children }: { children: ReactNode }) => <span className="font-semibold">{children}</span>;
const S = ({ children }: { children: ReactNode }) => <span className="text-signal-ink">{children}</span>;
const C = ({ children }: { children: ReactNode }) => <span className="text-ink-2">{children}</span>;

function Section({ id, title, aside, children, className = "" }: {
  id?: string;
  title: ReactNode;
  aside?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section id={id} className={`border-t border-ink ${className}`}>
      <div className="mx-auto max-w-[1280px] px-4 py-20 sm:px-8 lg:py-28">
        <div className="grid gap-x-12 gap-y-6 lg:grid-cols-12">
          <h2 className="display text-[clamp(2rem,4vw,3.5rem)] lg:col-span-7">{title}</h2>
          {aside && <div className="text-lg leading-relaxed text-ink-2 lg:col-span-5 lg:pt-2">{aside}</div>}
        </div>
        <div className="mt-14">{children}</div>
      </div>
    </section>
  );
}

const LEVERS = [
  {
    figure: "$0",
    title: "for a repeated request",
    body: "Identical deterministic requests (temperature unset or 0, no tools, no streaming) are answered from cache and never reach the provider. Requests meant to vary always go to the model.",
    config: "[models.auto]\ncache_ttl_seconds = 3600",
  },
  {
    figure: "4,096",
    title: "output tokens, at most",
    body: "A default output cap stops one bad prompt from writing 30,000 tokens on your most expensive model. Each alias can set its own limit.",
    config: "default_max_output_tokens = 4096",
  },
  {
    figure: "1st",
    title: "try the cheapest model that fits",
    body: "Your apps ask for an alias like auto, fast or smart. TwinLM sends it to the cheapest model you allow, and falls over to the next provider when one is down.",
    config: '[models.fast]\ntargets = [\n  "groq/openai/gpt-oss-20b",\n  "gemini",\n]',
  },
  {
    figure: "1 key",
    title: "per app or team",
    body: "Every request is recorded with tokens, cost and latency per key, project and model. Monthly budgets per team, person or company refuse a request before it would cross the limit, with alerts at 80%.",
    config: 'PUT /admin/budgets\n{"scope": "team",\n "target": "support",\n "limit_usd": 500}',
  },
];

const FEATURES: [string, string][] = [
  ["OpenAI-compatible chat, embeddings and models, with streaming, tools and vision", "/v1/*"],
  ["Keys per app or team, rate limits and a daily spend limit", "/admin/keys"],
  ["Usage and cost per key, project, provider and model", "/admin/usage"],
  ["Model aliases with routing by priority, latency, cost or load", "gateway.toml"],
  ["Retries, circuit breakers and fallback across providers", "gateway.toml"],
  ["Exact-match cache for deterministic requests, TTL per alias", "/admin/cache"],
  ["Semantic cache, opt-in per alias, for questions asked in different words", "[cache.semantic]"],
  ["Budgets per team, person or company, with alerts at 80% and 100%", "/admin/budgets"],
  ["Output-token caps, globally or per alias", "gateway.toml"],
  ["Savings report against the model you would otherwise use", "/admin/savings"],
  ["Web console: sign-in, teams and members with roles, keys and spend per team", "/app"],
  ["Add providers from the console with just an API key, live, no restart", "/app/providers"],
  ["Day-zero models: any model name a provider ships works the same day", "any model"],
  ["Several instances behind a load balancer, sharing state through Redis", "REDIS_URL"],
  ["Your own models (Ollama, vLLM, fine-tunes) behind the same key", "custom endpoint"],
];

const FAQ: [string, string][] = [
  [
    "Do we have to change our code?",
    "Usually only the base URL and the API key. TwinLM speaks the OpenAI API, so the OpenAI SDKs, LangChain, LlamaIndex and most AI tools work unchanged. Parameters it doesn't support are rejected with a clear error instead of being silently ignored, so you find out on the first test call.",
  ],
  [
    "Where does our data go?",
    "TwinLM runs on your own servers or cloud. Prompts go only to the AI providers you configure, with your own provider keys. Nothing is sent to us.",
  ],
  [
    "Which providers work?",
    "Anthropic, OpenAI, Azure OpenAI, Mistral, DeepSeek, Groq, Google Gemini, xAI, Together, OpenRouter, Cerebras and any other OpenAI-compatible API, plus your own models through Ollama or vLLM. Add one from the console with just its API key; it works immediately.",
  ],
  [
    "Will caching give users stale answers?",
    "Only deterministic requests without tools, streaming or sensitive content are cached, and each alias sets its own expiry or turns caching off. Requests meant to vary always reach the model. The semantic cache, which also matches differently worded questions, is off unless you turn it on for a specific alias.",
  ],
  [
    "How much will we actually save?",
    "It depends on how much of your traffic repeats and how much cheaper your routed models are. The estimate at the top uses your assumptions; after a week, the savings report shows the measured figure. We don't quote numbers we haven't measured.",
  ],
  [
    "Is it production-ready?",
    "TwinLM is young. It has an automated test suite, versioned database migrations, backup and restore scripts, and has been load-tested on one instance with simulated providers. Several instances can share state through Redis; that is tested but not yet load-tested. If it will carry critical traffic, talk to us first.",
  ],
];

export default function Home() {
  return (
    <>
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:bg-ink focus:px-4 focus:py-2 focus:text-paper">
        Skip to content
      </a>

      <SiteHeader />

      <main id="main">
        {/* First spread: the estimate */}
        <section aria-label="Estimate your savings">
          <div className="mx-auto max-w-[1280px] px-4 pb-24 pt-14 sm:px-8 lg:pt-20">
            <Estimator />
          </div>
        </section>

        {/* The four levers */}
        <Section
          title="Where the money goes, and how TwinLM keeps it"
          aside="Most AI spend leaks through the same four holes. TwinLM closes each one at the gateway, so every app behind it benefits at once."
        >
          <div className="border-t border-ink">
            {LEVERS.map((l) => (
              <div key={l.title} className="grid gap-x-12 gap-y-4 border-b border-rule py-10 lg:grid-cols-12">
                <p className="figure text-[clamp(3.5rem,7vw,6rem)] lg:col-span-3">{l.figure}</p>
                <div className="lg:col-span-5">
                  <h3 className="display text-2xl">{l.title}</h3>
                  <p className="mt-3 max-w-[60ch] leading-relaxed text-ink-2">{l.body}</p>
                </div>
                <pre className="self-start overflow-x-auto border-l border-ink pl-4 font-code text-[0.8125rem] leading-[1.7] text-ink-2 lg:col-span-4">
                  {l.config}
                </pre>
              </div>
            ))}
          </div>
        </Section>

        {/* The orange spread: savings report */}
        <section id="savings" className="bg-signal text-on-signal">
          <div className="mx-auto grid max-w-[1280px] gap-x-12 gap-y-14 px-4 py-20 sm:px-8 lg:grid-cols-12 lg:py-28">
            <div className="lg:col-span-5">
              <h2 className="display text-[clamp(2rem,4vw,3.5rem)]">The number your finance team asks for</h2>
              <p className="mt-6 max-w-[48ch] text-lg leading-relaxed">
                Pick a baseline: the model you would send everything to without TwinLM. The savings report prices
                your real traffic both ways, per project, key or provider.
              </p>
              <p className="mt-4 max-w-[48ch] text-lg leading-relaxed">
                Requests with an unknown price are left out, and the report says so. No figure is ever padded.
              </p>
              <div className="mt-10">
                <CodeBlock title="terminal" tone="signal">
                  {`curl -G http://localhost:8000/admin/savings \\
  -d hours=720 -d group_by=project \\
  -H "Authorization: Bearer $ADMIN_TOKEN"`}
                </CodeBlock>
              </div>
            </div>

            <figure className="lg:col-span-7">
              <figcaption className="flex items-baseline justify-between border-b-2 border-on-signal pb-2">
                <span className="label">Savings report, last 30 days</span>
                <span className="label">Example output, illustrative numbers</span>
              </figcaption>

              <dl className="divide-y divide-on-signal/40">
                {[
                  ["Baseline cost", "openai/gpt-4o for every request", "$388.90"],
                  ["Actual cost", "what was served, after routing", "$61.42"],
                  ["Cache avoided", "9,120 repeated requests", "$14.07"],
                ].map(([k, d, v]) => (
                  <div key={k} className="flex items-baseline justify-between gap-6 py-4">
                    <dt>
                      <span className="text-lg font-semibold">{k}</span>
                      <span className="ml-3 hidden text-sm sm:inline">{d}</span>
                    </dt>
                    <dd className="figure text-4xl">{v}</dd>
                  </div>
                ))}
              </dl>

              <div className="mt-6 border-t-2 border-on-signal pt-4">
                <div className="flex items-baseline justify-between">
                  <span className="text-lg font-semibold">Saved</span>
                  <span className="text-sm">48,210 requests, all priced</span>
                </div>
                <p className="figure mt-2 text-[clamp(4.5rem,11vw,9rem)]">$327.48</p>
                <div className="mt-6" aria-hidden>
                  <div className="h-5 w-full bg-on-signal" />
                  <div className="mt-1.5 h-5 bg-paper" style={{ width: "15.8%" }} />
                  <div className="mt-2 flex justify-between text-sm">
                    <span>Actual, 15.8% of baseline</span>
                    <span>Baseline</span>
                  </div>
                </div>
              </div>
            </figure>
          </div>
        </section>

        {/* Integration */}
        <Section
          id="integrate"
          title="Change one URL. Keep every line of your code."
          aside={
            <>
              Your apps keep the SDKs and frameworks they already use: the OpenAI SDKs, LangChain, LlamaIndex, the
              Vercel AI SDK, and anything else that speaks the OpenAI API. Give each app its own key.
            </>
          }
        >
          <div className="grid gap-x-12 gap-y-10 lg:grid-cols-12">
            <div className="lg:col-span-5">
              <CodeBlock title="app.py, the whole change">
                <span className="block text-ink-2 line-through decoration-signal">{`-   api_key="sk-...",`}</span>
                <span className="block font-semibold">{`+   base_url="https://ai.your-company.com/v1",
+   api_key="gw_live_...",`}</span>
              </CodeBlock>
              <p className="mt-6 leading-relaxed text-ink-2">
                <code className="font-code text-sm text-ink">model</code> becomes an alias you control:{" "}
                <code className="font-code text-sm text-ink">auto</code>,{" "}
                <code className="font-code text-sm text-ink">fast</code>,{" "}
                <code className="font-code text-sm text-ink">smart</code>, or any{" "}
                <code className="font-code text-sm text-ink">provider/model</code>. Change which model sits behind it
                in one config file, without redeploying an app.
              </p>
            </div>
            <div className="lg:col-span-7">
              <CodeTabs
                tabs={[
                  {
                    label: "Python",
                    title: "pip install openai",
                    code: (
                      <>
                        <K>from</K> openai <K>import</K> OpenAI{"\n\n"}client = OpenAI(base_url=
                        <S>&quot;http://localhost:8000/v1&quot;</S>, api_key=<S>&quot;gw_live_...&quot;</S>){"\n\n"}
                        reply = client.chat.completions.create({"\n"}    model=<S>&quot;auto&quot;</S>,{"\n"}    messages=[{"{"}
                        <S>&quot;role&quot;</S>: <S>&quot;user&quot;</S>, <S>&quot;content&quot;</S>: <S>&quot;Hello!&quot;</S>
                        {"}"}],{"\n"}){"\n"}
                        <K>print</K>(reply.choices[0].message.content)
                      </>
                    ),
                  },
                  {
                    label: "Node.js",
                    title: "npm install openai",
                    code: (
                      <>
                        <K>import</K> OpenAI <K>from</K> <S>&quot;openai&quot;</S>;{"\n\n"}
                        <K>const</K> client = <K>new</K> OpenAI({"{"} baseURL: <S>&quot;http://localhost:8000/v1&quot;</S>,
                        apiKey: <S>&quot;gw_live_...&quot;</S> {"}"});{"\n\n"}
                        <K>const</K> reply = <K>await</K> client.chat.completions.create({"{"}
                        {"\n"}  model: <S>&quot;auto&quot;</S>,{"\n"}  messages: [{"{"} role: <S>&quot;user&quot;</S>, content:{" "}
                        <S>&quot;Hello!&quot;</S> {"}"}],{"\n"}
                        {"}"});{"\n"}console.log(reply.choices[0].message.content);
                      </>
                    ),
                  },
                  {
                    label: "LangChain",
                    title: "pip install langchain-openai",
                    code: (
                      <>
                        <K>from</K> langchain_openai <K>import</K> ChatOpenAI{"\n\n"}llm = ChatOpenAI({"\n"}    base_url=
                        <S>&quot;http://localhost:8000/v1&quot;</S>,{"\n"}    api_key=<S>&quot;gw_live_...&quot;</S>,{"\n"}
                        {"    "}model=<S>&quot;auto&quot;</S>,{"\n"}){"\n"}
                        <K>print</K>(llm.invoke(<S>&quot;Hello!&quot;</S>).content)
                      </>
                    ),
                  },
                  {
                    label: "cURL",
                    title: "terminal",
                    code: (
                      <>
                        curl http://localhost:8000/v1/chat/completions \{"\n"}  -H{" "}
                        <S>&quot;Authorization: Bearer gw_live_...&quot;</S> \{"\n"}  -H{" "}
                        <S>&quot;Content-Type: application/json&quot;</S> \{"\n"}  -d{" "}
                        <S>{`'{"model": "auto", "messages": [{"role": "user", "content": "Hello!"}]}'`}</S>
                      </>
                    ),
                  },
                ]}
              />
            </div>
          </div>
        </Section>

        {/* Deploy */}
        <Section
          id="deploy"
          title="Run it on your own infrastructure. No repo to clone."
          aside="TwinLM ships as a ready-made container image. Your prompts and provider keys never leave your servers."
        >
          <div className="border-t border-ink">
            {[
              {
                name: "Docker Compose",
                note: "Recommended. Gateway and PostgreSQL from one downloaded file; secrets are generated on first start.",
                title: "terminal",
                code: (
                  <>
                    <C># 1. Download one file</C>
                    {"\n"}curl -fsSLO https://raw.githubusercontent.com/rsh-119/TwinLM/main/deploy/quickstart.yml{"\n\n"}
                    <C># 2. Add your provider keys</C>
                    {"\n"}echo <S>&quot;GEMINI_API_KEY=...&quot;</S> &gt;&gt; .env{"\n"}echo <S>&quot;GROQ_API_KEY=...&quot;</S> &gt;&gt; .env{"\n\n"}
                    <C># 3. Start, then read your admin token</C>
                    {"\n"}docker compose -f quickstart.yml up -d{"\n"}docker compose -f quickstart.yml exec gateway cat
                    /run/secrets/admin_token
                  </>
                ),
              },
              {
                name: "Docker",
                note: "One container with a built-in SQLite database. Quickest way to try it; use Compose for real traffic.",
                title: "terminal",
                code: (
                  <>
                    export ADMIN_TOKEN=$(openssl rand -hex 32)   <C># keep it: needed for /admin</C>
                    {"\n\n"}docker run -d --name twinlm -p 8000:8000 \{"\n"}  -e GEMINI_API_KEY=... -e GROQ_API_KEY=... \{"\n"}  -e
                    ADMIN_TOKEN -v twinlm-data:/app/data \{"\n"}  ghcr.io/rsh-119/twinlm:latest
                  </>
                ),
              },
              {
                name: "Your models and prices",
                note: "The built-in config uses Gemini and Groq. Mount your own gateway.toml to add OpenAI, DeepSeek, Ollama or any OpenAI-compatible API. Check prices against your provider's current list.",
                title: "gateway.toml",
                code: (
                  <>
                    [providers.openai]{"\n"}type = <S>&quot;openai_compatible&quot;</S>{"\n"}base_url ={" "}
                    <S>&quot;https://api.openai.com/v1&quot;</S>{"\n"}default_model = <S>&quot;gpt-4o-mini&quot;</S>{"\n"}
                    api_key_setting = <S>&quot;openai_api_key&quot;</S>   <C># reads OPENAI_API_KEY</C>
                    {"\n\n"}[models.auto]{"\n"}targets = [<S>&quot;openai/gpt-4o-mini&quot;</S>]{"\n"}cache_ttl_seconds = 3600
                    {"\n\n"}[pricing.<S>&quot;openai/gpt-4o-mini&quot;</S>]   <C># USD per million tokens</C>
                    {"\n"}input_per_million = 0.15{"\n"}output_per_million = 0.60
                  </>
                ),
              },
            ].map((d) => (
              <div key={d.name} className="grid gap-x-12 gap-y-5 border-b border-ink py-10 lg:grid-cols-12">
                <div className="lg:col-span-4">
                  <h3 className="display text-3xl">{d.name}</h3>
                  <p className="mt-3 max-w-[44ch] leading-relaxed text-ink-2">{d.note}</p>
                </div>
                <div className="min-w-0 lg:col-span-8">
                  <CodeBlock title={d.title}>{d.code}</CodeBlock>
                </div>
              </div>
            ))}

            <div className="grid gap-x-12 gap-y-5 border-b border-ink py-10 lg:grid-cols-12">
              <div className="lg:col-span-4">
                <h3 className="display text-3xl">Render</h3>
                <p className="mt-3 max-w-[44ch] leading-relaxed text-ink-2">
                  One click: the published image, a managed PostgreSQL database and a generated admin token. You add
                  provider keys in the Render dashboard. Uses paid Render plans.
                </p>
              </div>
              <div className="lg:col-span-8 lg:self-center">
                <a
                  href="https://render.com/deploy?repo=https://github.com/rsh-119/TwinLM"
                  className="label inline-flex h-12 items-center gap-2 bg-ink px-6 text-base text-paper transition-colors hover:bg-signal hover:text-on-signal"
                >
                  Deploy to Render <ArrowUpRight size={18} strokeWidth={2} aria-hidden />
                </a>
              </div>
            </div>
          </div>

          <div className="mt-14 grid gap-8 lg:grid-cols-2">
            <CodeBlock title="Then: one key per app">
              {`curl -X POST http://localhost:8000/admin/keys \\
  -H "Authorization: Bearer $ADMIN_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"name": "support-bot", "project": "support"}'`}
            </CodeBlock>
            <CodeBlock title="And: spend per team">
              {`curl "http://localhost:8000/admin/usage?hours=24&group_by=project" \\
  -H "Authorization: Bearer $ADMIN_TOKEN"`}
            </CodeBlock>
          </div>
        </Section>

        {/* Contents: the small core */}
        <Section
          title="A small core, on purpose"
          aside="TwinLM does a few things that cut cost, instead of everything. Fewer moving parts means less to run and less to break."
        >
          <ol className="border-t border-ink">
            {FEATURES.map(([f, ref]) => (
              <li key={f} className="flex items-baseline gap-4 border-b border-rule py-4">
                <span className="text-lg">{f}</span>
                <span className="hidden flex-1 border-b border-dotted border-ink-2 sm:block" aria-hidden />
                <code className="ml-auto shrink-0 font-code text-sm text-ink-2 sm:ml-0">{ref}</code>
              </li>
            ))}
          </ol>
        </Section>

        {/* Pricing */}
        <Section
          id="pricing"
          title="Free to run. Help when you want it."
          aside="The gateway is open source under the MIT license. If you would rather not run it yourself, we will set it up with you."
        >
          <div className="grid border-t border-ink lg:grid-cols-2">
            <div className="py-10 lg:pr-12">
              <h3 className="display text-3xl">Open source</h3>
              <p className="figure mt-6 text-8xl">$0</p>
              <ul className="mt-8 space-y-2 text-lg">
                <li>Every feature, no limits</li>
                <li>Docker, Compose or Render</li>
                <li>Your data stays with you</li>
                <li>Community support on GitHub</li>
              </ul>
              <a href={REPO} className="label mt-10 inline-flex h-12 items-center gap-2 border border-ink px-6 text-base hover:bg-ink hover:text-paper">
                Get it on GitHub <ArrowUpRight size={18} strokeWidth={2} aria-hidden />
              </a>
            </div>
            <div className="border-t border-ink py-10 lg:border-l lg:border-t-0 lg:pl-12">
              <h3 className="display text-3xl">Managed setup and support</h3>
              <p className="figure mt-6 text-8xl text-signal">Let&apos;s talk</p>
              <ul className="mt-8 space-y-2 text-lg">
                <li>Deployed on your cloud, with TLS and backups</li>
                <li>Routing and caching tuned to your traffic</li>
                <li>A monthly savings review</li>
                <li>Priority support</li>
              </ul>
              <a href={CONTACT} className="label mt-10 inline-flex h-12 items-center bg-ink px-6 text-base text-paper transition-colors hover:bg-signal hover:text-on-signal">
                Talk to us
              </a>
            </div>
          </div>
        </Section>

        {/* FAQ */}
        <Section id="faq" title="Questions businesses ask">
          <div className="border-t border-ink lg:ml-[calc(41.666%+1.25rem)]">
            {FAQ.map(([q, a]) => (
              <details key={q} className="group border-b border-ink">
                <summary className="flex cursor-pointer items-baseline justify-between gap-6 py-5 text-xl font-semibold">
                  {q}
                  <Plus size={22} strokeWidth={2} aria-hidden
                    className="shrink-0 self-center transition-transform group-open:rotate-45" />
                </summary>
                <p className="max-w-[65ch] pb-6 leading-relaxed text-ink-2">{a}</p>
              </details>
            ))}
          </div>
        </Section>

        {/* Close */}
        <section className="border-t border-ink bg-ink text-paper">
          <div className="mx-auto grid max-w-[1280px] gap-10 px-4 py-24 sm:px-8 lg:grid-cols-12">
            <p className="display text-[clamp(2.5rem,6vw,5.5rem)] lg:col-span-8">
              Find out what your AI really costs.
            </p>
            <div className="flex flex-col justify-end gap-5 lg:col-span-4">
              <p className="text-lg leading-relaxed text-rule">
                Deploy in five minutes, point one app at it, and read the savings report after a week.
              </p>
              <div className="flex flex-wrap gap-4">
                <a href="#deploy" className="label inline-flex h-12 items-center bg-signal px-6 text-base text-on-signal hover:bg-paper">
                  Get started
                </a>
                <a href={REPO} className="label inline-flex h-12 items-center border border-paper px-6 text-base hover:bg-paper hover:text-ink">
                  Star on GitHub
                </a>
              </div>
            </div>
          </div>
        </section>
      </main>

      <footer className="border-t border-ink">
        <div className="mx-auto flex max-w-[1280px] flex-wrap items-baseline gap-x-8 gap-y-3 px-4 py-8 text-sm text-ink-2 sm:px-8">
          <span className="figure text-xl text-ink">TwinLM</span>
          <span>MIT licensed. Figures on this page are illustrative unless they are your own estimate.</span>
          <span className="flex gap-6 sm:ml-auto">
            <a href="docs/" className="hover:text-ink hover:underline">Docs</a>
            <a href={`${REPO}/blob/main/docs/OPERATIONS.md`} className="hover:text-ink hover:underline">Operations</a>
            <a href={`${REPO}/issues`} className="hover:text-ink hover:underline">Support</a>
          </span>
        </div>
      </footer>
    </>
  );
}
