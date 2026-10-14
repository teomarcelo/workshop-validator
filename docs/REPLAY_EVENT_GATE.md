# Controlled replay event-order gate (synthetic structural prototype)

`workshop_validator.replay_events.assess_recorded_replay` adds an ordered,
run-bound event ledger to the six independent phase outcomes already required
by `assess_replay`. It **does not execute** a Salesforce step or authenticate
any event. Only an approved private collector may supply real evidence.

The executed trial requires independently observed reset/prerequisites and
before-state, the **literal** authored action, after-state, and downstream
checks. The omission trial requires a separate reset, prerequisites and
before-state, an explicit single-action omission, and downstream checks; an
`literal_action` event in this trial invalidates the ledger. The negative
control requires a third reset, prerequisites, a reversible fault, the target
oracle's post-fault result, restoration, and a separate independent **PASS**
of the same target oracle after restoration.

Every event is bound to a run, reset, step, monotonically increasing sequence,
unique event ID, unique evidence IDs and a specific actor. Mutation events
must name the exact authored action and executor; observations must be
attributed to a distinct verifier. Observed event evidence IDs must exactly
match their corresponding independently verified replay phase; restoration
must match a separately supplied target-oracle verification. Missing events,
wrong order, reused evidence, a skipped action actually logged as executed,
wrong action/actor, or unverified restoration all fail closed. A deliberately
wrong value that still passes the target oracle remains
`TEST_ORACLE_INSENSITIVE` and cannot justify guide correction.

**Security boundary:** This ledger is only a *structural consistency check*.
A dishonest or compromised executor can fabricate events or omit actions from
its log. `reset_verified` booleans, actor names, event IDs and evidence IDs
are not authenticated proof. Before any real instructor-review candidate, an
approved private runner must authenticate the collector, bind reset evidence
to an authorized disposable-org baseline, store append-only signed or
integrity-protected records, verify the exact action/fault and restore against
the real UI, and keep all artifacts in encrypted private storage. Never send
private event logs, guide content, org URLs, screenshots or credentials to
public GitHub or its CI. This module does not authorize automatic skipping,
resetting, fault injection, or guide edits.

Synthetic unit tests exercise missing/forged/out-of-order events, negative
controls, independent actor binding, omission boundaries, and restoration.
Existing Chromium replay tests separately exercise real local HTML form
behavior. **No live Salesforce execution or real baseline reset is claimed.**
The approved private pilot must still produce both the quick HTML summary and
pixel-preserving annotated instructor PDF, linked to run-bound evidence.
