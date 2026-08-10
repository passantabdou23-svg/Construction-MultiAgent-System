# Final acceptance report

**Status:** PASS
**Tested baseline commit:** `b76e39b20639e589ec0392bef596838568707428`
**Generated (UTC):** 2026-08-10T11:55:07+00:00
**Python:** 3.12.0

## Acceptance scope

This report records reproducible technical evidence for the controlled local prototype.
It validates the implemented software contracts; it is not regulatory approval, an
engineering certification, a verified supplier quotation, or a production authorization.

## Release readiness

| Check | Result | Evidence |
|---|---|---|
| python | PASS | Python 3.12.0 |
| dependencies | PASS | 10 exact dependency pins installed |
| release_files | PASS | 14 release files present; app.py compiles |
| ignore_contract | PASS | 8 runtime/secret ignore rules present |
| controlled_sources | PASS | 4 controlled PDFs verified (198 pages) |
| database | PASS | SQLite integrity/foreign keys valid; audit events=18; head=7b9fe01873c8f2298ffd3508744dfc3e6f7eef9b7c215d56baa8c90dd0dc4984 |
| runtime_rag | PASS | 586 auditable chunks; 526 retrieval-eligible vectors |
| ollama | PASS | Ollama reachable; configured model 'llama3.1' installed |

## Automated verification

- Repository tests: **73 passed**, 0 failures, 0 errors.
- Stress tests: **3 passed**, 0 failures, 0 errors.
- Retrieval Hit@5: **100.0%**.
- Retrieval Top-1 accuracy: **100.0%**.
- Mean Reciprocal Rank: **1.000**.
- Document-routing accuracy: **100.0%**.
- Positive-query acceptance: **100.0%**.
- Negative-query rejection: **100.0%**.
- Grounded supported-case acceptance: **100.0%**.
- Grounding-guard rejection: **100.0%**.

These percentages describe the labelled controlled evaluation sets only and are not a
general construction-compliance benchmark.

## Accepted workflow evidence

- Active local accounts: **3**.
- Persisted design revisions: **1**.
- Persisted material requirements: **1**.
- Unverified procurement records: **1**.
- Schedule-impact records: **1**.
- Pending approvals: **0**.
- Audit chain: **valid**, 18 events, head `7b9fe01873c8f2298ffd3508744dfc3e6f7eef9b7c215d56baa8c90dd0dc4984`.
- Database backup: Validated: SHA-256 `98c57970050478110f7b53d43d7b10ff3008689b8f8cce4574b5f8fc3f157025`, 118784 bytes.

The accepted demonstration proves the implemented state transition: a PREPARER creates
a grounded immutable package; a different DESIGN_REVIEWER reauthenticates and decides
it; only approval permits procurement planning and CPM analysis; the resulting records
remain traceable through SQLite and the hash-chained audit trail.

## Controlled knowledge base

- Sources: official England Approved Documents **A, C, K, and 7**.
- Retrieval: 75% MiniLM semantic similarity + 25% BM25-style lexical relevance.
- Embedding model: `sentence-transformers/all-MiniLM-L6-v2`.
- Confidence floor: **0.45**.
- Every accepted technical claim must cite a retrieved chunk and verbatim evidence.
- Unsupported numbers, unavailable chunks, altered quotes, superseded/conflicting
  versions, and evidence-created procurement items are rejected deterministically.

## Security and governance acceptance

- Passwords use uniquely salted `scrypt` derivation.
- Roles and active sessions are checked server-side.
- Repeated failures lock accounts; sessions have idle and absolute expiry.
- Preparation and approval enforce separation of duties.
- Approval requires fresh password verification and is single-use.
- Immutable package snapshots prevent stale-state approval and replay.
- SQLite integrity, foreign keys, backup manifests, and the SHA-256 audit chain passed.

## Deployment and recovery acceptance

- Default deployment is localhost on the same workstation as Ollama.
- LAN exposure requires an HTTPS reverse proxy; plain HTTP must not carry credentials.
- Backups use SQLite's online-backup API and a SHA-256 sidecar manifest.
- Restore validates the source, preserves the previous destination, verifies a temporary
  copy, and replaces atomically.
- The activation-ready CI template is stored as
  `GITHUB_ACTIONS_VALIDATION.template.yml` until a Workflow-authorized token is available.

## Explicit limitations

- Controlled single-workstation prototype; not a managed multi-user production service.
- No MFA, enterprise SSO, account recovery, or legally binding electronic signature.
- Audit chain is tamper-evident but not digitally signed or externally anchored.
- Procurement outputs are unverified planning estimates.
- CPM uses a five-task demonstration network, not live Primavera P6/MS Project data.
- Approved Documents apply to England and are not Egyptian compliance authority.
- A licensed engineer and applicable authorities remain responsible for decisions.
