# Deployment guide

## Recommended demonstration deployment

Run the application on the same workstation as Ollama. This is the cleanest configuration because the model, SQLite database, and dashboard remain local.

### Pre-demonstration checklist

1. Open PowerShell in the repository.
2. Activate the Python 3.12 virtual environment.
3. Confirm `ollama list` contains the configured model.
4. Run `python ingest_documents.py` to verify the controlled PDF and regenerate chunks.
5. Run `python index_documents.py` and confirm 128 retrieval-eligible chunks are persistent.
6. Run `python evaluate_rag.py` and confirm all configured retrieval targets pass.
7. Run `python -m unittest discover -s tests -v`.
8. Run `python check_db.py` and inspect the current audit-record counts.
9. Start the dashboard with `python -m streamlit run app.py`.
10. Test one valid revision and one irrelevant note before the audience arrives.
11. Explain that citations are guidance passages and quotes are estimates pending human verification.

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

Before production use, add authentication, role-based authorization, HTTPS, a managed database with backups, a controlled schedule import, verified supplier integrations, licensed standards content, human approvals, monitoring, and data-retention controls.

The current application must therefore be presented as a validated local planning prototype, not as an autonomous construction approval system.

The embedded `PersistentClient` deployment is appropriate for this single-workstation
demonstration. A multi-user or multi-process deployment should run Chroma as a separately
managed server with authentication, backups, monitoring, controlled document-version
migrations, and a revalidated retrieval threshold. Never copy a live index between embedding
models: the application rejects a stored collection whose model contract differs from the
configured one.
