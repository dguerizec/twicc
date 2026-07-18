// TwiCC telemetry collector. Insert-only by design: validates the envelope,
// stores the raw payload, answers 204. Never reads, logs, or stores the
// client IP or any request metadata (a stated public commitment — see
// docs/plans/2026-07-18-telemetry-design.md §4/§7 in the TwiCC repo).

// Rough cap on payload size. `String.length` counts UTF-16 code units, not
// bytes — close enough for an abuse guard on ASCII-heavy JSON.
const MAX_BODY_CHARS = 64_000;

const SUPPORTED_SCHEMAS = new Set([1]);

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/v1/telemetry") {
      if (request.method !== "POST") {
        return new Response(null, { status: 405, headers: { Allow: "POST" } });
      }
      return handleTelemetry(request, env);
    }

    // Static assets (the transparency page) are served before the worker
    // runs; anything reaching this point is an unknown path.
    return new Response(null, { status: 404 });
  },
};

async function handleTelemetry(request, env) {
  const body = await request.text();
  if (body.length > MAX_BODY_CHARS) {
    return new Response(null, { status: 413 });
  }

  let payload;
  try {
    payload = JSON.parse(body);
  } catch {
    return new Response(null, { status: 400 });
  }

  if (!SUPPORTED_SCHEMAS.has(payload?.schema) || typeof payload?.instance_id !== "string") {
    return new Response(null, { status: 400 });
  }

  await env.DB.prepare("INSERT INTO payloads (schema, instance_id, body) VALUES (?1, ?2, ?3)")
    .bind(payload.schema, payload.instance_id, body)
    .run();

  return new Response(null, { status: 204 });
}
