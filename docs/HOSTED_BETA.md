# Hosted beta: setup and operation

A free, public TwinLM where visitors sign up from the website and start using it right away. One shared gateway
on free tiers; each sign-up gets their own team (as its admin), API keys and a monthly budget, on the free models in
[`deploy/gateway.beta.toml`](../deploy/gateway.beta.toml).

| Piece | Free tier | What it holds |
| --- | --- | --- |
| Render web service | `free` plan (sleeps after 15 idle min) | the gateway and console, from `ghcr.io/rsh-119/twinlm:latest` |
| Neon PostgreSQL | free plan | users, teams, keys, budgets, usage records |
| Gemini + Groq API keys | free tiers | the models everyone uses (your keys) |
| GitHub Pages | free | the website, with a "Try it free" button |

## Before you start

1. The image must contain the sign-up feature and `deploy/gateway.beta.toml`: merge this change to `main` and wait
   for CI's `image` job.
2. Create **new** API keys for the beta (don't reuse keys that were pasted anywhere):
   [Google AI Studio](https://aistudio.google.com/apikey) and [Groq](https://console.groq.com/keys).

## 1. Database (Neon)

Create a Neon project `twinlm-beta` in AWS us-east-2 (the blueprint runs on Render in Ohio, next to it). Copy its **direct** connection
string (not the "pooled" one: migrations take a database lock that connection pooling can break), e.g.
`postgresql://user:pass@ep-xxx.us-east-2.aws.neon.tech/neondb?sslmode=require`.

## 2. Gateway (Render)

1. Sign up at [render.com](https://render.com) with GitHub.
2. **New → Blueprint**, pick this repository, set **Blueprint path** to `deploy/render-beta.yaml`.
3. Fill in `DATABASE_URL` (Neon), `GEMINI_API_KEY`, `GROQ_API_KEY`; **Apply**.
4. When the deploy is live, note the URL, e.g. `https://twinlm-beta.onrender.com`. `GET /ready` returns `ready`.

## 3. Your admin account

Open `<url>/app`. The first visit asks for the admin token: Render → `twinlm-beta` → **Environment** →
`ADMIN_TOKEN`. Create your account; you are the only admin. Sign-up for visitors opens after this step.

Check it in a private window: `<url>/app/sign-in/?signup` → create an account → **Get started** → create a key →
send a request (the Playground works too). Back in your admin view, the request appears under **Usage**.

## 4. Website and keep-awake

In GitHub → Settings → Secrets and variables → Actions → **Variables**, add `BETA_URL` = the Render URL.
- The website shows **Try it free** linking to `<BETA_URL>/app/sign-in/?signup` on its next deploy (re-run the
  **Website** workflow once).
- The **Keep hosted beta awake** workflow then pings it every 10 minutes.

## Operating it

| Task | How |
| --- | --- |
| See all usage, teams, spend | Your admin console: Overview, Usage, Teams, Budgets |
| More or fewer accounts | Render env `SIGNUP_MAX_ACCOUNTS` (counts every user, including you) |
| Bigger or smaller free budget | `SIGNUP_TEAM_BUDGET_USD` (new teams); existing teams on the Budgets page |
| Stop new sign-ups | `SIGNUP_ENABLED=false` (existing users keep working) |
| Block someone | People → disable; revoke their team's keys on the Keys page |
| Change models or limits | edit `deploy/gateway.beta.toml` (needs a new image), or Providers/Models in the console |
| Provider free limits | shared by all users: Groq about 30 requests/min per account, Gemini per-model daily limits |

## Limits to tell users (also on the website's Hosted beta page)

No SLA, data may be reset, the service may sleep (first request after a pause takes about a minute), free-tier
models with shared limits, prompts pass through the beta server to Google/Groq (TwinLM stores request metadata, not
prompts; conversations are off) and free-tier providers may use data per their own terms. Password reset by email
doesn't exist yet: users ask you, and you reset it on the People page.

## Later

A custom domain (GitHub Pages and Render both support one, with DNS records at your registrar), email verification
and password reset, payments, and private per-customer instances with their own provider keys.
