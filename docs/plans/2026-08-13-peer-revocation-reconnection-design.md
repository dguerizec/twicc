# Peer Revocation, Reconnection, and Address Changes — Design

**Status:** draft checkpoint; owner decisions captured; not final; adversarial review not started
**Date:** 2026-08-13
**Scope:** preserve peer history when a relationship ends, provide an explicit full-pairing reconnection flow, and make local or remote address changes fail closed.
**Required revisit before review:** reconcile the address and settings contracts, especially §5, §6, §11, and §12, with the parallel public-origin and Peer-origin work. The managed `peerBaseUrl` write path is provisional until that reconciliation is complete.

---

## 0. Relationship to earlier designs

`docs/plans/2026-07-24-peer-messaging-design.md` is the frozen founding design. It remains unchanged.

This design replaces the founding design's decisions about deleting an established peer, cascading its message history, re-pairing a known address, and editing peer addresses. It also replaces the corresponding historical edge cases in `docs/plans/2026-08-11-peer-threading-design.md`. The threading design remains authoritative for threading behavior.

### 0.1 Development-only owner ruling

The complete `peer-system` branch is private development work on the owner's machines. The Peer System has never been released, distributed, or deployed for another user. No supported external installation, client, database, or Peer wire version exists.

The owner can recreate every Peer test instance and its data from zero at any time. The implementation therefore targets only the final contract in this design.

It requires no backward compatibility between Peer System revisions. It adds no version negotiation, dual read or write, fallback, historical-schema adapter, duplicate merge, or invalid-row repair. Earlier branch commits and earlier designs are development history, not supported versions.

An invalid or duplicate development row can stop the migration with an explicit diagnostic. Resetting that development database is an accepted result.

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
- an agent, skill, CLI, or MCP mutation for Revoke or Reconnect;
- changes to inbox rendering or search beyond revoked-peer visibility and selection;
- repair of development-only Peer rows before the migration.
- compatibility with an earlier Peer frontend, backend, schema, or wire contract.

---

## 1. Current implementation

This section records only the current behavior that this design changes.

### 1.1 Relationship lifetime

`core.models.PeerState` has `pending_sent`, `pending_received`, `active`, and `broken`. It has no revoked state or structured broken reason.

`core.services.peer_mutation.delete_peer` deletes a `Peer` in any state. `PeerMessage.peer` uses `CASCADE`, so deleting an established peer deletes all of its messages.

`DELETE /api/peers/<id>/` exposes that deletion. `PeersManagerDialog.vue` labels the action **Remove**.

### 1.2 Pairing identity

`Peer.base_url` is not unique. `peer_mutation.normalize_base_url` only trims whitespace and a trailing slash. The initial handshake excludes `broken` rows during address matching, so the same address can produce a second `Peer`.

The repository now has the strict shared `core.services.public_origin.normalize_public_origin` contract. Peer relationship creation and inbound handshake validation do not yet use it.

Per-peer address editing is already disabled. `PATCH /api/peers/<id>/` rejects `base_url`, and Manage Peers has no Edit URL action.

### 1.3 Pairing storage

Initial pairing uses the main `Peer` state and credential fields. A second pairing attempt for an established address has no separate provisional storage.

An active relationship can become `broken` after a remote `403`. The current model does not record why it became broken.

### 1.4 Local public address

`peerBaseUrl` is a synchronized setting. The shared settings mutation path can replace or clear it without coordinating `Peer` rows or credentials.

A `Peer` does not record the local public origin used when its credentials were established. A process crash between a settings write and Peer updates therefore has no durable mismatch gate.

### 1.5 Inbox and agent reads

The inbox peer select starts with **All peers**. Search by peer or text does not distinguish revoked history because no revoked state exists.

`twicc peers` lists active and broken peers. `twicc peer-send` treats `broken` as one combined unavailable state.

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
| `remote_rejected_or_unreachable` | A remote failure made the existing relationship unusable without proving a more specific cause. |

The last value remains deliberately non-assertive. A remote `403` does not prove that the remote human revoked this instance.

