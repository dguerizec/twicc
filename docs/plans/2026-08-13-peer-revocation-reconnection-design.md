# Peer Revocation, Reconnection, and Address Changes — Design

**Status:** validated by adversarial review; awaiting owner approval
**Date:** 2026-08-15
**Scope:** preserve peer history when a relationship ends, provide an explicit full-pairing reconnection flow, and make local or remote address changes fail closed.

---

## 0. Relationship to earlier designs

`docs/plans/2026-07-24-peer-messaging-design.md` is the frozen founding design. It remains unchanged.

This design replaces the founding design's decisions about deleting an established peer, cascading its message history, re-pairing a known address, and editing peer addresses. It also replaces the corresponding historical edge cases in `docs/plans/2026-08-11-peer-threading-design.md`. The threading design remains authoritative for threading behavior.

### 0.1 Development-only owner ruling

The complete `peer-system` branch is private development work on the owner's machines. The Peer System has never been released, distributed, or deployed for another user. No supported external installation, client, database, or Peer wire version exists.

The owner can recreate every Peer test instance and its data from zero at any time. The implementation therefore targets only the final contract in this design.

It requires no backward compatibility between Peer System revisions. It adds no version negotiation, dual read or write, fallback, historical-schema adapter, duplicate merge, or invalid-row repair. Earlier branch commits and earlier designs are development history, not supported versions.

Any development-only Peer row can stop the migration with an explicit diagnostic. Resetting that development database is an accepted result.

### 0.2 In scope

- revoke an established relationship without deleting its messages;
- distinguish human revocation from an automatic broken relationship;
- reconnect the same canonical remote address through a complete pairing flow;
- preserve the same local `Peer` and history after that reconnection;
- reconcile unresolved outbound message statuses after reconnection;
- filter revoked-peer messages out of the default inbox view;
- canonicalize and uniquely identify remote peer origins;
- invalidate relationships safely when this instance's `peerBaseUrl` changes;
- represent a remote address replacement as a new `Peer` and new history;
- expose the resulting states in owner REST, Settings, Manage Peers, inbox, and read-only agent surfaces.

### 0.3 Out of scope

- a message-level archive state;
- automatic discovery of a peer's new address;
- transfer or merge of history between two different remote addresses;
- remote notification when a user revokes a relationship;
- an agent, skill, CLI, or MCP mutation for Revoke, Reconnect, or `peerBaseUrl`;
- changes to inbox rendering or search beyond revoked-peer visibility and selection;
- inbox pagination, result-cap changes, or filter-grammar changes;
- behavior after a user edits `settings.json` directly;
- repair of development-only Peer rows before the migration;
- compatibility with an earlier Peer frontend, backend, schema, or wire contract.

---

## 1. Current implementation

This section records only the current behavior that this design changes.

### 1.1 Relationship lifetime

`src/twicc/core/models.py::PeerState` has `pending_sent`, `pending_received`, `active`, and `broken`. It has no revoked state or structured broken reason.

`src/twicc/core/services/peer_mutation.py::delete_peer` deletes a `Peer` in any state. `src/twicc/core/models.py::PeerMessage.peer` uses `CASCADE`, so deleting an established peer deletes all of its messages.

`src/twicc/peer/owner_views.py::peer_detail` exposes that deletion through `DELETE /api/peers/<id>/`. `frontend/src/components/peer/PeersManagerDialog.vue` labels the action **Remove**.

### 1.2 Pairing identity

`src/twicc/core/models.py::Peer.base_url` is not unique. `src/twicc/core/services/peer_mutation.py::normalize_base_url` only trims whitespace and a trailing slash. The initial handshake excludes `broken` rows during address matching, so the same address can produce a second `Peer`.

`src/twicc/core/services/public_origin.py::normalize_public_origin` now owns the strict shared origin contract. `docs/superpowers/specs/2026-08-13-public-origin-settings-design.md` defines its input and canonicalization rules. Peer relationship creation and inbound handshake validation do not yet use it.

Per-peer address editing is already disabled. `PATCH /api/peers/<id>/` rejects `base_url`, and Manage Peers has no Edit URL action.

### 1.3 Pairing storage

Initial pairing uses the main `src/twicc/core/models.py::Peer` state and credential fields. A second pairing attempt for an established address has no separate provisional storage.

An active relationship can become `broken` after a remote `403`. The current model does not record why it became broken.

`src/twicc/core/services/peer_mutation.py` already provides request, verification, acceptance, retry, and credential helpers. The initial handshake verifies a six-digit code before acceptance.

The initial received-only Accept path persists its fresh local token before the outbound callback. The initial sent-only
held-Accept path persists the returned remote token and display name until local code confirmation completes.

### 1.4 Local public address

`peerBaseUrl` is a synchronized setting. `frontend/src/composables/useOriginSettingsForm.js::apply` sends one origin field through `frontend/src/stores/settings.js::sendOriginSetting` and `frontend/src/composables/useWebSocket.js::sendSyncedSettings`.

`src/twicc/asgi.py::UpdatesConsumer._handle_update_synced_settings` preserves `baseVersion` and `request_id`, then delegates to `src/twicc/core/services/settings_mutation.py::update_synced_settings`. The service validates and canonicalizes the origin, writes `settings.json`, broadcasts `synced_settings_updated`, and returns a correlated `synced_settings_result`. It does not coordinate `Peer` rows or credentials.

`src/twicc/origin_gate.py::PublicOriginGate` and `src/twicc/core/services/origin_policy.py::get_origin_policy` apply the configured routing authority on every request. A valid settings write becomes effective without a backend restart.

A `Peer` does not record the local public origin used when its credentials were established. The runtime therefore cannot detect that an established credential belongs to a different local origin.

`src/twicc/providers/db_writer.py::run_under_db_write_lock` serializes Peer writes. The settings service has a separate synchronous `_settings_lock` critical section.

### 1.5 Inbox and agent reads

`frontend/src/components/peer/PeerInboxDialog.vue` starts its peer select with **All peers**. `frontend/src/utils/peerInboxFilter.js` switches between the store's unfiltered messages and `GET /api/peer-messages/?peer_id=&q=&limit=200` for peer or text filtering.

`src/twicc/peer/owner_views.py::peer_messages_list` applies an exact `peer_id` when supplied, then matches title or text with the existing fuzzy-or-quoted-exact grammar. Neither the local source nor the REST query distinguishes revoked history because no revoked state exists.

`src/twicc/core/services/peer_messages.py::apply_status_callback` uses the shared database write lock. Its final-state recheck does not yet occur inside that lock.

`twicc peers` lists active and broken peers. `twicc peer-send` treats `broken` as one combined unavailable state.

The repository has no Vue component-test harness for the Peer dialogs. Frontend interaction checks use manual acceptance tests.

### 1.6 Mutation surface boundaries

Owner REST routes, the Settings WebSocket path, CLI setting-key classification, and agent-facing command allow-lists are separate entry points.

The CLI has no full-settings JSON import or file-apply command. Provider and notification settings commands accept only their owned keys.

---

## 2. Vocabulary and state ownership

### 2.1 Main relationship states

`Peer.state` gains `revoked`.

| State | Owner | Meaning |
|---|---|---|
| `pending_sent` | pairing flow | This instance started an initial relationship that is not established. |
| `pending_received` | pairing flow | This instance received an initial relationship request that is not established. |
| `active` | successful pairing | The current credentials and both canonical addresses form a usable relationship. |
| `broken` | TwiCC | The relationship is established but cannot be used automatically. |
| `revoked` | local human | The local human explicitly ended this relationship. |

`revoked` is never an automatic network result. `broken` is never a synonym for a local human revocation.

### 2.2 Broken reasons

`Peer.broken_reason` is blank outside `broken`. It supports these defined values:

| Value | Meaning |
|---|---|
| `local_address_changed` | This instance changed its public Peer origin after pairing. |
| `local_address_disabled` | This instance disabled its public Peer origin. |
| `remote_address_changed` | A verified replacement request established the same human at a new remote origin. |
| `remote_credential_rejected` | An authenticated outbound message received `403 unknown_token` for the credential used by that request. |

`remote_credential_rejected` does not assert that the remote human revoked this instance. It records only the credential rejection.

Section 6.3 owns its only trigger. A network failure, timeout, any other HTTP response, status callback failure, status-query failure, or reconciliation failure leaves an active `Peer` active. Such a result changes only its operation or message.

### 2.3 Revocation fields

