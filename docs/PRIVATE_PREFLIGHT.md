# Private source preflight

The private runner now refuses to authenticate or execute browser actions unless a complete human page review is provided. This is a fail-closed gate for the separate, operator-approved private execution environment; it is not deployed in public CI.

Before starting the browser, the runner checks the exact guide and inventory digests, reviewer attestations for all pages and candidate actions, the reviewed action ID set, each source page anchor (including manually added actions), and the presence of an authored UI action and independent assertion for every reviewed action. Missing, extra, duplicate, page-mismatched, unresolved or unexecutable steps stop the run. No step is marked verified by this preflight.

A reviewed manifest is still a human attestation, not objective proof of completeness. The independent browser evidence gate remains mandatory for PASS. The template workflow is not deployed, and live org validation requires an approved private runner, private guide access and rotated secrets. No confidential artifacts belong in the public repository.
