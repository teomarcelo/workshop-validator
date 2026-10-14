# Restored-state reconciliation (synthetic preflight)

The public-safe `resume_reconciliation.reconcile_resume` function is a read-only
gate for a future approved PRIVATE runner. It does not restore or encrypt SQLite,
authenticate a collector, log into Salesforce, or resume an action.

After private checkpoint authentication, independently reobserve the expected
run/org/guide/baseline identity, baseline state, each historical PASS step,
and the preconditions of each currently runnable step. Use trusted frozen
assertions and the existing independent evidence verifier. Reject wrong live
values, reused evidence, unfinished attempts, inconsistent dependencies, or
missing checks. A blocked HTTP 429 step stays blocked; independent NOT_RUN
steps may remain eligible under Option A.

A passing preflight is not permission to skip an instruction or propose a
guide correction. Repeat before-state checks immediately before every action.
For guide removal, use separately authenticated reset, omission, downstream
and negative-control evidence with instructor review. No live Salesforce
validation is claimed by public synthetic tests.

Deployment still requires an approved private runner, encrypted guide/plan
and checkpoint storage, rotated credentials, authenticated observations,
disposable baseline resets, and the two private outputs: quick HTML summary
and pixel-preserving annotated instructor PDF.