`Peer.revoked_at` records the local revocation time. It is non-null only in `revoked`.

`accepted_at` keeps the first successful pairing time. Reconnection does not replace it.

Database checks enforce the state metadata:

- `revoked` requires `revoked_at`, and every other state requires it to be null;
- `broken` requires a defined `broken_reason`, and every other state requires it to be blank;
- a non-empty `reconnect_state` owns one coherent provisional slot;
- an empty `reconnect_state` requires every provisional field to be empty;
- `request_previous_base_url` is empty outside an initial pending state;
- `replaces_peer` cannot reference the same row.
- `previous_base_url` and `replaces_peer` are both empty or both populated;
- while an initial row is pending, a populated `previous_base_url` equals `request_previous_base_url` as a canonical Python origin string;
- a populated `previous_base_url` differs from the row's `base_url`.

### 2.4 Local-origin binding

`Peer.paired_local_base_url` stores the canonical `peerBaseUrl` used by the most recent successful initial pairing or reconnection.

Every credential-sensitive operation compares it with the current canonical `peerBaseUrl`. A mismatch blocks the operation without changing the Peer. Section 6 defines the explicit Web recovery path.

### 2.5 Provisional reconnection slot

An established `Peer` stores at most one reconnection attempt. It does not create a second relationship model.

The provisional slot contains:

- `reconnect_state`: blank, `sent`, `received`, or `crossed`;
- `reconnect_token_ours` and `reconnect_token_theirs`;
- `reconnect_remote_display_name`;
- `reconnect_verification_code`;
- `reconnect_verification_attempts` and `reconnect_verification_regens`;
- `reconnect_verified_at`;
- `reconnect_code_confirmed_at`;
- `reconnect_remote_accepted_at`;
- `reconnect_sent_at` and `reconnect_received_at`;
- `reconnect_sent_display_name`;
- `reconnect_sent_base_url` and nullable `reconnect_sent_previous_base_url`;
- `reconnect_sent_accept_remote_display_name`;
- `reconnect_send_status`: blank, `prepared`, `sent`, or `retryable`;
- `reconnect_send_error`, a non-secret request error code.

These fields never replace active credentials before a complete reconnection succeeds.

The slot owns `reconnect_token_ours` and `reconnect_token_theirs` as one provisional credential pair. The tokens are
directional credentials, not request-leg metadata:

- `reconnect_token_ours` is the fresh local credential. The service mints it once before the first local handshake request or Accept callback that needs it.
- `reconnect_token_theirs` is the fresh remote credential. The service stores it from an incoming request or Accept callback.
- either token can remain blank until its protocol step supplies it;
- a one-sided slot can contain both tokens after acceptance activity;
- Refuse and Cancel preserve both tokens while the other leg remains;
- clearing the complete slot clears both tokens.

The sent leg owns:

- `reconnect_code_confirmed_at`;
- `reconnect_remote_accepted_at`;
- `reconnect_sent_at`;
- `reconnect_sent_display_name`;
- `reconnect_sent_base_url`;
- `reconnect_sent_previous_base_url`;
- `reconnect_sent_accept_remote_display_name`;
- `reconnect_send_status`;
- `reconnect_send_error`.

The received leg owns:

- `reconnect_remote_display_name`;
- `reconnect_verification_code`;
- `reconnect_verification_attempts`;
- `reconnect_verification_regens`;
- `reconnect_verified_at`;
- `reconnect_received_at`.

Ownership defines the no-leg database constraints and the fields that an action clears. It does not make every
present-leg field mandatory before the protocol stage that fills it. The two received-leg counters are nullable when
the leg is absent. A new received leg sets both counters to zero.

The slot invariants are:

- `sent` has one sent leg and no received leg;
- `received` has one received leg and no sent leg;
- `crossed` has both legs;
- a sent leg requires `reconnect_token_ours`, `reconnect_sent_at`, `reconnect_sent_display_name`, `reconnect_sent_base_url`, and a non-blank `reconnect_send_status`; `reconnect_sent_previous_base_url` follows the nullable §7.2 classification;
- a received leg requires `reconnect_token_theirs`, `reconnect_remote_display_name`, `reconnect_verification_code`, both verification counters, and `reconnect_received_at`; `reconnect_verified_at` stays blank until verification succeeds;
- no sent leg requires every sent-owned field to be blank;
- no received leg requires every received-owned field to be blank;
- a provisional token can remain when its authentication leg is absent, but §7.2 cannot authenticate it for that absent leg;
- `reconnect_remote_accepted_at` requires both provisional tokens and `reconnect_sent_accept_remote_display_name`;
- `prepared` and `sent` require a blank `reconnect_send_error`; `retryable` requires a non-blank one;
- `reconnect_send_error` contains no token, URL, display name, or response body;
- later held-accept fields can fill while the state remains `sent`, `received`, or `crossed`;
- refusing the received leg clears every received-owned field and preserves the slot-owned token pair and every sent-owned field while a sent leg remains;
- cancelling the initiated leg clears every sent-owned field and preserves the slot-owned token pair and every received-owned field while a received leg remains;
- clearing the last leg clears all provisional fields in one database write.

### 2.6 Address replacement claim

An initial pending `Peer` can store these separate values:

- nullable canonical `request_previous_base_url`, the exact optional `previous_base_url` value from the accepted request;
- canonical `previous_base_url`, only for replacement provenance that becomes trusted after code verification;
- nullable self-`ForeignKey` `replaces_peer`, with `on_delete=PROTECT` and `related_name="replacement_peers"`.

`request_previous_base_url` is internal request identity. Null means the request field was absent. A non-null value does
not identify an old Peer, does not establish replacement provenance, and is never owner-serialized. The service clears
it when the initial row leaves a pending state. This field belongs to the initial request row, not to a reconnection leg
or its provisional slot.

The service stores `request_previous_base_url` before it evaluates the claim. It stores `previous_base_url` and
`replaces_peer` only when the request value exactly matches an established active, broken, or revoked Peer and the new
`base_url` differs. When no exact established Peer exists, it keeps `request_previous_base_url` for exact request Retry
and leaves both provenance fields empty. After verified acceptance, `previous_base_url` and `replaces_peer` remain
immutable provenance on the new Peer.

The request-identity and provenance comparisons target canonical Python origin strings. These fields never merge either
row or either history.

Deleting the pending or later established replacement row is not blocked by this forward reference. Deleting the referenced old Peer is blocked while any replacement row retains that provenance. The application still exposes no established-Peer delete path.

### 2.7 Owner serialization

`serialize_peer` adds `revoked_at`, `broken_reason`, `previous_base_url`, and `replaces_peer_id`. It never exposes
`request_previous_base_url` as replacement provenance or as any other owner field.

It also adds a `reconnect` object when the provisional slot is non-empty. The object exposes state, timestamps, remote display name, crossed status, and the local verification code when the receiving human must see it. It exposes no current or provisional token.

`serialize_peer` derives leg presence only from `reconnect_state`. It serializes public fields only for a present leg and
never infers leg presence from a non-blank owned field or slot-owned token. The object exposes `sent_at` for an initiated
leg and `received_at`, remote display name, verification state, and the local verification code for a received leg. A
sent-only held Accept can expose its sent-owned remote display name. A crossed slot exposes both leg sets. The object
also exposes the non-secret send status and error for an initiated leg, but none of its stored request payload.

Owner serialization exposes no current token, slot-owned token, or other provisional credential.

---

## 3. Decisions

