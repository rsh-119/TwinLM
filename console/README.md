# TwinLM web console

The admin and team console served by the gateway at `/app` (Next.js static export, Tailwind, shadcn/ui,
TanStack Query). The gateway image builds it (see the repo `Dockerfile`); there is no separate server.

```bash
npm install
npm run dev            # http://localhost:3000/app - API proxied to the gateway on :8000 (TWINLM_GATEWAY to change)
npm run build          # static export to out/ (the gateway serves it when CONSOLE_DIR points there)
npm run lint
npx playwright test    # end-to-end journey against a fresh gateway (TWINLM_URL, ADMIN_TOKEN); uses installed Edge
```

Auth is the gateway's httpOnly session cookie; every request sends `X-TwinLM-Console: 1` (CSRF guard).
