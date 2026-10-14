# Read-only Salesforce org baseline checks (prototype)

This module provides a **public-safe, synthetic-tested API** for an eventual private runner. It is **not yet called by** `private_runner.py` or the undeployed private Actions workflow template. No live Salesforce validation has occurred.

A private operator supplies a baseline JSON file **outside this public repository**. The file is pinned to the exact guide SHA-256 and the expected post-login Salesforce hostname. Each check uses a unique accessible role/name selector and requires a unique, visible match. At least one `ORG_IDENTITY` check must compare exact visible DOM text; at least one `PREREQUISITE` check must verify a required UI condition. No click, fill, navigation, or mutation occurs in this module.

Example with **synthetic values only** (never commit actual org hostnames, guide hashes, or workshop-specific assertions):

```json
{
  "schema_version": 1,
  "guide_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "org_hostname": "synthetic.my.salesforce.com",
  "checks": [
    {
      "check_id": "identity",
      "category": "ORG_IDENTITY",
      "kind": "DOM_TEXT",
      "role": "heading",
      "accessible_name": "Org label",
      "expected": "Synthetic Org"
    },
    {
      "check_id": "builder",
      "category": "PREREQUISITE",
      "kind": "DOM_VISIBLE",
      "role": "button",
      "accessible_name": "Builder",
      "expected": true
    }
  ]
}
```

Private integration sequence: complete guide review and executable plan preflight; load the baseline against the guide digest; authenticate; call `require_org_baseline(page, baseline)`; **only then** start authored browser actions. On any mismatch, abort and leave every workshop step unverified. Do not log the expected or observed org identity text. In addition to UI checks, a real pilot must establish whether a reset/seed mechanism can reproduce the starting state. These UI checks alone do **not** prove complete org state, permission coverage, authentic evidence provenance, or cloud deployment.

Validation in the scheduled milestone: 11 synthetic unit tests passed locally and source/test Git blob hashes matched. Full-repository CI must be checked on the committed revision before claiming repository-wide verification.