| ID | Decision | Definition | Verification |
|---|---|---|---|
| D1 | Established peers are revoked, never deleted. | §4 | §15.1, §15.4 |
| D2 | Revocation is local, silent, immediate, and invalidates all credentials. | §4 | §15.1, §15.4 |
| D3 | A complete pairing flow with fresh credentials is required for every reconnection. | §7 | §15.2, §15.4 |
| D4 | Reconnecting the same canonical remote origin reuses the same `Peer` and history. | §5, §7 | §15.1, §15.2 |
| D5 | One canonical remote origin identifies at most one `Peer` across all states. | §5 | §15.1 |
| D6 | Revoked-peer messages appear only when that exact revoked peer is selected. | §9 | §15.3, §15.4 |
| D7 | Locally stored pending messages remain locally actionable after revocation without a remote callback. | §8 | §15.1, §15.4 |
| D8 | A same-origin reconnection reconciles only local outbound pending statuses and never retransmits content. An address replacement never resolves across Peer history. | §10 | §15.2 |
| D9 | Established credentials are never used when the paired local origin differs from the current origin. Recovery requires an explicit Web transition. | §6, §11 | §15.1, §15.4 |
| D10 | A confirmed Web transition that disables or replaces `peerBaseUrl` invalidates existing relationships before any optional network work. | §11 | §15.1, §15.4 |
| D11 | A new remote origin creates a new `Peer` and new history, even when it claims to replace a known origin. | §12 | §15.2, §15.4 |
| D12 | A replacement claim uses exact canonical origin matching and becomes trusted only after code verification. | §12 | §15.2, §15.4 |
| D13 | Revoke, Reconnect, and `peerBaseUrl` relationship transitions are owner-only Web actions. | §11, §13 | §15.1, §15.3 |
| D14 | Initial incomplete requests can still be cancelled or refused and deleted. | §4 | §15.1 |
| D15 | Peer identity validation uses the authoritative shared Python public-origin normalizer. | §5 | §15.1 |
| D16 | Revocation and message arrival or send are serialized by the database write lock. | §6 | §15.1 |
| D17 | `peerBaseUrl` transitions extend the existing correlated WebSocket origin-setting flow. | §11 | §15.1, §15.3 |
| D18 | A temporary routing-settings failure or a request on the wrong authority never changes Peer lifecycle state or credentials. | §6 | §15.1 |

### 3.1 Feasibility

The levels below describe buildability in this repository. They do not estimate effort.

| Contract | Decisions | Level | Feasibility basis and required construction |
|---|---|---|---|
| Revocation, retained history, and race serialization | D1, D2, D7, D14, D16 | `needs work` | Section 1.1 identifies the destructive lifetime contract. Section 1.4 identifies the serialization primitive. The implementation adds lifecycle fields, constraints, `PROTECT`, and locked transitions. |
| Full same-origin reconnection and status reconciliation | D3, D4, D8 | `needs work` | Sections 1.3 and 1.5 identify the reusable handshake and resolution primitives. The slot-owned credential pair supports one-sided and crossed acceptance without early current-credential mutation. The implementation also moves the final-state recheck inside the lock. |
| Canonical remote identity | D5, D15 | `obvious` | Section 1.2 identifies the authoritative normalizer. One canonical string can be normalized before storage and constrained unique by Django and SQLite. |
| Revoked-aware inbox scope | D6 | `obvious` | Section 1.5 identifies the local and REST filtering paths. Each path can apply the same Peer-state predicate before text matching. |
| Local-origin credential binding and managed address transition | D9, D10, D17, D18 | `needs work` | Section 1.4 identifies the correlated settings path and both lock domains. The implementation adds credential binding and one ordered transition without holding `_settings_lock` across an `await`. |
| Verified remote address replacement | D11, D12 | `needs work` | Section 1.3 identifies the reusable verified handshake. The implementation adds canonical replacement provenance to the pending-row pipeline. |
| Owner-only mutation surfaces | D13 | `obvious` | Section 1.6 identifies the separate mutation entry points. Each entry point can reject or omit the managed operations independently. |

---

## 4. Revocation, cancellation, and deletion

### 4.1 Established relationship revocation

The owner endpoint is:

```text
POST /api/peers/<id>/revoke/
```

It accepts `active` and `broken`. It is idempotent for `revoked`. Initial pending states return `bad_state` and use their existing cancel or refuse actions instead.

The transition runs under the database write lock. It:

- sets `state = revoked`;
- sets `revoked_at` when the first revocation succeeds;
- clears `broken_reason`;
- clears `token_ours` and `token_theirs`;
- clears every provisional reconnection field;
- preserves the row and all messages;
- emits one owner-side peer update after the lock;
- performs no network call.

The endpoint returns the serialized peer. A retry against an already revoked peer returns the same successful state.

### 4.2 Initial request deletion

`DELETE /api/peers/<id>/` is restricted to `pending_sent` and `pending_received` rows that do not own message history. Other states return `bad_state`.

Cancelling an outgoing initial request and refusing an incoming initial request can hard-delete that incomplete row. The deletion broadcasts `peer_removed`.

### 4.3 Message foreign key

`PeerMessage.peer` changes from `CASCADE` to `PROTECT`.

The application exposes no established-peer delete path. The protection is the final database guard against accidental history loss.

### 4.4 Old credentials after revocation

All authenticated inbound Peer routes return the same response for an old revoked token and an unknown token:

```text
403 {"error": "unknown_token"}
```

The attempt creates no `PeerMessage`. It changes no badge, toast, status, or `last_contact_at`.

The technical log can record that an unknown credential reached a route. It records no token and no message content. The system stores no durable attempt row.

---

## 5. Canonical remote identity and migration

### 5.1 Shared origin contract

These inputs use `src/twicc/core/services/public_origin.py::normalize_public_origin`:

- the local `peerBaseUrl` setting;
- the remote address entered in Add a Peer;
- `base_url` in an inbound handshake request;
- non-empty `previous_base_url` in an inbound handshake request.

`docs/superpowers/specs/2026-08-13-public-origin-settings-design.md` remains authoritative for accepted input and canonical output. Python gives the final verdict. `frontend/src/utils/publicOrigin.js` performs only the documented permissive subset check before Apply and never becomes a second origin parser.

The canonical value keeps only an HTTP or HTTPS origin. It has no credentials, path, query, or fragment. The shared helper handles case, IDNA, IP literals, and default ports.

The local `peerBaseUrl` can be empty because empty disables Peer routing. Add a Peer and every handshake `base_url` require a non-empty canonical origin. `previous_base_url` is either absent or a non-empty canonical origin; a present empty value returns `invalid_payload`.

### 5.2 Uniqueness

`Peer.base_url` becomes unique after canonicalization. The constraint covers initial pending, active, broken, and revoked rows.

Every application lookup and creation check uses the canonical value under the database write lock. A human name or remote display name never participates in relationship identity.

Add a Peer handles an existing canonical address as follows:

| Existing state | Result |
|---|---|
| `pending_sent` or `pending_received` | `already_related` |
| `active` | `already_related` |
| `broken` or `revoked` | enter the Reconnect flow on that row |

### 5.3 Migration

The released TwiCC schema contains no Peer data. The migration targets that empty Peer surface.

Before changing the schema, the migration fails with an explicit diagnostic when any development-only `Peer` row exists. The diagnostic lists the Peer IDs and tells the developer to reset the development database. It does not inspect, canonicalize, merge, repair, or backfill those rows.

On the empty released-data path, the migration:

1. adds the lifecycle, local-origin, reconnection, request-identity, and replacement fields;
2. adds the state-metadata checks from §2.3;
3. adds the unique constraint on `Peer.base_url`;
4. changes `PeerMessage.peer` to `PROTECT`.

All new rows enter through the final normalization and state contracts. The development-only owner ruling in §0.1 supplies the reason for the explicit non-empty-table failure.

---

## 6. Credential binding, routing availability, and races

### 6.1 Credential-sensitive gate

Inbound message receipt, outbound send, status callback, status query, reconciliation, and handshake completion re-read the Peer under the database write lock.

The operation is allowed only when:

- the main state allows that operation;
- the expected current or provisional token matches;
- the backend routing snapshot is available and contains a canonical non-empty `peerBaseUrl`;
- an established credential uses `paired_local_base_url == peerBaseUrl`.

An unavailable routing snapshot returns `peer_routing_unavailable`. A local-origin mismatch returns `local_address_transition_required`. Both results stop the operation before a message, callback, status change, or authenticated remote call.

Neither result changes `Peer.state`, credentials, provisional fields, messages, or timestamps. No startup process converts either result into a lifecycle transition.

`src/twicc/origin_gate.py::PublicOriginGate` rejects a request that reaches the wrong routing authority before a Peer service authenticates it. That rejection is request-scoped. It changes no Peer state or credential.

If a confirmed Web address transition writes `settings.json` but stops before the SQLite transition commits, `paired_local_base_url` supplies the fail-closed mismatch. Applying the already-stored address again through the Web form is not a no-op while such mismatched established rows exist. The form requests confirmation and the managed service completes the transition defined in §11.

This design adds no durable transition marker and no startup repair. Direct edits to `settings.json` remain outside the contract in §0.3.

### 6.2 Incoming message versus Revoke

Token resolution before the lock is not authorization. `receive_peer_message` re-resolves the token and relationship state inside the write lock before row creation.

