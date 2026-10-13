# Conservative PDF step inventory prototype

This module uses MIT-licensed `pdfplumber` to extract candidate numbered/lettered instructions, source page numbers, layout coordinates, task/exercise headings, and review warnings. It **does not** execute Salesforce steps, modify the original PDF, infer missing text, or assert that every guide instruction has been captured.

Install `pdfplumber==0.11.9`; public CI tests also use synthetic PDF fixtures created with `reportlab==4.4.9`. Only *synthetic* fixtures run in the public repository.

Private operator usage (not in GitHub Actions public CI):

    python -m workshop_validator.guide_inventory /private/guide.pdf --output /private/manifest.json

The JSON result includes source-specific text; it must be stored privately and is created with owner-only file permissions. A `verified_complete: false` field is unconditional until a separate, evidence-backed, page-by-page visual reconciliation pass is implemented.

Warnings cover numbering anomalies, text extraction damage, and steps referring to field/value tables. Steps crossing pages and nested lettered steps are tracked conservatively. Screenshots, tables, non-numbered instructions, and source omissions remain unverified. Human review of every rendered page is required before using this inventory for automated guide validation.

Never commit original exercise guides, PDF-derived manifests, screenshots, traces, source text, or org details to this public repository or its Actions artifacts.
