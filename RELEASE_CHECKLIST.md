# Release checklist

Use this checklist for a tagged demonstration build or before handing the project to a
new workstation. Record the commit hash and retain the command output with the release.

## 1. Clean source and environment

- Confirm `git status --short --branch` is clean and points to the intended commit.
- Use Python 3.12 in a fresh virtual environment.
- Install only the exact versions in `requirements.txt`.
- Run `python -m pip check`; resolve every conflict in the release virtual environment.
- Copy `.env.example` to `.env`; never commit `.env`, passwords, databases, or backups.
- Confirm `ollama list` contains the configured model.

## 2. Controlled knowledge base

```powershell
python ingest_documents.py
python index_documents.py
python evaluate_rag.py --top-k 5 --threshold 0.45
python evaluate_grounding.py
```

Confirm four checksummed source PDFs, 586 auditable chunks, 526 retrieval-eligible
vectors, and all labelled retrieval and grounding targets passing. Rebuild and reevaluate
after any source, manifest, chunking, routing, weight, prompt, or threshold change.

## 3. Release validation

```powershell
python release_check.py --database construction_mas.db --runtime --with-ollama
python -m unittest discover -s tests -v
python -m unittest test_stress_cases -v
python check_db.py
```

Every check must pass. After `GITHUB_ACTIONS_VALIDATION.template.yml` is copied to
`.github/workflows/validation.yml` with a Workflow-authorized GitHub credential, GitHub
Actions repeats the deterministic checks on Python 3.12. The live Ollama and persisted-
index checks remain local because CI has no trusted model service or generated runtime
index.

## 4. Backup and recovery

Stop application writes before a restore. A live backup may be created safely:

```powershell
python manage_backup.py create --output-directory backups --label pre-release
python manage_backup.py validate --backup "backups\<backup-file>.db"
```

Test restoration only into a disposable database before release:

```powershell
python manage_backup.py --database "backups\restore-test.db" restore `
  --backup "backups\<backup-file>.db" `
  --recovery-directory "backups\recovery" --confirm-replace
python release_check.py --database "backups\restore-test.db"
```

For an actual incident, stop Streamlit and every process using SQLite, validate the
selected backup, restore with `--confirm-replace`, run `release_check.py`, then restart.
The restore command automatically preserves the previous destination in the recovery
directory when one exists.

## 5. Role and workflow smoke test

- `PREPARER` can create a grounded review package but cannot decide it.
- A different `DESIGN_REVIEWER` can inspect evidence and approve or reject after
  reauthentication.
- Invalid credentials do not mutate the pending package.
- Procurement and CPM remain empty before approval and run once after approval.
- A second decision, changed snapshot, unsupported claim, or irrelevant note is refused.
- `ADMIN` can inspect users and the audit trail but cannot bypass approval duties.

## 6. Deployment and rollback

- Launch on localhost: `python -m streamlit run app.py --server.address 127.0.0.1`.
- Do not expose credentials over plain HTTP. LAN access requires an HTTPS reverse proxy.
- Verify the dashboard shows the expected model, database, corpus, and confidence floor.
- Confirm the audit chain is valid before and after the smoke test.
- Keep the validated database backup and its manifest outside the Git checkout.
- Roll back application code to the last accepted commit and restore only a compatible,
  validated database backup. Rebuild ChromaDB after source or embedding-contract changes.