- If message creation commits first, the local message remains stored. A later Revoke does not delete it.
- If Revoke commits first, receipt returns `403 unknown_token` and creates no message.

No intermediate result creates a partial message.

### 6.3 Outgoing send versus Revoke

Peer validation and outbound `PeerMessage` creation share the database write lock.

- If message creation commits first, its network send can continue. The row remains history even if Revoke commits during the network call. Its display can report that the target peer is now revoked.
- If Revoke commits first, send returns `peer_revoked`. It creates no message and starts no network call.

The sender snapshots the exact `token_theirs` credential used by the authenticated request. Every `202`, `403`, other HTTP response, timeout, or network-failure result re-reads the `Peer` and its outbound message under the database write lock before it writes the result.

The locked post-send rules are:

- a late failing send result changes the outbound `PeerMessage` only when the locked row is still `pending` and `resolved_at` is null;
- when a callback or reconciliation already made the message final, every send result preserves its `status`, `error`, and `resolved_at`;
- a `202` response never changes message resolution;
- the final message guard is independent of the following Peer-side-effect rules;
- `202` updates `last_contact_at` only when the re-read Peer is still `active`, its current `token_theirs` exactly matches the request credential, and the current routing snapshot passes §6.1;
- `403 unknown_token` changes the Peer to `broken / remote_credential_rejected` only under those same active-state, current-credential, and routing conditions;
- a network failure, timeout, or any HTTP response other than `403 unknown_token` never changes `Peer.state`;
- when the Peer is already broken or revoked, its credential changed, or routing now fails §6.1, the result cannot change its state, reason, revocation metadata, credentials, provisional fields, `paired_local_base_url`, `accepted_at`, or `last_contact_at`.

The credential comparison targets stored token strings. The service uses the same constant-time comparison as other Peer credential checks.

### 6.4 Reconnection races

Revoke clears any reconnection slot under the same lock. A late verify or accept callback for that slot returns `unknown_token` and cannot restore credentials.

An address transition cancels all provisional reconnections before it starts optional new pairing requests.

---

## 7. Full reconnection

### 7.1 Owner action

The owner endpoint is:

```text
POST /api/peers/<id>/reconnect/
```

It accepts `broken` and `revoked`. It returns `bad_state` for `active` and initial pending rows.

Before minting or changing a provisional field, Reconnect re-reads the Peer and routing snapshot under the database write lock.

- An unavailable snapshot or empty canonical `peerBaseUrl` returns `peer_routing_unavailable`. It creates no slot and starts no network call.
- A Peer that still holds a current credential bound to another local origin returns `local_address_transition_required`. It creates no slot and starts no network call.
- A Peer whose current credentials were cleared by a completed managed transition can start under the new canonical origin. Section 7.2 classifies that attempt from `paired_local_base_url`.

After these checks, the endpoint starts a complete pairing attempt on the existing row. It mints fresh provisional credentials and never copies current credentials into the reconnection slot.

The remaining owner actions are:

```text
POST /api/peers/<id>/reconnect/verify/   {"code": "123456"}
POST /api/peers/<id>/reconnect/accept/   {"name": "Jacques"}
POST /api/peers/<id>/reconnect/refuse/
POST /api/peers/<id>/reconnect/cancel/
```

Verify applies to the locally initiated leg. Accept and Refuse apply to the locally received leg. Cancel clears a locally initiated request. A crossed slot can contain both legs and each action changes only the leg it owns until successful promotion clears the complete slot.

Refusing the received leg of a crossed slot changes it to `sent`, clears every received-owned field, and preserves the
slot-owned token pair and every sent-owned field. Cancelling the initiated leg changes it to `received`, clears every
sent-owned field, and preserves the slot-owned token pair and every received-owned field. Clearing the last remaining
leg empties the complete slot, including both tokens.

### 7.2 Wire reuse

Reconnection reuses the complete `/peer/handshake/request/`, `/peer/handshake/verify/`, and `/peer/handshake/accept/` protocol. It does not add a fast token-refresh protocol.

The request payload gains one optional field:

```json
{
  "display_name": "Paul",
  "base_url": "https://new.example",
  "previous_base_url": "https://old.example",
  "token": "fresh provisional token"
}
```

`previous_base_url` is absent when the request does not claim an address replacement.

Every outbound reconnection classifies the current canonical `peerBaseUrl` against that row's canonical `paired_local_base_url` before storing its exact request payload. The comparison targets canonical Python origin strings.

- Equal origins advertise the current value as `base_url` and omit `previous_base_url`. This is a same-origin reconnection.
- Different origins advertise the current value as `base_url` and the paired value as `previous_base_url`. This claims a local-origin replacement on the remote instance.

This classification applies to the optional address-change batch, its per-Peer Retry, and every later manual Reconnect after §11.3-§11.5. An empty current origin cannot start Reconnect under §7.1.

A first replacement claim requires a canonical `base_url` that does not already identify a Peer. An exact retry of the same stored request is the §7.6 exception. Any other request for an existing address with `previous_base_url` returns `invalid_payload`. An established match without `previous_base_url` uses same-origin reconnection, while an initial-pending match stays in the initial pairing flow.

The service routes verify and accept operations by the matching credential:

- an initial-pairing credential updates the main initial fields;
- a reconnection credential updates only the reconnection slot.

Both paths use shared verification, retry, held-accept, and crossed-request transition helpers. The implementation does not duplicate the pairing state machine.

One token resolver checks all current and provisional credential columns. Slot ownership does not grant authentication:

- `reconnect_token_ours` authenticates an incoming Accept callback only while a sent leg is present;
- `reconnect_token_theirs` authenticates an incoming Verify request only while a received leg is present;
- a slot-owned token preserved for the other leg cannot authenticate an absent leg or state;
- the resolver re-checks `reconnect_state` and the applicable leg under the §6.1 lock before mutation.

Token minting retries if its value already exists in any current or provisional credential column. An unknown, stale,
cancelled, or authentication-ineligible provisional token receives the same `403 unknown_token` response as any other
unknown token.

### 7.3 Incoming request for the same canonical address

When the canonical `base_url` matches an established row, the request populates that row's reconnection slot.

| Main state before request | Main state while pending | On local refusal | On complete acceptance |
|---|---|---|---|
| `active` | `active` | remain `active` | stay `active` with fresh credentials |
| `broken` | `broken` | remain `broken` | become `active` |
| `revoked` | `revoked` | remain `revoked` | become `active` |

An incoming request never changes active credentials before complete acceptance.

### 7.4 Code and human acceptance

Reconnect uses the same six-digit out-of-band proof and attempt limits as initial pairing.

The initiating human starts the request and submits the remote code. The receiving human sees a reconnection request, verifies its code state, and explicitly accepts or refuses it.

The owner Verify action requires a sent leg. An incoming Verify request requires a received leg, even when the
slot-owned `reconnect_token_theirs` remains after Refuse. Each entry re-checks the applicable leg under the write lock.

Successful acceptance on each instance atomically:

- requires both slot-owned provisional tokens;
- promotes `reconnect_token_ours` and `reconnect_token_theirs` to the corresponding current credential fields;
- promotes `reconnect_sent_accept_remote_display_name` for a sent-only slot, or `reconnect_remote_display_name` for a received or crossed slot, to the current remote display name;
- clears every reconnection field;
- sets `state = active`;
- clears `broken_reason` and `revoked_at`;
- updates `paired_local_base_url` to the current canonical local origin;
- preserves `accepted_at` when already set;
- updates `last_contact_at`.

A received-only Accept mints and persists `reconnect_token_ours` before the first outbound Accept callback. An uncertain
callback result preserves that token. Retry uses the same token and payload.

A sent-only Accept callback persists `reconnect_token_theirs`, `reconnect_sent_accept_remote_display_name`, and
`reconnect_remote_accepted_at` when local code confirmation is not complete. Later code confirmation promotes the same
token pair without requiring another request.

In a crossed slot, received-leg Accept reuses the existing `reconnect_token_ours`. An Accept callback for the sent leg
requires its returned token to equal the existing `reconnect_token_theirs`; a mismatch fails without mutation. The
callback does not create a second credential pair.

A refusal clears every received-owned field. A cancellation clears every sent-owned field. Each action preserves the
slot-owned token pair and the other leg's complete owned set while that other leg remains. Clearing the last leg clears
the pair. These actions leave the main state, current credentials, history, `accepted_at`, and `last_contact_at`
unchanged.

### 7.5 Crossed reconnection

