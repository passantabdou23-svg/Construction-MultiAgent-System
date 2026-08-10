# Deployment guide

## Recommended demonstration deployment

Run the application on the same workstation as Ollama. This is the cleanest configuration because the model, SQLite database, and dashboard remain local.

### Pre-demonstration checklist

1. Open PowerShell in the repository.
2. Activate the Python 3.12 virtual environment.
3. Create separate `PREPARER`, `DESIGN_REVIEWER`, and `ADMIN` accounts with `python manage_users.py create`; never share one account between roles.
4. Confirm `ollama list` contains the configured model.
5. Run `python ingest_documents.py` and confirm 4 checksummed PDFs produce 586 auditable chunks.
6. Run `python index_documents.py` and confirm 526 retrieval-eligible chunks are persistent.
7. Run `python evaluate_rag.py --top-k 5` and confirm Hit@5, Top-1, routing, positive acceptance, and negative rejection targets pass.
8. Run `python evaluate_grounding.py` and confirm supported acceptance and guard rejection both pass.
9. Run `python -m unittest discover -s tests -v`.
10. Run `python release_check.py --database construction_mas.db --runtime --with-ollama`
    and confirm every readiness check passes.
11. Create and validate a pre-demonstration backup using
    `python manage_backup.py create --output-directory backups --label pre-demo` and the
    printed validation command.
12. Start the dashboard with `python -m streamlit run app.py`.
13. Test one approved revision, one reviewer rejection, one insufficient-evidence refusal, and one irrelevant note before the audience arrives.
14. Confirm a `PREPARER` cannot decide a package and a reviewer cannot create one.
15. Confirm approval requires the reviewer's current password and failed credentials do not change the pending package.
16. Before approval, confirm the run is at `workflow_stage = AWAITING_APPROVAL` and that procurement and schedule tables have no rows for that revision.
17. For the approved revision, confirm the persisted material rows contain only facts explicitly stated in the site note; retrieved standards must never create extra procurement items.
18. Confirm a second decision for the same review is rejected and a changed snapshot cannot be approved.
19. Explain that local authentication is not MFA or a legal electronic signature, verified citations are guidance passages, and procurement quotes remain estimates pending verification.

## Backup and recovery

Create a transactionally consistent backup while the application is running:

```powershell
python manage_backup.py create --output-directory backups --label pre-release
python manage_backup.py validate --backup "backups\<backup-file>.db"
```

Each backup has a sidecar manifest containing its SHA-256, size, audit-chain head, event
count, and manifest schema. Keep both files together and store release copies outside the
Git checkout with access controls appropriate to the project records.

Restoration is deliberately explicit. Stop Streamlit and other database users, validate
the selected backup, then run:

```powershell
python manage_backup.py restore --backup "backups\<backup-file>.db" `
  --recovery-directory "backups\recovery" --confirm-replace
python release_check.py --database construction_mas.db --runtime --with-ollama
```

If the destination exists, the command first creates a validated pre-restore backup. The
replacement is verified in a temporary file and moved atomically; an open-file failure
leaves the destination unchanged.

## Continuous validation

`GITHUB_ACTIONS_VALIDATION.template.yml` is activation-ready and uses Python 3.12, exact
dependency pins, read-only repository permissions, and immutable GitHub Action revisions.
Copy it to `.github/workflows/validation.yml` with a GitHub credential that has Workflow
permission. Until then, run its commands locally. Ollama and the generated persistent
ChromaDB index remain local release checks because CI intentionally does not contain
runtime models or generated project data.

## Local-only launch

```powershell
python -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501
```

Then open `http://localhost:8501`.

## LAN demonstrations require TLS

Do not expose the password form over plain HTTP on a LAN. The following bind command is
acceptable only behind a correctly configured HTTPS reverse proxy on a trusted network:

```powershell
python -m streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```

Allowing a firewall rule exposes the interface to other devices. Without HTTPS, credentials
and project content can be intercepted. Prefer the localhost launch for the academic demo.

## Why Streamlit Community Cloud is not the default

The current architecture depends on a local Ollama service and a local SQLite file. A hosted Streamlit container cannot automatically reach the Ollama service on a private workstation, and its local filesystem is not an appropriate shared production database.

## Production boundary

The local phase now provides scrypt password storage, account lockout, server-side roles,
separation of duties, approval-time reauthentication, session expiry, and a SHA-256 audit
chain. Before production use, replace or integrate this with an enterprise identity provider,
MFA, recovery and revocation procedures, HTTPS, centrally managed authorization, an
electronic-signature policy where required, an externally anchored audit service, a managed
database with backups, a controlled schedule import, verified supplier integrations,
licensed standards content, monitoring, and data-retention controls.

The current application must therefore be presented as a validated local planning prototype, not as an autonomous construction approval system.

The embedded `PersistentClient` deployment is appropriate for this single-workstation
demonstration. A multi-user or multi-process deployment should run Chroma as a separately
managed server with authentication, backups, monitoring, controlled document-version
migrations, and a revalidated retrieval threshold. Never copy a live index between embedding
models: the application rejects a stored collection whose model contract differs from the
configured one.

## Controlled RAG release checks

- The corpus is limited to official England Approved Documents A, C, K and 7.
- Every startup corpus refresh must verify the manifest checksum, PDF page count, searchable-page count, edition, status, source-check date, jurisdiction, and official URL.
- Rebuild the index whenever a PDF or manifest record changes; do not mix vectors produced by different embedding-model contracts.
- Re-run the labelled evaluation after changing documents, chunking, routing keywords, semantic/lexical weights, or the confidence floor.
- Re-run the grounding evaluation after changing prompts, claim schemas, quote-alignment rules, site-fact isolation, citation rules, or document-version policy.
- Treat unresolved conflicts, superseded editions, out-of-jurisdiction questions, and low-confidence results as human-review conditions rather than compliance answers.
