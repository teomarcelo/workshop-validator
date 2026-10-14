# Encrypted private cross-job checkpoint handoff (synthetic prototype)

A future approved private runner may export its SQLite checkpoint as an authenticated, context-bound encrypted envelope. This is a local design, not deployed cloud storage, automatic resume, or proof of a Salesforce action.

The operator must provision a securely generated private key and protected storage. The envelope must bind run ID, org ID and the private guide digest, reject tampering, reject any checkpoint containing a RUNNING attempt, preserve 429 retry tickets, and refuse destination overwrite. Never place actual envelopes, source documents, screenshots, or org identifiers in the public repository or public CI artifacts.

After restoration, the private operator must re-establish the disposable-org baseline and reconcile possible side effects before resuming. Keep the quick HTML summary and pixel-preserving annotated instructor PDF as mandatory outputs. Public synthetic tests cannot establish live Salesforce correctness.
