# twicc-telemetry-collector

Collector for TwiCC's anonymous usage telemetry. A Cloudflare Worker with a
D1 (SQLite) backing store, served at `twicc-telemetry.twidi.com`. Design and
rationale: [`../docs/plans/2026-07-18-telemetry-design.md`](../docs/plans/2026-07-18-telemetry-design.md).

All commands below are run from this directory (`telemetry-collector/`).

## Layout

```
README.md                  this file
package.json                npm scripts (dev/deploy/test/tail/db:migrate:remote)
package-lock.json           locked dependency versions
.gitignore                  node_modules/, .wrangler/
wrangler.toml                Worker config: name, route, D1 binding, assets dir
vitest.config.js             vitest + @cloudflare/vitest-pool-workers wiring
migrations/0001_payloads.sql the `payloads` table + indexes
src/worker.js                the Worker: validates and inserts telemetry payloads
public/index.html            the public transparency page (served as static assets)
test/apply-migrations.js     vitest setup file: applies migrations to the test D1
test/worker.test.js          request/response and validation tests
```

## Prerequisites

- Node >= 22 (the floor set by `wrangler` 4; `vitest` 4 alone would allow >= 20).
- Install dependencies (local `wrangler`/`vitest`, no global install — every
  command below goes through `npx` or an npm script):
  ```bash
  npm install
  ```
- Authenticate wrangler once per machine:
  ```bash
  npx wrangler login
  ```

## First deployment (runbook)

Run once, in order, the first time this collector is deployed:

```bash
npx wrangler d1 create twicc-telemetry
```

Paste the returned `database_id` into `wrangler.toml`, replacing the
`REPLACE_AFTER_D1_CREATE` placeholder. This id is **intentionally committed**
— D1 database ids are not secrets, so don't "sanitize" it back out.

```bash
npm run db:migrate:remote
npm run deploy
```

Verify:

```bash
curl -i https://twicc-telemetry.twidi.com/
curl -i -X POST https://twicc-telemetry.twidi.com/v1/telemetry \
  -H 'content-type: application/json' \
  -d '{"schema":1,"instance_id":"smoke-test","instance":{},"days":[]}'
```

The first call should return the transparency page (200), the second a bare
204. Confirm the row landed (see *Querying the data* below), then remove it
— this is the one place a destructive command is legitimate here, and it is
scoped to the smoke-test row only:

```bash
npx wrangler d1 execute twicc-telemetry --remote --command \
  "DELETE FROM payloads WHERE instance_id = 'smoke-test'"
```

Finally, configure the WAF rate-limiting rule (see *Custom domain & rate
limiting* below) — it is not part of `wrangler deploy` and must be set up
by hand.

## Everyday operations

- **Update the worker or the transparency page:** edit, then
  ```bash
  npm test
  npm run deploy
  ```
  `deploy` ships the worker code, the static assets, and the custom domain
  config together — there is no separate asset-publish step.
- **Watch live traffic:**
  ```bash
  npm run tail
  ```
- **Local dev:**
  ```bash
  npm run dev
  ```
  If a local request fails because the `payloads` table doesn't exist yet,
  apply migrations to the local D1 instance:
  ```bash
  npx wrangler d1 migrations apply twicc-telemetry --local
  ```

## Querying the data

Ad hoc queries against the remote D1 database, run from the CLI:

```bash
# Total payloads received per day
npx wrangler d1 execute twicc-telemetry --remote --command \
  "SELECT substr(received_at, 1, 10) AS day, COUNT(*) AS payloads FROM payloads GROUP BY day ORDER BY day;"

# Distinct instances seen per day
npx wrangler d1 execute twicc-telemetry --remote --command \
  "SELECT substr(received_at, 1, 10) AS day, COUNT(DISTINCT instance_id) AS instances FROM payloads GROUP BY day ORDER BY day;"

# Latest payload of one instance, pretty-printed
npx wrangler d1 execute twicc-telemetry --remote --command \
  "SELECT received_at, json_pretty(body) AS body FROM payloads WHERE instance_id = 'INSTANCE_ID_HERE' ORDER BY received_at DESC LIMIT 1;"
```

For interactive SQL, the D1 database also has a console in the Cloudflare
dashboard (Workers & Pages -> D1 -> `twicc-telemetry` -> Console).

For backups (use a timestamped filename — a fixed one silently overwrites
the previous backup):

```bash
npx wrangler d1 export twicc-telemetry --remote --output="backup-$(date +%F).sql"
```

## Schema changes

```bash
npx wrangler d1 migrations create twicc-telemetry <name>
```

Edit the generated SQL file under `migrations/`, then apply it locally for
tests/dev:

```bash
npx wrangler d1 migrations apply twicc-telemetry --local
```

Once verified, apply it to production:

```bash
npm run db:migrate:remote
```

Never edit a migration that has already been applied (locally or remotely) —
create a new one instead.

## Custom domain & rate limiting

The hostname is declared in `wrangler.toml` (`routes = [{ pattern =
"twicc-telemetry.twidi.com", custom_domain = true }]`); DNS and the TLS
certificate are provisioned automatically on `npm run deploy` since the
`twidi.com` zone is already on this Cloudflare account.

Per-IP rate limiting is **not** implemented in the worker. It is a
zone-level WAF rate-limiting rule, configured once in the Cloudflare
dashboard (Security -> WAF -> Rate limiting rules): match hostname
`twicc-telemetry.twidi.com` and path `/v1/telemetry`, e.g. block for 10s any
IP exceeding 30 requests/10s. This happens entirely at the edge — the worker
code never sees, logs, or stores client IPs.

## Contract with the TwiCC client

- **Endpoint:** `POST https://twicc-telemetry.twidi.com/v1/telemetry`
- **Method:** POST only; any other method on that path returns 405 with
  `Allow: POST`. Unknown paths return 404.
- **Body:** JSON, UTF-8, with at least `schema` (integer) and `instance_id`
  (string). An unrecognized `schema` value is rejected with 400 — this is
  the versioning mechanism. Additive changes to the payload shape within
  schema 1 are safe: the collector stores the raw body and does not
  validate beyond `schema`/`instance_id`.
- **Success:** 204 with an empty body.
- **Errors:** 400 (malformed JSON, unsupported `schema`, missing/non-string
  `instance_id`), 413 (body over 64000 characters).
