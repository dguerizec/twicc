-- One row per received telemetry payload. Raw JSON is the source of truth
-- (design: the reading side evolves without redeploying the collector);
-- schema + instance_id are extracted as columns for cheap querying.
-- No IP, no user agent, no request metadata of any kind is stored.
CREATE TABLE payloads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    schema INTEGER NOT NULL,
    instance_id TEXT NOT NULL,
    body TEXT NOT NULL
);

CREATE INDEX idx_payloads_instance ON payloads(instance_id);
CREATE INDEX idx_payloads_received ON payloads(received_at);
