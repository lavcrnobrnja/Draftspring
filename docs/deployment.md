# Example deployment notes

This is intentionally generic. The original production deployment was removed from the public repo because it was specific to our server.

## Processes

Run two long-lived processes:

```bash
# Web/API
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8223

# Worker
cd backend
python -m app.worker_main
```

Use systemd, Docker, Fly.io, Render, Railway, or whatever boring process manager you trust.

## Reverse proxy

Point your domain to the FastAPI process. Example Nginx shape:

```nginx
server {
    server_name draftspring.example.com;

    location / {
        proxy_pass http://127.0.0.1:8223;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## Database/storage

SQLite works for a small internal tool if the DB lives on durable disk and is backed up. If you turn this into SaaS, review DB concurrency/durability and consider migrating the persistence layer.

Generated media can use local disk or S3-compatible storage.
