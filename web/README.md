# web — DATARAG query console

Next.js 16 frontend: admin sign-in (Neon Auth) and the dashboard that streams the
API's SSE events. All retrieval, gating and billing happen in the Python API (`../rag/api.py`).

```bash
npm install
npm run build && npx next start -p 3000   # open http://localhost:3000 (not 127.0.0.1)
```

Needs the API on `NEXT_PUBLIC_API_URL` (default `http://127.0.0.1:8000`) and
`NEON_AUTH_BASE_URL` / `NEON_AUTH_COOKIE_SECRET` in `.env.local`.
Design: Organic design system — see `../docs/dashboard_build_brief.md`.
