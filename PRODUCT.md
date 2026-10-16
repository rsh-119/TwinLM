# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Marketing site: Next.js (user request: "modern SaaS website with Next.js and other frameworks"), statically exported to GitHub Pages at `https://rsh-119.github.io/TwinLM/`. The gateway itself is Python/FastAPI and is not part of the site.

## Users

Two people at a small or mid-size business that already pays for AI APIs (OpenAI, Gemini, Groq...), weighted equally:
- **Owner / founder**: sees the monthly AI invoice growing and wants it smaller without a project.
- **Developer / tech lead**: is asked to cut the bill, will deploy and integrate the gateway, and checks that nothing breaks.

## Product Purpose

TwinLM is a small, self-hosted, OpenAI-compatible LLM gateway that reduces AI spend. Apps change only `base_url` and `api_key`; the gateway routes each use case to the cheapest model that is good enough, answers repeated deterministic requests from cache, caps output tokens, fails over between providers, and reports usage, cost and savings per team. Success: a business points one app at it and sees its AI bill shrink in the savings report within a week.

## Positioning

A deliberately small core (vs. large gateways like LiteLLM): one OpenAI-compatible adapter plus Gemini, seven cost features, honest accounting (unknown prices stay unknown; savings report says when it is incomplete). Self-hosted: prompts never pass through TwinLM's makers.

## Operating Context

Evaluated by reading the site, then running Docker / Docker Compose (`deploy/quickstart.yml`, no repo clone) or one-click Render (`render.yaml`), creating per-app keys via `POST /admin/keys`, and reading `GET /admin/usage` and `GET /admin/savings`.

## Capabilities and Constraints

- Endpoints: `/v1/chat/completions` (streaming, tools, vision), `/v1/embeddings`, `/v1/models`; admin `/admin/keys`, `/admin/usage`, `/admin/savings`, `/admin/cache`.
- Keys per app/team with RPM/TPM limits and a daily spend limit (soft: in-flight requests may overshoot).
- Aliases (`auto`, `smart`, `fast`) with fallback, retries, circuit breakers; routing by priority, latency, cost or load.
- Exact-match cache only for stateless, non-streaming, temperature unset/0, no tools/sensitive data; per-alias TTL.
- Output cap `default_max_output_tokens`, per-alias `max_output_tokens`.
- Savings report vs a configured baseline model.
- One gateway instance is the supported setup. Optional/frozen: conversations/memories, guardrails, structured-output repair, UIs.
- Image: `ghcr.io/rsh-119/twinlm:latest`. MIT license.

## Brand Commitments

Name: TwinLM. Repo: github.com/rsh-119/TwinLM. Commercial offer: free open source + "Managed setup & support — talk to us" (contact via GitHub issue). No hosted cloud plan exists.

## Evidence on Hand

- Local benchmarks with mock providers (≈100 concurrent users, ~122 RPS, 0% errors on one instance; zero lost usage records through a 10 s Postgres outage). Mock data only.
- No customers, testimonials, logos, or measured real-world savings exist. Any savings figure on the site must be labelled illustrative/example.

## Product Principles

1. Cost first: every feature on the page must explain how it reduces spend.
2. Drop-in: never require a new SDK or code rewrite.
3. Honest numbers: no invented savings, customers or benchmarks.
4. Small on purpose: fewer features done well beats feature parity.
