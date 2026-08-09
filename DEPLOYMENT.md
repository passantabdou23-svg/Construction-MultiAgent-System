# Deployment guide

## Recommended demonstration deployment

Run the application on the same workstation as Ollama. This is the cleanest configuration because the model, SQLite database, and dashboard remain local.

### Pre-demonstration checklist

1. Open PowerShell in the repository.
2. Activate the Python 3.12 virtual environment.
3. Confirm `ollama list` contains the configured model.
4. Run `python ingest_documents.py` and confirm 4 checksummed PDFs produce 586 auditable chunks.
5. Run `python index_documents.py` and confirm 526 retrieval-eligible chunks are persistent.
6. Run `python evaluate_rag.py --top-k 5` and confirm Hit@5, Top-1, routing, positive acceptance, and negative rejection targets pass.
7. Run `python evaluate_grounding.py` and confirm supported acceptance and guard rejection both pass.
8. Run `python -m unittest discover -s tests -v`.
9. Run `python check_db.py` and inspect the current audit-record counts.
10. Start the dashboard with `python -m streamlit run app.py`.
11. Test one approved revision, one reviewer rejection, one insufficient-evidence refusal, and one irrelevant note before the audience arrives.
12. Before approval, confirm the run is at `workflow_stage = AWAITING_APPROVAL` and that procurement and schedule tables have no rows for that revision.
13. For the approved revision, confirm the persisted material rows contain only facts explicitly stated in the site note; retrieved standards must never create extra procurement items.
14. Confirm a second decision for the same review is rejected and a changed snapshot cannot be approved.
15. Explain that reviewer names and roles are self-declared audit fields, verified citations are guidance passages, and procurement quotes remain estimates pending verification.

## Local-only launch

```powershell
python -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501
```

Then open `http://localhost:8501`.

## Controlled LAN demonstration

Only expose the application on a trusted network:

```powershell
python -m streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```

Allowing a firewall rule exposes the interface to other devices on that network. Do not use this mode on a public network and do not store sensitive project notes in the demonstration database.

## Why Streamlit Community Cloud is not the default

The current architecture depends on a local Ollama service and a local SQLite file. A hosted Streamlit container cannot automatically reach the Ollama service on a private workstation, and its local filesystem is not an appropriate shared production database.

## Production boundary

Before production use, add authentication, role-based authorization with separation of duties, electronic-signature policy where required, HTTPS, a managed database with backups, a controlled schedule import, verified supplier integrations, licensed standards content, monitoring, and data-retention controls.

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