If both users reconnect the same relationship concurrently, each incoming request merges into the existing provisional slot. `reconnect_state` becomes `crossed`.

Each side shares one slot-owned provisional credential pair across both legs, validates the code, and accepts locally.
One row and one provisional attempt exist on each instance.

The initiated and received legs keep their own timestamps. Refuse preserves `reconnect_sent_at`. Cancel preserves `reconnect_received_at`. Owner ordering and Retry age use the timestamp of the applicable leg.

### 7.6 Retry and idempotency

Before network work, an outbound reconnection commits its initiated leg with `reconnect_send_status = prepared`. The leg stores the exact display name and canonical origin fields used to build the payload. The slot stores its `reconnect_token_ours` credential.

A definitive pre-transmission failure is a local failure before the outbound HTTP client is invoked. It clears only the initiated leg. It preserves a received leg and the slot-owned token pair that the received leg can use. If no received leg remains, it clears the complete slot. It preserves the main state, current credentials, and history.

After the outbound HTTP client is invoked, transmission can have started. A timeout, network exception, non-success HTTP response, lost response, or process interruption therefore preserves the initiated leg, its token, and its exact stored request payload. The service sets `reconnect_send_status = retryable` and a non-secret `reconnect_send_error`. A 2xx request response sets `reconnect_send_status = sent`.

A `prepared` leg left by an interruption and a `retryable` leg both offer Retry. Retry rebuilds and resends the exact stored payload with the same `reconnect_token_ours`. It never reads a changed display name, reclassifies the origins, mints a credential, or creates another leg. The UI reports **Reconnect required** until the request succeeds.

The receiver treats an exact request retry as idempotent. The comparison targets canonical Python origin strings for
`base_url` and optional `previous_base_url`, and exact stored strings for `display_name` and `token`. For an initial row,
the optional wire value compares with `request_previous_base_url`, not replacement provenance. For a received
reconnection leg, an accepted first request has no replacement claim under §7.2, so both optional values are absent.
When all four request identity values match, the receiver returns HTTP 200 with the same success body. It does not
change a code, attempt counter, timestamp, token, display name, request-identity field, provenance field, or state.

A request that differs in any compared value is not an exact retry. This includes a request whose only changed value is
`previous_base_url`. It cannot overwrite an existing initial row or received leg. The existing-address and replacement
rules in §7.2-§7.5 return their defined error or crossed-request result.

Verify and Accept callbacks also tolerate a lost successful HTTP response. Their retries use the same slot-owned
provisional credential pair and apply their existing idempotent transition. A received-only Accept retry reuses the
persisted local token. A sent-only held Accept reuses the persisted remote token and remote display name.

The existing handshake rate limits and verification attempt ceilings count reconnection traffic. The pending-received capacity counts initial incoming rows plus established rows with a received or crossed reconnection leg.

---

## 8. Messages around revocation

### 8.1 Inbound pending message already stored locally

An inbound pending message that committed before Revoke remains readable.

The owner can still:

- deliver it to an existing local session;
- deliver it to a new local draft;
- refuse it locally.

Delivery or refusal updates the local message. It sends no status callback while the peer is revoked or otherwise unavailable.

The review dialog displays:

> This message came from a peer you revoked. You can resolve it locally, but TwiCC cannot notify the sender.

Reply controls remain unavailable until a successful reconnection.

### 8.2 Outbound pending message already accepted remotely

The local row stays pending until a status callback or §10 reconciliation provides a final status.

Its message detail displays:

> Sent to a peer that is now revoked.

Revocation never asks the remote instance to delete or resolve its stored copy.

---

## 9. Inbox visibility

### 9.1 Peer select

`frontend/src/components/peer/PeerInboxDialog.vue` changes the existing **All peers** option to **Current peers**.

The same select shows active and broken Peer options, followed by a labelled divider **Revoked peers**, followed by revoked Peer options. Initial pending requests remain in their separate request section.

### 9.2 Current peers view

With **Current peers** selected:

- message rows whose peer is revoked are excluded;
- text search matches title and content only inside the remaining current-peer rows;
- the pending-message badge and count exclude pending inbound messages from revoked peers.

### 9.3 Exact revoked-peer view

Selecting one revoked peer displays all matching inbound pending messages for that Peer and at most 200 other matching messages. A text query searches only that selected peer's title and content through the existing fuzzy-or-quoted-exact grammar.

The existing `history_has_more` callout reports when more matching non-pending messages exist. The user can narrow the same text filter to reach older retained rows. This feature adds no pagination or revoked-only loading path.

Pagination remains a future global inbox feature. It is not a separate revoked-Peer path.

The UI provides no combined **all revoked peers** message view. A text query cannot return revoked-peer messages while **Current peers** or a different peer is selected.

When the user revokes the peer currently selected in the inbox, the exact Peer selection returns to **Current peers**. The dialog stays open, and the existing text query remains unchanged.

### 9.4 REST filtering

`frontend/src/utils/peerInboxFilter.js::buildPeerInboxSearchUrl` adds `scope=current` when no exact Peer is selected. It keeps the existing `peer_id`, `q`, and `limit=200` parameters for exact-Peer and text filtering.

`src/twicc/peer/owner_views.py::peer_messages_list` accepts `scope=current`. This scope includes active and broken Peers and excludes revoked and initial-pending Peers. An explicit `peer_id` selects that exact Peer in any established state and takes precedence over `scope=current`.

The backend applies peer scope before text matching. It does not fetch revoked candidates and discard them after search.

The unfiltered store path applies the same current-Peer predicate locally. Selecting a revoked Peer continues to use the existing debounced, generation-guarded REST search path. That path returns all matching inbound pending messages, at most 200 other matching messages, and the existing `history_has_more` value.

---

## 10. Status reconciliation after reconnection

### 10.1 Query contract

After successful reconnection that reuses the same canonical Peer row on both instances, each instance snapshots its local outbound messages that are still pending for that Peer. It partitions every snapshot into deterministic chunks of at most 200 identifiers and queries the chunks sequentially.

A handshake request with `previous_base_url` creates a new remote-origin Peer on the receiver. The successful replacement starts no automatic status reconciliation for old history. On the instance that preserved its local Peer row, a later manual refresh uses the new credential against the receiver's new Peer row. An old message ID is unknown there and stays pending locally.

The authenticated route is:

```text
POST /peer/messages/statuses/
```

The request contains at most 200 identifiers:

```json
{"message_ids": ["pm_one", "pm_two"]}
```

The response is:

```json
{
  "statuses": {"pm_one": "delivered"},
  "unknown": ["pm_two"]
}
```

The request carries no title, payload, attachment, origin, thread field, or local session reference. A malformed identifier, duplicate identifier, non-list value, or list over the cap returns `400 invalid_payload` without a partial response.

The remote response reports, for each known inbound message:

- `pending`;
- `delivered`;
- `refused`.

An unknown ID is reported as unknown. It stays pending locally. TwiCC never retransmits the message.

### 10.2 Monotonic application

Reconciliation can move a local outbound row from pending to delivered or refused. It cannot move a final local status back to pending.

One failed reconciliation chunk does not change the reconnected Peer to broken or stop the next initial chunk. Each failed chunk enters its own retries after 10 seconds, 1 minute, and 5 minutes. It then stops automatically.

The message detail offers **Refresh status** only for an outbound pending row whose Peer is active and passes the credential-sensitive gate. This action queries only that message and produces no toast. A broken or revoked Peer exposes no refresh action.

### 10.3 Callback convergence

A send result, normal status callback, and reconciliation can race. Callback and reconciliation updates use the existing
message-resolution lock and apply the same monotonic rule. Under §6.3, a late failing send result can resolve only a
still-pending row. A `202` or any send result that finds a final row preserves its message fields.

---

## 11. Changes to this instance's `peerBaseUrl`

### 11.1 Extend the existing correlated WebSocket path

A `peerBaseUrl` write remains a per-field Apply in `frontend/src/composables/useOriginSettingsForm.js::apply`. It uses the existing path through `frontend/src/stores/settings.js::sendOriginSetting`, `frontend/src/composables/useWebSocket.js::sendSyncedSettings`, and `src/twicc/asgi.py::UpdatesConsumer._handle_update_synced_settings`.

The owner Web form includes a `peer_transition` object on every `peerBaseUrl` Apply:

```json
{
  "type": "update_synced_settings",
  "settings": {"peerBaseUrl": "https://new.example"},
  "baseVersion": 42,
  "request_id": "origin-write-id",
  "peer_transition": {
    "confirmed": true,
    "reconnect_active": true
  }
}
```

