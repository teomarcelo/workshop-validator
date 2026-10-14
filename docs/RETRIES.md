# Bounded 429 retry queue (SQLite prototype)

`workshop_validator.retries.RetryQueue` persists retry tickets for **environment-blocked** steps only. After a trusted browser response is classified as HTTP 429 and `CheckpointStore.block_step(..., 'ENV_BLOCKED')` is called, `record_429` records the failed attempt, respects numeric/HTTP-date `Retry-After`, computes capped exponential backoff with bounded optional jitter, and refuses to exceed a maximum attempt budget. Exhaustion leaves the step ENV_BLOCKED and the overall run INCOMPLETE.

`wake_due(now=...)` atomically changes only due, currently ENV_BLOCKED steps to NOT_RUN and increments checkpoint version so that stale workers cannot finish previous attempts. The normal dependency/evidence gate must recheck prerequisites, perform the actual UI action and verify independent assertions; this queue cannot grant PASS or bypass provider limits. Independent branches remain runnable under the approved Option A policy.

The SQLite database is private data and is **not automatically durable across GitHub-hosted runners**. A cloud implementation must use protected external persistence or safe checkpoint export/import, authenticate workers, establish and recover leases, and detect missing retry tickets after crashes. It must not schedule retries more frequently than provider limits allow.

No live Salesforce request, retry, gateway bypass, or production cloud worker has been performed by this prototype.

## Mutation-safe retry boundary (synthetic)

A 429 can be observed after a browser click or fill has already changed state.
The pipeline now records such a step as ENV_BLOCKED with a durable retry ticket
**without a due time**. It will not replay authored UI actions automatically,
even after Retry-After expires. Independent branches with verified prerequisites
continue under Option A. A separate approved private operator/runner must
reconcile the actual side effects, reset when safe, reobserve prerequisites and
explicitly authorize any new attempt. This prevents duplicate mutations from
being mistaken for harmless retries.

The existing bounded same-job timer remains available for **actionless** steps
that a trusted private executor has independently established are read-only.
The public test fixture exercises that path synthetically; an empty action
list is not, by itself, proof that an arbitrary custom executor has no side
effects. Do not enable timed replay for hidden mutations or after an uncertain
Salesforce response. No live retry or authenticated baseline reset is claimed.
