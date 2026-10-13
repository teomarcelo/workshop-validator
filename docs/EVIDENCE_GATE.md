# Independent evidence gate (prototype)

`workshop_validator/evidence.py` evaluates **required, manifest-defined** assertions against structured observations from a browser/API worker. It is pure Python and has no access to Salesforce, secrets, browser sessions, screenshots, or external storage. It does **not** claim to prove that a worker's observations are genuine; production requires authenticated collectors, immutable private evidence storage, and independent verification of source authenticity.

```python
from workshop_validator.evidence import AssertionSpec, Observation, StepContext, verify_step

context = StepContext("run-1", "org-1", "S-1", "browser-worker", "verifier")
expected = [AssertionSpec("saved", "DOM_VISIBLE", True)]
observed = [Observation("e-1", "saved", "DOM_VISIBLE", True,
                        "run-1", "org-1", "S-1", "browser-worker")]
assert verify_step(context, expected, observed).status == "PASS"
```

The expected assertions **must originate in a trusted guide manifest** rather than being invented by the executor after it sees the page. Every required assertion must have a uniquely identified, type-correct observation bound to the same run, org, step and executor. The verifier identity must differ from the executor identity. DOM text supports exact equality and nonempty substring containment; visibility and HTTP status support strict equality. An observed mismatch returns `FUNCTIONAL_FAIL`; missing/ambiguous/invalid evidence returns `NEEDS_REVIEW`. Only a fully verified result returns `PASS`. A screenshot, LLM confidence statement, or an executor-supplied PASS flag is not assertion-grade evidence.

Do not put raw observations containing private data in public logs or reports. Returned failure reasons deliberately omit observed values. The current identity checks are **structural, not cryptographic**; a compromised worker can forge IDs and values. Production must authenticate collector identities and evidence storage, bind assertions to the immutable manifest, ensure the verifier cannot silently reuse the executor's conclusion, and enforce dependency prerequisites through `plan_steps` before starting execution. This prototype does not address 429 retry policy or durable state.

Run the unit tests from the repository root with `python3 -m unittest discover -s tests -v`.