### 2.3 Revocation fields

`Peer.revoked_at` records the local revocation time. It is non-null only in `revoked`.

`accepted_at` keeps the first successful pairing time. Reconnection does not replace it.

Database checks enforce the state metadata:

- `revoked` requires `revoked_at`, and every other state requires it to be null;
- `broken` requires a defined `broken_reason`, and every other state requires it to be blank;
- a non-empty `reconnect_state` owns one coherent provisional slot;
- an empty `reconnect_state` requires every provisional field to be empty;
- `replaces_peer` cannot reference the same row.

### 2.4 Local-origin binding

`Peer.paired_local_base_url` stores the canonical `peerBaseUrl` used by the most recent successful initial pairing or reconnection.

Every credential-sensitive operation compares it with the current canonical `peerBaseUrl`. Section 6 defines the result of a mismatch.

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
- `reconnect_requested_at`.

These fields never replace active credentials before a complete reconnection succeeds.

The slot invariants are:

- `sent` requires `reconnect_token_ours` and `reconnect_requested_at`;
- `received` requires `reconnect_token_theirs`, `reconnect_verification_code`, and `reconnect_requested_at`;
- `crossed` requires both provisional tokens, the verification code, and `reconnect_requested_at`;
- later held-accept fields can fill while the state remains `sent`, `received`, or `crossed`;
- clearing the slot clears all fields in one database write.

### 2.6 Address replacement claim

An initial pending `Peer` can store:

- canonical `previous_base_url`;
- nullable self-reference `replaces_peer`.

These fields describe a claimed remote address replacement. They do not merge either row or either history.

### 2.7 Owner serialization

`serialize_peer` adds `revoked_at`, `broken_reason`, `previous_base_url`, and `replaces_peer_id`.

It also adds a `reconnect` object when the provisional slot is non-empty. The object exposes state, timestamps, remote display name, crossed status, and the local verification code when the receiving human must see it. It exposes no current or provisional token.

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
| D8 | Reconnection reconciles only local outbound pending statuses and never retransmits content. | §10 | §15.2 |
| D9 | Every credential-sensitive operation fails closed when the paired local origin differs from the current origin. | §6 | §15.1, §15.2 |
| D10 | Disabling or replacing `peerBaseUrl` invalidates existing relationships before any optional network work. | §11 | §15.1, §15.4 |
| D11 | A new remote origin creates a new `Peer` and new history, even when it claims to replace a known origin. | §12 | §15.2, §15.4 |
| D12 | A replacement claim uses exact canonical origin matching and becomes trusted only after code verification. | §12 | §15.2, §15.4 |
| D13 | Revoke, Reconnect, and local-address relationship transitions are human-only. | §13 | §15.1, §15.3 |
| D14 | Initial incomplete requests can still be cancelled or refused and deleted. | §4 | §15.1 |
| D15 | Peer identity validation uses the shared public-origin normalizer. | §5 | §15.1 |
| D16 | Revocation and message arrival or send are serialized by the database write lock. | §6 | §15.1 |

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

These inputs use `core.services.public_origin.normalize_public_origin`:

- the local `peerBaseUrl` setting;
- the remote address entered in Add a Peer;
- `base_url` in an inbound handshake request;
- non-empty `previous_base_url` in an inbound handshake request.

Python remains authoritative. The frontend mirror provides immediate feedback only.

The canonical value keeps only an HTTP or HTTPS origin. It has no credentials, path, query, or fragment. The shared helper handles case, IDNA, IP literals, and default ports.

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

The migration:

1. canonicalizes every valid `Peer.base_url` with the shared contract;
2. reports invalid rows with their Peer IDs and values;
3. reports canonical collisions with all involved Peer IDs;
4. adds the database uniqueness constraint;
5. changes `PeerMessage.peer` to `PROTECT`;
6. adds the lifecycle, local-origin, reconnection, and replacement fields.
7. adds the state-metadata checks from §2.3.

