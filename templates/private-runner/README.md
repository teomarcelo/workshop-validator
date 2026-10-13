# Private GitHub Actions validation runner — operator setup template

This folder is **public-safe SOURCE ONLY**. It contains a workflow template which is **not deployed** and must never be activated in this public repository.

**Required operator-controlled configuration before any real Salesforce test:**

1. Confirm that an approved private GitHub repository or company-approved equivalent may process workshop PDFs, agent configuration and Salesforce trial-org screenshots. Do not copy real guide files into this public code repository. In GitHub Free, private Actions minutes are shared across private repos and may incur charges beyond the free quota; set spending limit/budget to $0 if available.
2. Create an approved **private** repo manually; the current GitHub connector cannot create it. Copy `workflow.yml` into that repo as `.github/workflows/validate.yml`. Do not turn on scheduled runs yet.
3. Configure GitHub Actions environment named `sandbox-trial-org` with any required reviewers, restricted access, and rotated Salesforce org secrets `SF_USERNAME` and `SF_PASSWORD`. Do not reuse a password previously pasted into a chat.
4. Arrange **company-approved private HTTPS storage** providing URLs that return the specific PDF, its complete source-review attestation and the private authored executable plan. These are NOT stored in this public repo. Enter URL strings only as private repo/environment **secrets** `PRIVATE_GUIDE_URL`, `PRIVATE_PLAN_URL`, and optional `PRIVATE_REVIEW_URL`; configure `PRIVATE_CONTENT_BEARER_TOKEN` if required. All URLs must use the single host configured in environment variable `PRIVATE_STORAGE_HOST`. Redirects and non-HTTPS downloads are refused.
5. Set private Actions variable `VALIDATOR_SHA` to a **reviewed full 40-character commit SHA** of this public source repo, not a moving branch. This prevents arbitrary public PR changes from executing with private credentials.
6. Leave `PRIVATE_ARTIFACT_UPLOAD_APPROVED` unset until security approves retention/access for private screenshots, PDF and HTML reports. Once approved, set `true` to retain artifacts for **7 days in the PRIVATE repository only**. They must never be published as public artifacts.
7. Use **Run workflow** manually from Actions. Inspect job statuses and private artifacts. An exit code 2 means incomplete/blocked, not success. Do not enable a daily scheduled job until at least one full manual pilot run and an interrupted/restarted run have passed operational QA.

The private plan must be pinned to the uploaded PDF SHA-256, contain the **complete reconciled set of action IDs** for a green report, and specify authored accessible selectors and assertions. The source review must explicitly inspect every page, candidate instruction and visual context. No claim of complete automated arbitrary Salesforce PDF interpretation is made.

**Important limitations:** The current CLI supports direct Salesforce username/password login only, does not bypass MFA or SSO, does not automatically produce UI repairs from LLMs, and does not yet resume a job from private cross-run state. The retry queue library exists but the end-to-end private worker still requires integration and a durable private state handoff. This workflow is intended for the **approved first pilot**, not as a production-ready continuous agent platform.

No new paid cloud resources, GitHub private repository, secrets or files were created automatically by these templates.
