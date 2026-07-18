import { describe, it, expect, beforeEach } from "vitest";
import { SELF, env } from "cloudflare:test";

const ENDPOINT = "https://collector.test/v1/telemetry";

// Storage isolation in @cloudflare/vitest-pool-workers is per test FILE, not
// per test (see "Isolation and concurrency" in the Workers Vitest docs), so
// D1 rows written by one test are visible to the next unless cleared here.
beforeEach(async () => {
  await env.DB.exec("DELETE FROM payloads");
});

function validPayload(overrides = {}) {
  return {
    schema: 1,
    instance_id: "3b1e9c2a-0000-4000-8000-1234567890ab",
    instance: { twicc_version: "1.0.0" },
    days: [],
    ...overrides,
  };
}

async function post(body) {
  return SELF.fetch(ENDPOINT, { method: "POST", body });
}

describe("POST /v1/telemetry", () => {
  it("stores a valid payload and returns 204", async () => {
    const body = JSON.stringify(validPayload());
    const res = await post(body);
    expect(res.status).toBe(204);

    const { results } = await env.DB.prepare(
      "SELECT schema, instance_id, body FROM payloads"
    ).all();
    expect(results).toHaveLength(1);
    expect(results[0].schema).toBe(1);
    expect(results[0].instance_id).toBe("3b1e9c2a-0000-4000-8000-1234567890ab");
    expect(results[0].body).toBe(body);
  });

  it("rejects invalid JSON with 400 and stores nothing", async () => {
    const res = await post("{not json");
    expect(res.status).toBe(400);
    const { results } = await env.DB.prepare("SELECT id FROM payloads").all();
    expect(results).toHaveLength(0);
  });

  it("rejects an unsupported schema version with 400", async () => {
    const res = await post(JSON.stringify(validPayload({ schema: 999 })));
    expect(res.status).toBe(400);
  });

  it("rejects a missing instance_id with 400", async () => {
    const res = await post(JSON.stringify(validPayload({ instance_id: undefined })));
    expect(res.status).toBe(400);
  });

  it("rejects a non-string instance_id with 400", async () => {
    const res = await post(JSON.stringify(validPayload({ instance_id: 42 })));
    expect(res.status).toBe(400);
  });

  it("rejects an oversized body with 413 and stores nothing", async () => {
    const res = await post(JSON.stringify(validPayload({ padding: "x".repeat(70_000) })));
    expect(res.status).toBe(413);
    const { results } = await env.DB.prepare("SELECT id FROM payloads").all();
    expect(results).toHaveLength(0);
  });

  it("rejects non-POST methods with 405", async () => {
    const res = await SELF.fetch(ENDPOINT, { method: "GET" });
    expect(res.status).toBe(405);
    expect(res.headers.get("Allow")).toBe("POST");
  });
});

describe("other paths", () => {
  it("returns 404 for unknown paths", async () => {
    const res = await SELF.fetch("https://collector.test/nope", { method: "POST" });
    expect(res.status).toBe(404);
  });
});
