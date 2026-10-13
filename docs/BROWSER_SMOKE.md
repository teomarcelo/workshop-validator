# Synthetic Playwright browser worker

The typed `UIAction` / `UIAssertion` adapter executes pre-authored click and fill actions using accessible roles and exact names, collects DOM visibility/text observations and hands them to the separate verifier in `workshop_validator.evidence`. It never directly marks a step PASS. UI failures do not reveal page contents or secrets through our own error messages.

The public browser smoke test creates an isolated HTML mock, performs a fill-and-click operation, and checks both positive and negative assertions. One test also generates a **synthetic** PDF, discovers its candidate steps and checks a manually pre-authored action mapping. That is a pipeline smoke check, **not automatic instruction-to-Playwright planning**. The mock has no Salesforce connection, credentials, org state, or real exercise guide.

Run locally with Chromium and Python Playwright available:

    RUN_BROWSER_SMOKE=1 CHROMIUM_PATH=/usr/bin/chromium python -m unittest discover -s tests -p test_browser_worker.py -v

For GitHub Actions the public browser-smoke job installs Chromium in an ephemeral standard GitHub-hosted runner; its inputs and test output are synthetic only.

**Not yet built:** automatic guide action planning, real-org authentication, screenshots/trace collection in approved private storage, immutable evidence provenance, durable orchestration/retries, and cloud execution of real workshop steps. Run these in a separately authorized private workflow only after privacy/secret gates have been completed.
