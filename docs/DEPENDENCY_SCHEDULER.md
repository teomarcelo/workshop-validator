# Dependency-aware Option A gate (prototype)

`workshop_validator/dependencies.py` is a **pure, standard-library planning function**, not a Salesforce runner or a durable cloud service. It returns which `NOT_RUN` steps are eligible to start, which are waiting for prerequisites, and which are blocked by an upstream failure. It does not execute, retry, or mark any step `PASS`.

```python
from workshop_validator.dependencies import Step, plan_steps

steps = [Step("setup"), Step("ai_response", ("setup",)),
         Step("follow_up", ("ai_response",)), Step("independent")]
result = plan_steps(steps, {"setup": "PASS", "ai_response": "ENV_BLOCKED"})
assert result.runnable == ("independent",)
assert result.blocked_by == {"follow_up": ("ai_response",)}
assert not result.complete
```

`PASS` is accepted **only as an input from a separate independent evidence verifier**; this module cannot prove assertions or generate evidence. It rejects a dependent `PASS` if any prerequisite is not `PASS`. A step with `ENV_BLOCKED`, `AUTH_BLOCKED`, `UI_MISMATCH`, `FUNCTIONAL_FAIL`, or `NEEDS_REVIEW` is not automatically retried; a separate durable recovery queue must own cooldowns, HTTP 429 `Retry-After`, bounded attempts, and revalidation. `RUNNING` and `RETRY_WAIT` are non-runnable until an external worker reconciles them. No gateway bypass is implemented.

The `complete` flag means all *required* manifest steps are marked `PASS`; the caller must additionally establish that the PDF inventory is complete, evidence is independently verified, and org/run provenance is correct before reporting an end-to-end green result. Empty manifests are never complete. The planner's inputs are untrusted unless backed by durable run records.

Run the unit tests from the repository root with:

```sh
python3 -m unittest discover -s tests -v
```

Next: add a separately tested evidence gate and durable checkpoint/retry state machine; do not wire an unverified `PASS` directly from browser execution into this planner.
