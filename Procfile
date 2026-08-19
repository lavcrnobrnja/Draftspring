web: cd backend && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
worker: cd backend && python -m app.worker_main
backup: cd backend && python -m scripts.backup_db