It does not merge rows or choose a winner. The development-only owner ruling in §0.1 applies. A developer can reset an incompatible test database instead of adding a compatibility path.

Existing `active` and `broken` rows keep their current states. Initial pending rows keep their current states. The migration does not infer revocation.

Existing broken rows receive `broken_reason = remote_rejected_or_unreachable`. This supplies the required reason without claiming a more specific historical cause.

For an established development row, `paired_local_base_url` is initialized from the current canonical `peerBaseUrl`. If that setting is empty or invalid, the migration leaves the field blank. Startup reconciliation then applies §6 without inventing credentials or an origin.

---

## 6. Live availability gate and races

### 6.1 Credential-sensitive gate

Inbound message receipt, outbound send, status callback, status query, reconciliation, and handshake completion re-read the Peer under the database write lock.

The operation is allowed only when:

- the main state allows that operation;
- the expected current or provisional token matches;
- the current canonical `peerBaseUrl` is non-empty;
- an established credential uses `paired_local_base_url == peerBaseUrl`.

An established-peer local-origin mismatch fails closed. Under the same lock, TwiCC:

- sets `state = broken`, unless it is already `revoked`;
- sets `broken_reason = local_address_changed` when a non-empty current address differs;
- sets `broken_reason = local_address_disabled` when the current address is empty;
- clears current credentials and provisional reconnection fields;
- emits an owner update after the lock.

The request performs no authenticated remote call after this repair.

Startup reconciliation applies the same repair to all established rows. This closes a crash window between synchronized settings storage and SQLite.

### 6.2 Incoming message versus Revoke

Token resolution before the lock is not authorization. `receive_peer_message` re-resolves the token and relationship state inside the write lock before row creation.

- If message creation commits first, the local message remains stored. A later Revoke does not delete it.
- If Revoke commits first, receipt returns `403 unknown_token` and creates no message.

No intermediate result creates a partial message.

### 6.3 Outgoing send versus Revoke

Peer validation and outbound `PeerMessage` creation share the database write lock.

- If message creation commits first, its network send can continue. The row remains history even if Revoke commits during the network call. Its display can report that the target peer is now revoked.
- If Revoke commits first, send returns `peer_revoked`. It creates no message and starts no network call.

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

The endpoint starts a complete pairing attempt on the existing row. It mints fresh provisional credentials and never copies current credentials into the reconnection slot.

The remaining owner actions are:

```text
POST /api/peers/<id>/reconnect/verify/   {"code": "123456"}
POST /api/peers/<id>/reconnect/accept/   {"name": "Jacques"}
POST /api/peers/<id>/reconnect/refuse/
POST /api/peers/<id>/reconnect/cancel/
```

Verify applies to the locally initiated leg. Accept and Refuse apply to the locally received leg. Cancel clears a locally initiated request. A crossed slot can contain both legs and each action changes only the leg it owns until successful promotion clears the complete slot.

Refusing the received leg of a crossed slot changes it to `sent` and preserves the initiated leg. Cancelling the initiated leg changes it to `received` and preserves the received leg. Clearing the last remaining leg empties the complete slot.

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

The service routes verify and accept operations by the matching credential:

- an initial-pairing credential updates the main initial fields;
- a reconnection credential updates only the reconnection slot.

Both paths use shared verification, retry, held-accept, and crossed-request transition helpers. The implementation does not duplicate the pairing state machine.

One token resolver checks all current and provisional credential columns. Token minting retries if its value already exists in any of those columns. An unknown, stale, or cancelled provisional token receives the same `403 unknown_token` response as any other unknown token.

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

Successful acceptance on each instance atomically:

- promotes `reconnect_token_ours` and `reconnect_token_theirs` to the current credential fields;
- clears every reconnection field;
- sets `state = active`;
- clears `broken_reason` and `revoked_at`;
- updates `paired_local_base_url` to the current canonical local origin;
- preserves `accepted_at` when already set;
- updates `last_contact_at`.

A refusal or cancellation clears only provisional fields. It leaves the main state, current credentials, history, and timestamps unchanged.

### 7.5 Crossed reconnection

