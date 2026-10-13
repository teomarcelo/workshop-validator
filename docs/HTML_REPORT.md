# Reviewable HTML report prototype

`workshop_validator/report.py` renders a self-contained HTML report for private storage. It shows each step's page, status, evidence IDs and finding. All dynamic text is HTML-escaped. The included source text and results may be confidential; never upload a real-org report, trace or screenshot as a public GitHub Actions artifact or repository file.

The report stays `INCOMPLETE` unless a trusted caller supplies independently reconciled inventory coverage, at least one required step exists, all required steps are PASS, and every PASS has evidence IDs. These structural checks are **not** a replacement for signed provenance or the independent verifier.

Synthetic usage:

    from workshop_validator.report import ReportStep, render_report
    html = render_report('demo', [
        ReportStep('P001N001', 1, 'Open a synthetic page', 'PASS', ('evidence-1',)),
        ReportStep('P001N002', 1, 'Generate a synthetic response', 'ENV_BLOCKED')
    ], inventory_verified=False)

The output must be saved to an approved private store and offered to instructors through appropriately authorized access. For temporary files, `write_private_report(path, html)` writes with mode `0600` and refuses to overwrite.

**Remaining:** Connect real step records to report generation, approved private evidence links, per-step screenshot/DOM integrity, calibrated confidence, instructor review UI, and reviewed annotated PDF.
