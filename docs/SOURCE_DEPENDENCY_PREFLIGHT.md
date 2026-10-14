# Source-bound dependency graph preflight

The approved private runner must reject an executable plan **before login** if a
reviewed action depends on an unknown or unreviewed source action, repeats a
dependency, contains a cycle (including self-dependency), or supplies malformed
dependency identifiers. Valid cross-page dependencies and independent branches
are supported.

`workshop_validator.private_preflight.require_executable_source_review` checks
exact source-action/page coverage and then reuses the production
`dependencies.plan_steps` graph validator with all steps initially NOT_RUN.
This does not duplicate the Option A scheduler. `run_private` calls the
preflight before opening a browser or reading secret environment variables.

The synthetic tests in `tests/test_source_dependency_preflight.py` cover
cross-page links, independent branches, unknown source IDs, cycles, duplicate
edges, malformed dependencies and wrong source pages. Public CI checks only
the harness; it cannot prove the human-authored dependency relationships are
semantically correct, that live browser evidence is authentic, or that any
Salesforce step passed.

The private operator must still review the complete source-to-step mapping,
including all lettered substeps and downstream assertions, before a live trial.
Omission and negative-control experiments require an authorized, resettable
disposable baseline. A deliberately broken target that still passes the
independent oracle blocks guide-correction recommendations.

No private PDF, org URL, screenshot, source-specific manifest or credential
belongs in public GitHub or Actions. The approved private workflow must emit
both the quick HTML summary and pixel-preserving annotated review PDF.