If both users reconnect the same relationship concurrently, each incoming request merges into the existing provisional slot. `reconnect_state` becomes `crossed`.

Each side keeps its minted provisional credential, stores the other provisional credential, validates the code, and accepts locally. One row and one provisional attempt exist on each instance.

### 7.6 Retry and idempotency

Request, verify, and accept callbacks tolerate a lost successful HTTP response. A retry uses the same provisional credentials and cannot create a second slot.

An unreachable initial request clears the provisional slot and keeps the main state. The UI reports **Reconnect required** and offers Retry for that peer only.

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

The first option becomes **Current peers**.

The select then shows current peer options, followed by a labelled divider **Revoked peers**, followed by revoked peer options. Initial pending requests remain in their separate request section.

### 9.2 Current peers view

With **Current peers** selected:

- message rows whose peer is revoked are excluded;
- text search matches title and content only inside the remaining current-peer rows;
- the pending-message badge and count exclude pending inbound messages from revoked peers.

### 9.3 Exact revoked-peer view

Selecting one revoked peer displays that peer's complete retained message history. A text query then searches only that selected peer's title and content.

The UI provides no combined **all revoked peers** message view. A text query cannot return revoked-peer messages while **Current peers** or a different peer is selected.

When the user revokes the peer currently selected in the inbox, the filter returns to **Current peers**. The dialog stays open.

### 9.4 REST filtering

`GET /api/peer-messages/` accepts an explicit current-peer scope for the default view and the existing exact `peer_id` for a peer-specific view.

The backend applies peer scope before text matching. It does not fetch revoked candidates and discard them after search.

---

## 10. Status reconciliation after reconnection

### 10.1 Query contract

After successful reconnection, each instance queries statuses only for its local outbound messages that are still pending for that Peer.

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

One failed reconciliation does not change the reconnected Peer to broken. The system starts one query immediately, then retries after 10 seconds, 1 minute, and 5 minutes. It then stops automatically.

The message detail offers **Refresh status** for an outbound pending row. This action queries only that message and produces no toast.

### 10.3 Callback convergence

A normal status callback and reconciliation can race. Both update the row under the existing message-resolution lock and apply the same monotonic rule.

---

## 11. Changes to this instance's `peerBaseUrl`

### 11.1 One managed mutation path

A changed `peerBaseUrl` cannot use the generic synchronized-settings write directly.

The Settings UI calls:

```text
POST /api/peers/local-address/
{
  "base_url": "https://new.example",
  "base_version": 42,
  "confirmed": true,
  "reconnect_active": true
}
```

The owner-only managed mutation validates the canonical origin, updates the setting, applies local Peer transitions, broadcasts the authoritative setting and Peer rows, and then starts optional network work.

`base_version` provides the existing synchronized-settings optimistic check. A stale version rejects the complete mutation before the address or a Peer changes. Reapplying the same canonical address is a no-op and needs no confirmation.

The backend requires `confirmed = true` for valid-to-empty and valid-to-different-valid transitions. It also requires an explicit boolean `reconnect_active` for a valid-to-different-valid transition. Missing confirmation returns `confirmation_required` with no settings, Peer, credential, or network change.

Every generic WebSocket settings patch and `twicc settings set|unset peerBaseUrl` receive `managed_setting` when they attempt to change the value. No alternate write path can bypass confirmation or credential invalidation.

### 11.2 Empty to valid

Setting a valid origin when the current value is empty behaves like first-time configuration.

It enables the public Peer routes. It changes no existing Peer, starts no pairing request, scans no history, and offers no automatic restoration.

### 11.3 Valid to empty

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

Setting a valid origin later follows §11.2. The system remembers no automatic reconnection group.

### 11.4 Valid origin A to valid origin B

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

### 11.5 Optional batch results

The optional requests use bounded concurrency and independent results.

For each selected peer:

- a sent request shows **Reconnection pending**;
- an unreachable request remains **Reconnect required** with Retry;
- one failure does not cancel another success;
- closing the dialog does not roll back the address or any result.