The client values follow this exhaustive contract:

| Apply | `confirmed` | `reconnect_active` |
|---|---:|---:|
| First Apply or complete no-op | `false` | `null` |
| Confirmed valid-to-empty | `true` | `null` |
| Confirmed valid-to-different-valid | `true` | explicit boolean |
| Confirmed mismatch recovery to an empty address | `true` | `null` |
| Confirmed mismatch recovery to a valid address | `true` | explicit boolean |

The correlated `synced_settings_result` gains an optional `peer_transition` object for this field:

```json
{
  "kind": "replace",
  "canonical_base_url": "https://new.example",
  "reconnect_active_default": true
}
```

`kind` is `enable`, `no_op`, `disable`, `replace`, `recover_disable`, or `recover_replace`. `reconnect_active_default` is present only for `replace` and `recover_replace` confirmation. It is always `true`.

The direct result status is:

| Status | Meaning |
|---|---|
| `accepted` | Enable, complete no-op, or confirmed local transition committed. |
| `confirmation_required` | The canonical proposal needs the owner's confirmation. The settings version and both stores are unchanged. |
| `rejected` | Validation, stale version, malformed transition intent, or interrupted-transition failure. |

`src/twicc/asgi.py::UpdatesConsumer._handle_update_synced_settings` validates this object and passes it as an explicit `peer_transition` argument to `src/twicc/core/services/settings_mutation.py::update_synced_settings`. Only this authenticated owner WebSocket handler can supply that argument. `update_synced_settings_from_payload` and CLI callers cannot supply it.

`update_synced_settings` remains the single rich settings entry point. A `peerBaseUrl` Apply delegates inside it to the owner-only Peer address lifecycle branch. That branch reuses the existing authoritative normalizer, relationship validator, settings writer, version owner, and broadcast contract.

The first unconfirmed Apply lets Python canonicalize the input and classify the transition. Empty-to-valid and a complete canonical no-op can succeed immediately. Valid-to-empty, valid-to-different-valid, and a stored address with mismatched established Peer bindings return `confirmation_required` through the correlated `synced_settings_result`.

After confirmation, the form sends a new `request_id`, the current `baseVersion`, the same raw input, and the explicit transition choices. The backend canonicalizes and classifies again. It never trusts the transition kind supplied by the client.

`baseVersion` provides the existing synchronized-settings optimistic check. A stale version rejects the complete mutation before the address or a Peer changes. Reapplying the same canonical address is a no-op and needs no confirmation.

The no-op rule applies only when every established credential is already bound to that canonical address. A mismatch returns the explicit recovery confirmation instead.

The backend requires `confirmed = true` for valid-to-empty, valid-to-different-valid, and mismatch-recovery transitions. It also requires an explicit boolean `reconnect_active` for valid-to-different-valid and mismatch recovery. Missing confirmation returns `confirmation_required` with no settings, Peer, credential, or network change.

A generic WebSocket settings patch without `peer_transition` can carry the unchanged `peerBaseUrl` inside its existing full snapshot. It receives `managed_setting` when it attempts to change the canonical value, including empty-to-valid. `twicc settings set|unset peerBaseUrl` rejects the key before submitting a settings patch. Direct edits to `settings.json` have no defined behavior.

### 11.2 Persistence order and interrupted Apply

The managed service first acquires the cancellation-safe database write lock through `src/twicc/providers/db_writer.py::run_under_db_write_lock`. Inside that lock, one synchronous worker acquires `src/twicc/synced_settings.py::_settings_lock`. The worker re-reads the settings version, canonical addresses, routing relationships, and affected Peer rows.

The synchronous worker holds `_settings_lock` through the settings write and SQLite transaction. It performs no `await` while that thread lock is held. The generic settings path releases `_settings_lock` before any asynchronous transition work, so it creates no reverse held-lock path.

For a confirmed transition, it:

1. writes the canonical `peerBaseUrl` through the existing atomic settings-file replacement;
2. applies every Peer state and credential change, clears obsolete provisional slots, and creates the selected new provisional reconnection slots in one SQLite transaction;
3. releases both locks;
4. broadcasts the authoritative settings and Peer rows;
5. returns the local result and optional batch descriptor to the WebSocket handler;
6. schedules the optional network batch independently of the initiating connection.

If the process stops after the settings write but before the SQLite transaction commits, `paired_local_base_url` blocks every established credential. TwiCC performs no startup repair. The next owner Apply of the already-stored address returns the mismatch-recovery confirmation and can complete the transition from the still-unchanged Peer rows.

If the SQLite transaction fails after the settings write, the direct result is rejected with `transition_incomplete`. The service broadcasts the authoritative stored address, starts no network request, and leaves the unchanged Peer rows blocked by the same mismatch gate.

Mismatch recovery to an empty stored address completes the invalidation in §11.4. Mismatch recovery to a valid stored address applies §11.5 to established credentials bound to another local origin. Each optional request carries that Peer row's `paired_local_base_url` as `previous_base_url`. Only rows that are active when recovery starts can enter the optional batch.

If the SQLite transaction commits, every affected Peer is already safe before optional network work starts. A later process stop cannot restore an old credential. Existing per-Peer Retry handles an optional request that did not complete.

This design adds no transition table, marker file, or hidden synced setting.

### 11.3 Empty to valid

Setting a valid origin when the current value is empty behaves like first-time configuration.

It enables the public Peer routes. It changes no existing Peer, starts no pairing request, scans no history, and offers no automatic restoration.

### 11.4 Valid to empty

The UI asks:

> Disable peer messaging?

> This will invalidate every current peer relationship and cancel all pending pairing requests. Existing message history is preserved. If you configure a peer address again later, you must reconnect each peer yourself.

After confirmation, the local transition:

- writes the empty setting;
- changes every active peer to `broken / local_address_disabled`;
- clears current credentials from active and broken peers;
- cancels initial pending rows and provisional reconnections;
- leaves revoked peers revoked;
- starts no network request.

Setting a valid origin later follows §11.3. The system remembers no automatic reconnection group.

### 11.5 Valid origin A to valid origin B

The UI asks:

> Change peer address?

> Your old address will stop working for every current peer. TwiCC will invalidate the existing credentials and preserve all message history.

It presents one option, checked by default:

> Send new pairing requests to currently active peers

Its help text states:

> Your old address will stop working for every existing peer. TwiCC can start a new verified pairing with peers that are active now. Clear this option if you prefer to reconnect them individually later.

Before writing, the service snapshots the IDs that are active at the start. After confirmation, it:

1. writes canonical B;
2. changes the snapshotted peers to `broken / local_address_changed`;
3. clears current credentials from active and broken peers;
4. cancels initial pending rows and provisional reconnections;
5. leaves revoked peers revoked;
6. starts optional pairing requests only for the snapshotted active IDs.

The local change is effective before any optional request. Network failure never restores A or old credentials.

### 11.6 Optional batch results

The optional requests use bounded concurrency and independent results.

The accepted `synced_settings_result` follows the safe local commit. It does not wait for optional network requests and precedes every progress frame. The backend continues the selected batch if the Settings dialog closes or the initiating WebSocket disconnects.

While that connection exists, correlated `peer_transition_progress` frames contain `request_id`, optional `peer_id`, one result (`sent`, `failed`, or `skipped`), cumulative sent, failed, skipped, and total counts, and `complete`. These frames are transient UI progress, not a durable transition marker. Peer row broadcasts remain authoritative.

For each selected peer:

- a sent request shows **Reconnection pending**;
- an uncertain outbound reconnection request remains **Reconnect required** with Retry and preserves its exact stored payload and credential;
- one failure does not cancel another success;
- closing the dialog does not roll back the address or any result.

The UI reports sent, failed, and skipped counts. It never includes peers that were broken, revoked, or initial-pending before the change.

Each selected request uses the existing local `Peer` and its reconnection slot because the remote origin did not change locally. Its handshake payload advertises B and carries A as `previous_base_url`. The remote instance therefore creates a new pending `Peer` for B while this instance preserves the existing row and history for that remote origin.

A per-Peer Retry reuses the stored B/A payload under §7.6. A later manual Reconnect after batch opt-out or a cleared failed leg re-runs the §7.2 classification from the row's `paired_local_base_url`. The optional batch has no separate `previous_base_url` rule.

---

## 12. A remote peer changes address

### 12.1 Claim and matching

