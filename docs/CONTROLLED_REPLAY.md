# Controlled omission replay: public-safe provenance gate

`workshop_validator.replay_provenance.assess_replay` checks **six private, independently verified phase records** before it can return an instructor-review-only `REDUNDANCY_CANDIDATE`:

1. **Executed trial:** verified reset and prerequisites; observe target before; execute the literal action; observe target after; verify downstream behavior.
2. **Omission trial:** independently verified clean reset of the **same** disposable baseline; observe target before; omit exactly that action; verify downstream behavior.
3. **Negative-control trial:** independently verified clean reset; safely inject a reversible targeted defect; verify that the **same target oracle** returns `FUNCTIONAL_FAIL`. Restore and verify the baseline outside this gate.

Each record binds phase, guide SHA-256, step, org, baseline, reset, run, oracle identity, and independent `Verification` evidence IDs. The three trials require distinct run/reset IDs; the target and downstream oracles must remain stable across trials and distinct from each other. Duplicate evidence, missing phases, mismatched identities, inconclusive downstream behavior, and a broken state that still passes all block guide-removal suggestions.

**Scope limitation:** This is a deterministic **structural gate**, not a browser replay engine or authenticated evidence chain. A caller's `reset_verified` and `prerequisites_verified` booleans are assertions, **not proof**; the approved private runner must separately bind signed or authenticated collector records, exact baseline/reset evidence, the literal action/omission/fault event log, and a verified restoration. Never construct these samples from public CI results or executor claims alone.

Only one safely reversible action/substep should be omitted per trial. Preserve all dependent prerequisites, do not execute dependent steps past 429, and never run faults on shared/production orgs. An approved private runner, protected guide/manifest storage, operator-rotated credentials, a reviewed full-guide plan, and reset guarantees are required for a live trial. The quick private HTML summary and pixel-preserving annotated PDF remain required; **this module does not yet connect replay findings to those outputs**. No paid services are required for these synthetic tests.

## Real Chromium synthetic replay proof (public CI only)

The `test_replay_browser.py` test uses three fresh local HTML pages and
authored Playwright actions: execute a manual edit, omit only that edit, and
deliberately write an incorrect value. It verifies both the generated field
and the downstream Save result. An insensitive visibility-only target oracle
incorrectly passes the wrong value, so the provenance gate must return
`TEST_ORACLE_INSENSITIVE` and forbid a removal recommendation. A wrong
literal edit must fail the target and downstream assertions.

This is a **synthetic browser proof**, not an authenticated Salesforce run.
The test fixture's reset/prerequisite attestations are not signed evidence.
Private live replay still requires an approved resettable disposable org,
authenticated action/omission/fault provenance, secure guide storage, rotated
operator-managed credentials, and private HTML/PDF outputs.