The UI reports sent, failed, and skipped counts. It never includes peers that were broken, revoked, or initial-pending before the change.

Each selected request uses the existing local `Peer` and its reconnection slot because the remote origin did not change locally. Its handshake payload advertises B and carries A as `previous_base_url`. The remote instance therefore creates a new pending `Peer` for B while this instance preserves the existing row and history for that remote origin.

---

## 12. A remote peer changes address

### 12.1 Claim and matching

The pairing request's optional `previous_base_url` is a claim. The receiver strictly canonicalizes it.

The receiver links the new pending row to an old row only when the canonical previous origin matches exactly. It never uses a local name or remote display name.

The new canonical `base_url` always creates a new `Peer` and new history on the receiving instance.

That new row uses the existing initial-pairing fields and pipeline. `previous_base_url` and `replaces_peer` only add replacement context to the pending row; they do not use its reconnection slot.

### 12.2 Before verification

When the claim matches an active or broken old Peer, Manage Peers displays:

> This request claims to replace an existing peer address.

It shows the old and new origins. It prefills the old local name in an editable name field.

The UI does not call the claim verified before successful code verification.

### 12.3 After verification

After successful code verification, the UI displays:

> Verified address replacement request.

It provides one checked option:

> Revoke the old peering after accepting the new one

The help text states:

> The peer has moved to a new address, so the old peering can no longer be used. Revoking it hides its messages from the default inbox while preserving its complete history. If you keep it, TwiCC marks it as unavailable and you can revoke it later.

### 12.4 Acceptance result

Accepting the request activates the new `Peer`.

- With the option checked, the old Peer becomes revoked and its old credentials are cleared.
- With the option cleared, the old Peer becomes `broken / remote_address_changed` and its old credentials are cleared.

The old Peer never remains active after accepting a verified replacement.

Acceptance locks and re-reads both rows under the database write lock. If the old row became revoked after the confirmation opened, it stays revoked regardless of the checkbox. The new row can still be accepted.

If the old Peer was already revoked, the UI states that preserved history exists. It offers no checkbox and leaves that row revoked.

If no exact old origin matches, the request is a normal initial pairing. TwiCC ignores the replacement association and transfers no history.

---

## 13. Owner, CLI, and agent surfaces

### 13.1 Human-only relationship mutations

Revoke, Reconnect, replacement acceptance, and managed `peerBaseUrl` transitions are owner REST and Settings UI operations.

They have no drop-request kind, RPC command, MCP tool, or bundled skill instruction. The generic settings command cannot change `peerBaseUrl`.

### 13.2 Read-only peer list

`twicc peers` excludes revoked rows. It continues to show broken rows and includes their structured reason.

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

`tests/test_peer_handshake.py`, `tests/test_peer_messages.py`, owner API tests, settings-mutation tests, and a new migration test cover:

- canonical origin normalization on every Peer input;
- one canonical remote origin across all states;
- explicit migration failure diagnostics for invalid and duplicate development rows;
- `PROTECT` history retention;
- Revoke transitions, idempotency, token clearing, and pending-only deletion;
- old-token uniform `unknown_token` responses with no side effects;
- incoming-message and outgoing-send lock ordering against Revoke;
- local-origin mismatch repair and startup reconciliation;
- local resolution of stored pending inbound messages without callbacks;
- generic settings writes rejecting changed `peerBaseUrl`.

A test fails when a forbidden row is deleted, a credential survives, a network stub is called after the losing race, or a revoked message changes the default badge count.

### 15.2 Handshake and reconciliation protocol

Focused protocol tests cover:

- reconnect from broken and revoked while preserving the main state until acceptance;
- incoming reconnect against an active row without replacing active credentials early;
- fresh credential promotion and provisional-field clearing;
- refusal, cancellation, retry, held accept, and crossed reconnect;
- idempotent lost-response recovery;
- same-address row reuse and new-address row creation;
- `previous_base_url` canonical matching, unmatched claims, and verified replacement outcomes;
- outbound-pending-only status queries;
- unknown status IDs remaining pending;
- monotonic status application and no retransmission;
- reconciliation failure leaving the Peer active.

