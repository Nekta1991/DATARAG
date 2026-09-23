// Same-origin proxy to the Python API (rag/api.py). The browser calls
// /rag/api/<endpoint>; this forwards to RAG_API_URL, which in round 1 is an
// ngrok tunnel to the GPU machine. Server-to-server, so no CORS, the tunnel
// URL stays out of the client bundle, and ngrok's browser warning page never
// sees a browser. Authorization passes through untouched: the Python API
// verifies the Neon Auth JWT and role=admin itself.

const UPSTREAM = (process.env.RAG_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
const ALLOWED = new Set(["health", "status", "query", "confirm"]);

// A paid query streams for ~20-60 s; the agent's own hard limits end it sooner.
export const maxDuration = 300;

async function forward(request: Request, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  const endpoint = path.join("/");
  if (!ALLOWED.has(endpoint)) return new Response("not found", { status: 404 });

  const headers: Record<string, string> = { "ngrok-skip-browser-warning": "1" };
  for (const h of ["authorization", "content-type"]) {
    const v = request.headers.get(h);
    if (v) headers[h] = v;
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${UPSTREAM}/api/${endpoint}`, {
      method: request.method,
      headers,
      body: request.method === "POST" ? await request.text() : undefined,
      cache: "no-store",
      signal: request.signal, // browser aborts -> stop streaming from the API
    });
  } catch {
    return Response.json({ detail: "RAG API unreachable (is the GPU machine and tunnel up?)" }, { status: 502 });
  }

  // A reachable tunnel with nothing behind it is NOT a fetch failure: ngrok
  // answers with its own error page, and for an offline endpoint that page is
  // an HTTP 404. Passing it straight through surfaced as "API error 404 (/rag)"
  // in the dashboard, which reads like a routing bug in this app and sent a
  // debugging session after the wrong thing. The real cause is always the same:
  // uvicorn or the tunnel is not running on the GPU machine.
  //
  // Our API only ever replies JSON or text/event-stream, so anything else came
  // from the tunnel rather than from us.
  const upstreamType = upstream.headers.get("content-type") ?? "";
  const fromOurApi = /application\/json|text\/event-stream/.test(upstreamType);
  if (!fromOurApi) {
    const ngrokError = upstream.headers.get("ngrok-error-code");
    return Response.json(
      {
        detail:
          `RAG API is not answering behind the tunnel (upstream ${upstream.status}` +
          `${ngrokError ? `, ${ngrokError}` : ""}). ` +
          "Start it on the GPU machine: uvicorn rag.api:app --host 127.0.0.1 --port 8000, " +
          "and ngrok http 8000 --url=<static domain>.",
      },
      { status: 502 },
    );
  }

  return new Response(upstream.body, {
    status: upstream.status,
    headers: {
      "content-type": upstream.headers.get("content-type") ?? "application/json",
      "cache-control": "no-cache, no-transform",
      "x-accel-buffering": "no",
    },
  });
}

export { forward as GET, forward as POST };