The pairing request's optional `previous_base_url` is a claim. The receiver strictly canonicalizes it.

The receiver links the new pending row to an old row only when the canonical previous origin exactly matches an established active, broken, or revoked Peer. It never uses an initial-pending row, local name, or remote display name.

The new canonical `base_url` always creates a new `Peer` and new history on the receiving instance.

That new row uses the existing initial-pairing fields and pipeline. `previous_base_url` and `replaces_peer` only add replacement context to the pending row; they do not use its reconnection slot.

Every accepted initial request stores its exact optional wire value in `request_previous_base_url` while the row remains
pending. A matching claim also stores `previous_base_url` and `replaces_peer`. An unmatched claim stores neither
provenance field.

### 12.2 Before verification

When the claim matches an active, broken, or revoked old Peer, Manage Peers displays:

> This request claims to replace an existing peer address.

It shows the old and new origins. It prefills the old local name in an editable name field.

The UI does not call the claim verified before successful code verification.

### 12.3 After verification

After successful code verification, the UI displays:

> Verified address replacement request.

It provides one checked option:

> Revoke the old peering after accepting the new one

The help text states:

> The peer has moved to a new address, so the old peering can no longer be used. Revoking it hides its messages from the default inbox while preserving its retained history. If you keep it, TwiCC marks it as unavailable and you can revoke it later.

### 12.4 Acceptance result

Accepting the request activates the new `Peer`.

- With the option checked, the old Peer becomes revoked and its old credentials are cleared.
- With the option cleared, the old Peer becomes `broken / remote_address_changed` and its old credentials are cleared.

The old Peer never remains active after accepting a verified replacement.

Acceptance locks and re-reads both rows under the database write lock. In that same locked transaction, it clears every provisional reconnection field on the old Peer before applying either old-row disposition. A later callback for the cleared slot returns `unknown_token` and cannot restore credentials or reactivate the old Peer.

If the old row became revoked after the confirmation opened, it stays revoked regardless of the checkbox. The new row can still be accepted.

If the old Peer was already revoked, the UI states that preserved history exists. It offers no checkbox and leaves that row revoked.

If no exact old origin matches, the request is a normal initial pairing. TwiCC exposes no replacement association and
transfers no history. While that row remains pending, its internal `request_previous_base_url` still identifies an exact
request Retry. Successful acceptance clears the request-identity field and retains no replacement provenance.

---

## 13. Owner, CLI, and agent surfaces

### 13.1 Human-only relationship mutations

Revoke, Reconnect, and replacement acceptance are owner REST operations. A managed `peerBaseUrl` transition is an owner Settings UI operation over the correlated WebSocket path in §11.

They have no drop-request kind, RPC command, MCP tool, or bundled skill instruction.

`src/twicc/cli/settings/_keys.py` classifies `peerBaseUrl` as readable but managed. Bare `twicc settings` and `twicc settings get peerBaseUrl` expose its value. `twicc settings set peerBaseUrl` and `twicc settings unset peerBaseUrl` return `managed_setting` and direct the human to the Web Settings form.

Provider and notification settings commands stay restricted to their owned keys and cannot supply `peerBaseUrl`.

### 13.2 Read-only peer list

`twicc peers` excludes revoked rows. It shows broken rows and includes their structured reason.

### 13.3 Send resolution

`twicc peer-send` ignores revoked rows during name resolution.

An explicit revoked Peer ID returns:

```text
peer_revoked
```

An explicit broken Peer ID returns the existing unavailable result with the structured reason when available.

### 13.4 Message reads

`twicc peer-message` can read retained messages from a revoked peer. Its output identifies that the peer is now revoked.

The peer-message and peer-send skills explain the read and send results. Any skill edit bumps the bundled plugin patch version.

---

## 14. Manage Peers UI

### 14.1 Sections and actions

Manage Peers shows established active and broken rows in **Peers**. It shows revoked rows in **Revoked peers**, sorted by newest `revoked_at` first.

An active row offers Rename and Revoke. A broken row offers Rename, Reconnect, and Revoke. A revoked row offers Rename and Reconnect.

Add a Peer routes an exact broken or revoked address to the existing row's Reconnect flow.

Manage Peers disables Reconnect while the routing snapshot is unavailable or canonical `peerBaseUrl` is empty. It shows the same Settings guidance as Add a Peer. A `local_address_transition_required` result starts no provisional attempt and directs the owner to complete the managed Settings transition.

Before Reconnect starts, the UI states:

> Reconnecting creates new credentials and requires the remote user to verify and accept a new pairing request. Existing message history stays attached to this peer.

### 14.2 Revoke confirmation

The confirmation uses:

> Revoke peer “Jacques”?

> This instance will stop sending messages to and accepting messages from this peer. Existing message history is preserved. The remote user is not notified.

Buttons are **Keep peer** and danger-style **Revoke peer**.

When the peer owns local pending inbound messages, the dialog also shows their count and states that they remain available in the peer's retained history.

Success moves the row to **Revoked peers** without closing Manage Peers.

### 14.3 Reconnection presentation

The existing pairing code, verification, and acceptance controls are reused with reconnection-specific labels. The main row remains visibly Active, Broken, or Revoked until success.

The UI does not describe a provisional request as an active relationship.

---

## 15. Verification

### 15.1 Backend model, service, and owner API

`tests/test_peer_handshake.py`, `tests/test_peer_messages.py`, `tests/test_settings_mutation.py`, `tests/test_synced_settings_ws.py`, `tests/test_settings_cli.py`, `tests/test_origin_policy.py`, `tests/test_share_host_gate.py`, and a new `tests/test_peer_revocation_migration.py` cover:

- canonical origin normalization on every Peer input;
- empty local routing acceptance and empty remote-origin rejection;
- one canonical remote origin across all states;
- explicit migration failure when any development-only Peer row exists;
- `PROTECT` history retention;
- `replaces_peer` provenance using `PROTECT` with the `replacement_peers` reverse name;
- Revoke transitions, idempotency, token clearing, and pending-only deletion;
- old-token uniform `unknown_token` responses with no side effects;
- incoming-message and outgoing-send lock ordering against Revoke;
- late outbound `202`, `403 unknown_token`, other HTTP, timeout, and network-failure results re-reading under the lock and preserving revoked metadata;
- delivered and refused callbacks winning against each late `403 unknown_token`, other HTTP failure, timeout, and network failure;
- every late send result preserving `status`, `error`, and `resolved_at` after a callback or reconciliation made the message final;
- a late `202` preserving message resolution while applying only its separately gated Peer contact update;
- outbound `403 unknown_token` breaking only a still-active Peer whose current credential matches the request credential;
- network failures, timeouts, other HTTP responses, status operations, and reconciliation failures leaving an active Peer active;
- routing-unavailable and local-origin-mismatch operations stopping without lifecycle mutation;
- a wrong request authority stopping before Peer authentication without lifecycle mutation;
- the existing origin write preserving normalization, optimistic versioning, request correlation, and authoritative broadcast;
- owner Web transition intent, confirmation, persistence ordering, and mismatch recovery;
- confirmation-result shape, accepted-before-progress ordering, and batch continuation after disconnect;
- local resolution of stored pending inbound messages without callbacks;
- generic WebSocket and CLI writes rejecting `peerBaseUrl`.

A test fails when a forbidden row is deleted, an invalidated credential survives, a suspended operation mutates a Peer,
a network stub is called after the losing race, a late send result changes a final message field, a rejected settings
write changes either store, or a revoked message changes the default badge count.

### 15.2 Handshake and reconciliation protocol

Focused protocol tests cover:

- reconnect from broken and revoked while preserving the main state until acceptance;
- Reconnect start with unavailable routing, an empty local origin, a live mismatched credential, and cleared credentials after a managed transition;
- same-origin reconnect rejecting a replacement claim;
- incoming reconnect against an active row without replacing active credentials early;
- fresh credential promotion and provisional-field clearing;
- database constraints rejecting any received-owned field in `sent` state and any sent-owned field in `received` state;
- database constraints rejecting any sent-owned field without a sent leg and any received-owned field without a received leg;
- database constraints requiring a local provisional token for a sent leg, a remote provisional token for a received leg, and both tokens blank only when the complete slot is empty;
- a received-only Accept persisting and reusing `reconnect_token_ours` across an uncertain callback result;
- a sent-only held Accept persisting `reconnect_token_theirs` and its sent-owned remote display name until code confirmation;
- a crossed Accept reusing the slot-owned token pair and rejecting a different returned remote token;
- Refuse changing `crossed` to `sent`, clearing every received-owned field, and preserving the token pair and every sent-owned field;
- Cancel changing `crossed` to `received`, clearing every sent-owned field, and preserving the token pair and every received-owned field;
- Refuse or Cancel on the last leg clearing the complete slot and both provisional tokens;
- retry, held accept, and crossed reconnect with independent sent and received timestamps;
- owner serialization deriving leg presence from `reconnect_state`, exposing only present-leg public fields, and exposing no token;
- owner serialization omitting `request_previous_base_url` and every slot-owned token;
- provisional token resolution accepting a token only for its applicable present authentication leg, including after crossed Refuse and Cancel;
- definitive pre-transmission failure clearing only the initiated leg;
- uncertain transmission preserving the exact token and payload for Retry;
- idempotent lost-response recovery and conflicting retry-payload rejection;
- exact initial request Retry after an unmatched replacement claim, using the stored `request_previous_base_url`;
- a conflicting initial retry whose only changed value is `previous_base_url`;
- matched and unmatched request identity remaining separate from verified replacement provenance;
- `request_previous_base_url` clearing when the initial row leaves a pending state;
- batch opt-out followed by manual Reconnect;
- failed optional batch followed by exact per-Peer Retry;
- disable then re-enable at the same local origin and at a different local origin;
- same-address row reuse and new-address row creation;
- `previous_base_url` canonical matching, unmatched request-identity retention, provenance clearing or persistence, and verified replacement outcomes;
- remote replacement acceptance clearing the old Peer's reconnection slot before either disposition, including late callbacks for both checkbox outcomes;
- present-empty `previous_base_url` rejection;
- outbound-pending-only status queries;
- deterministic multi-chunk coverage above 200 pending messages;
- address replacement skipping automatic old-history status queries;
- manual refresh eligibility and replacement-history `unknown` results;
- unknown status IDs remaining pending;
- monotonic status application and no retransmission;
- reconciliation failure leaving the Peer active.

A test fails if provisional data reaches the main credential fields early, a one-sided flow loses either fresh token, an
absent-leg token authenticates, a last-leg clear leaves a token, serialization exposes an absent-leg field or internal
request identity, an exact request retry changes the pending row, a second same-address Peer appears, history moves to a
new-address Peer, or reconciliation sends message content.

### 15.3 Frontend pure contracts and build

`frontend/src/utils/publicOrigin.test.js`, `frontend/src/utils/originSettingsForm.test.js`, `frontend/src/composables/useOriginSettingsForm.test.js`, `frontend/src/utils/peerInboxFilter.test.js`, a new `frontend/src/utils/peerLifecycle.test.js`, and a new `frontend/src/utils/peerAddressTransition.test.js` cover:

- Current peers versus exact revoked-peer filtering;
- all matching pending inbound rows, the 200-row non-pending cap, and `history_has_more` for exact revoked-Peer filtering;
- text-search scope before matching;
- selectable peer grouping and revoked divider placement;
- badge exclusion for revoked-peer pending messages;
- Manage Peers grouping and revoked ordering;
- unconfirmed, confirmed, stale-version, and interrupted local-address Apply results;
- local-address transition summaries and per-peer batch results;
- the existing per-field request correlation and lost-connection behavior.

A frontend test fails if Current peers exposes revoked history, exact revoked-peer selection drops a matching pending inbound row or changes the existing non-pending cap, an unconfirmed transition mutates state, or a correlated result updates the wrong Apply request.

`cd frontend && npm test` runs these pure contracts with the repository's frontend tests. `cd frontend && npm run build` fails on an unresolved import or invalid production bundle.

This feature adds no Vue component-test harness. Component interaction remains in the manual matrix.

### 15.4 Manual acceptance matrix

| Case | Observable result | Failure signal |
|---|---|---|
| M1 — Revoke active | Row moves to Revoked peers; history remains selectable. | Row disappears or history is lost. |
| M2 — Old token after Revoke | Remote send fails without a local row, toast, or badge change. | Any local message or notification appears. |
| M3 — Pending inbound at Revoke | Message remains readable and locally deliverable or refusable with the revoked warning. | Message disappears or a callback is attempted. |
| M4 — Inbox default | Current peers and text search omit revoked history. | Revoked history appears without selecting that peer. |
| M5 — Revoked peer filter | Exact selection shows all matching inbound pending messages and at most 200 other matching messages for that revoked Peer; `history_has_more` reports more non-pending matches. | Another peer appears, a matching pending inbound message is missing, more than 200 other rows appear, or the existing overflow callout is wrong. |
| M6 — Reconnect | Full code verification is visible; a lost Accept response or held Accept recovers through Retry and success reactivates the same local row. | Current credentials change before success, Retry starts a fresh attempt, an acceptance wedges, or a second same-address row appears. |
| M7 — Crossed reconnect | Both instances converge through one attempt per side; Refuse or Cancel leaves the other leg actionable. | A surviving leg loses its credential pair, an absent leg accepts a token, or duplicate attempts or rows appear. |
| M8 — Disable local address | Confirmation appears; peers become unavailable; no network work starts. | A relationship remains usable or reconnect starts automatically. |
| M9 — Replace local address, batch enabled | Only peers active at confirmation enter independent pairing requests. | Broken or revoked peers enter the batch, or one failure rolls back another result. |
| M10 — Replace local address, batch disabled | Address changes and peers require individual reconnect. | TwiCC starts a request. |
| M11 — Remote replacement claim | A matching claim is labelled before code and verified after code; an unmatched claim appears as normal initial pairing. | An unverified or unmatched claim appears trusted. |
| M12 — Accept remote replacement | New address has new history; old row becomes revoked or broken according to the checkbox. | History merges or the old row stays active. |
| M13 — Manual status refresh after same-origin reconnect | One outbound pending status updates without a toast or retransmission. | Payload is sent or a final status regresses. |
| M14 — Revoke selected inbox peer | The inbox stays open, returns to Current peers, and preserves the existing text query; revoked history disappears until that exact peer is selected again. | The revoked selection remains active, the text query resets, revoked history remains in the default view, or the inbox closes. |
| M15 — Unmatched claim Retry | After a lost request response, exact Retry resumes the same normal pending request without replacement provenance. | Retry creates a row, changes the code or request metadata, or displays a replacement association. |

### 15.5 Scope inspection

The implementation diff must not add:

- a second relationship or pairing-request model;
- an agent-facing Revoke or Reconnect mutation;
- a history merge between different canonical remote origins;
- a compatibility repair path for external Peer data;
- an edit path for `Peer.base_url`;
- a durable local-address transition table, marker file, or hidden synced setting;
- a message archive field.

---

## 16. Implementation lots

### Lot 1 — Revocation and retained history

Deliver the schema migration, canonical remote identity, `PROTECT`, revoked and broken metadata, credential binding,
Revoke and pending-only deletion APIs, the final-`PeerMessage` late-send guard, Manage Peers revocation UI, and
revoked-aware extension of the existing inbox filters.

This lot also delivers the runtime safeguards for `twicc peers`, `twicc peer-send`, and `twicc peer-message` in §13.2-§13.4. The list excludes revoked rows. An explicit revoked send returns `peer_revoked`. A retained message read identifies its revoked Peer.

This lot ends with a complete usable Revoke flow. It does not expose Reconnect yet.

### Lot 2 — Full reconnection and status reconciliation

Deliver the provisional slot with its slot-owned credential pair and leg-owned metadata, shared handshake routing,
fresh credential promotion, one-sided and crossed Accept recovery, retry behavior, status query protocol, manual
Refresh status, and Reconnect UI.

### Lot 3 — Local and remote address changes

Deliver the managed extension of the existing `peerBaseUrl` WebSocket Apply, local address confirmations, fail-closed
interrupted-Apply recovery, the optional active-peer batch, internal `request_previous_base_url`, verified
`previous_base_url` provenance, remote replacement UI, and old-peer disposition.

This lot also classifies `peerBaseUrl` as readable but managed in the generic CLI. `twicc settings set peerBaseUrl` and `twicc settings unset peerBaseUrl` reject it before any settings patch can bypass the managed transition.

### Lot 4 — Skills and repository documentation

Deliver bundled skill wording, its required plugin version bump, and repository documentation only. This lot changes no runtime contract.

The lots are cumulative implementation boundaries on one feature branch. A deployment must include every completed migration and its matching runtime code.