A test fails if provisional data reaches the main credential fields early, a second same-address Peer appears, history moves to a new-address Peer, or reconciliation sends message content.

### 15.3 Frontend pure contracts and build

Frontend `node:test` utilities cover:

- Current peers versus exact revoked-peer filtering;
- text-search scope before matching;
- selectable peer grouping and revoked divider placement;
- badge exclusion for revoked-peer pending messages;
- Manage Peers grouping and revoked ordering;
- local-address transition summaries and per-peer batch results.

`cd frontend && npm test` and `cd frontend && npm run build` cover the complete frontend regression and production bundle.

The repository has no Vue component-test harness. Component interaction remains manual rather than introducing a new harness in this feature.

### 15.4 Manual acceptance matrix

| Case | Observable result | Failure signal |
|---|---|---|
| M1 — Revoke active | Row moves to Revoked peers; history remains selectable. | Row disappears or history is lost. |
| M2 — Old token after Revoke | Remote send fails without a local row, toast, or badge change. | Any local message or notification appears. |
| M3 — Pending inbound at Revoke | Message remains readable and locally deliverable or refusable with the revoked warning. | Message disappears or a callback is attempted. |
| M4 — Inbox default | Current peers and text search omit revoked history. | Revoked history appears without selecting that peer. |
| M5 — Revoked peer filter | Exact revoked peer selection shows its complete matching history. | Another peer appears or matching history is missing. |
| M6 — Reconnect | Full code verification is visible; success reactivates the same local row. | Old tokens reactivate it or a second same-address row appears. |
| M7 — Crossed reconnect | Both instances converge through one attempt per side. | Duplicate attempts or rows appear. |
| M8 — Disable local address | Confirmation appears; peers become unavailable; no network work starts. | A relationship remains usable or reconnect starts automatically. |
| M9 — Replace local address, batch enabled | Only peers active at confirmation enter independent pairing requests. | Broken or revoked peers enter the batch, or one failure rolls back another result. |
| M10 — Replace local address, batch disabled | Address changes and peers require individual reconnect. | TwiCC starts a request. |
| M11 — Remote replacement claim | UI labels the claim before code and verified replacement after code. | The unverified claim appears trusted. |
| M12 — Accept remote replacement | New address has new history; old row becomes revoked or broken according to the checkbox. | History merges or the old row stays active. |
| M13 — Manual status refresh | One outbound pending status updates without a toast or retransmission. | Payload is sent or a final status regresses. |

### 15.5 Scope inspection

The implementation diff must not add:

- a second relationship or pairing-request model;
- an agent-facing Revoke or Reconnect mutation;
- a history merge between different canonical remote origins;
- a compatibility repair path for external Peer data;
- an edit path for `Peer.base_url`;
- a message archive field.

---

## 16. Implementation lots

### Lot 1 — Revocation and retained history

Deliver the schema migration, canonical remote identity, `PROTECT`, revoked and broken metadata, live availability gate, Revoke and pending-only deletion APIs, message race behavior, Manage Peers revocation UI, and revoked-aware inbox filtering.

This lot ends with a complete usable Revoke flow. It does not expose Reconnect yet.

### Lot 2 — Full reconnection and status reconciliation

Deliver the provisional slot, shared handshake routing, fresh credential promotion, crossed reconnect, retry behavior, status query protocol, manual Refresh status, and Reconnect UI.

### Lot 3 — Local and remote address changes

Deliver the managed `peerBaseUrl` mutation, local address confirmations and optional active-peer batch, local-origin startup repair, `previous_base_url`, remote replacement UI, and old-peer disposition.

### Lot 4 — CLI, skills, and repository documentation

Deliver read-surface state wording, send errors, managed-setting CLI rejection, bundled skill updates with the required plugin version bump, and current repository guidance.

The lots are cumulative implementation boundaries on one feature branch. A deployment must include every completed migration and its matching runtime code.
