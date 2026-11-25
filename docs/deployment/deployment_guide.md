# RepoPilot Fix — Production Deployment Guide & Operations Manual

**Version:** 1.0.0 (Release Deployment)  
**Target:** Production / Staging / Local Docker Compose  
**Date:** November 2026  

---

## 1. Architectural Overview & Trust Boundaries

RepoPilot Fix is structured into four core isolated subsystems:
1. **Frontend Web Client (`web`):** Next.js 15 UI service running unprivileged (non-root `nextjs:nodejs` UID 1001), serving the dashboard and interacting with the backend API over HTTP.
2. **Backend API Service (`api`):** FastAPI application handling authentication, repository metadata, code intelligence indices, and chat queries. Does **not** perform execution of untrusted code or hold Docker socket access.
3. **Private Verification Worker (`worker`):** Isolated background worker process consuming approved verification runs from PostgreSQL via database leases and atomic fencing tokens. Executed in a dedicated private network boundary (`backend-net`).
4. **PostgreSQL Database (`db`):** PostgreSQL 16 database storing repository metadata, user sessions, chat history, verification runs, and audit logs with persistent volume storage (`postgres_data`).

### Security Perimeter & Docker Isolation
- **No Public Docker Socket:** The Docker daemon socket is strictly protected and **never mounted** to the public API (`api`) or frontend (`web`) containers.
- **Worker Isolation Boundary:** Verification execution occurs behind the private worker boundary. Real Docker sandbox execution requires `ALLOW_DOCKER_VERIFICATION_EXECUTION=true` in the worker environment and strictly enforces:
  - `--network none` (complete air-gapping).
  - `--read-only` root filesystem.
  - `--tmpfs /tmp:rw,noexec,nosuid,size=64m`.
  - Non-root user execution (`1000:1000`).
  - Strict resource quotas (`--memory 512m`, `--cpus 1.0`, `--pids-limit 100`).
  - Scoped cleanup strictly tagged with `repopilot.managed=true`.

---

## 2. Reproducible Local & Production Deployment Instructions

### Prerequisites
- Docker Engine 24+ and Docker Compose v2+
- Genuinely free local resources (minimum 2 CPU cores, 4 GB RAM, 10 GB disk)
- No paid cloud services or billing required

### Step-by-Step Deployment

1. **Clone and Configure Environment:**
   ```bash
   git clone https://github.com/Mehndi-Hassan28/repopilot-fix.git
   cd repopilot-fix
   cp .env.example .env
   ```

2. **Configure `.env` Security Settings:**
   Ensure production values are set:
   ```ini
   POSTGRES_DB=repopilot
   POSTGRES_USER=repopilot
   POSTGRES_PASSWORD=<generate-strong-random-password>
   DATABASE_URL=postgresql+psycopg://repopilot:<password>@db:5432/repopilot
   FRONTEND_URL=http://localhost:3000
   CORS_ORIGINS=http://localhost:3000
   COOKIE_SECURE=false # Set to true when serving behind HTTPS
   CHAT_PROVIDER=mock  # Zero-cost offline provider; or openai_compatible
   ALLOW_DOCKER_VERIFICATION_EXECUTION=false
   ```

3. **Build and Launch Containerized Services:**
   ```bash
   docker compose up -d --build
   ```

4. **Run Database Migrations:**
   ```bash
   docker compose exec api alembic -c apps/api/alembic.ini upgrade head
   ```

5. **Verify System Health:**
   ```bash
   # Check API health
   curl -f http://localhost:8000/health
   # Expected output: {"status":"ok","service":"repopilot-api"}

   # Check Web UI
   curl -I http://localhost:3000/
   # Expected output: HTTP/1.1 200 OK
   ```

---

## 3. Database Operations: Migrations, Backups & Recovery

### Running Migrations
Database migrations are managed via Alembic and are **never executed automatically on container boot** to prevent accidental schema corruption during rolling restarts:
```bash
# Check current migration revision
docker compose exec api alembic -c apps/api/alembic.ini current

# Upgrade to latest revision (e.g., 0009_github_pull_requests_unique_run)
docker compose exec api alembic -c apps/api/alembic.ini upgrade head

# Rollback one revision if needed
docker compose exec api alembic -c apps/api/alembic.ini downgrade -1
```

### Automated Backup Procedure
Execute periodic logical backups using `pg_dump`:
```bash
# Generate timestamped compressed backup
BACKUP_FILE="backup_repopilot_$(date +%Y%m%d_%H%M%S).sql.gz"
docker compose exec -T db pg_dump -U repopilot -d repopilot | gzip > "$BACKUP_FILE"
echo "Backup written to $BACKUP_FILE"
```

### Point-in-Time Recovery
To restore a backup into a fresh database instance:
```bash
# Stop application services
docker compose stop api worker web

# Restore database schema and data
gunzip -c "$BACKUP_FILE" | docker compose exec -T db psql -U repopilot -d repopilot

# Restart application services
docker compose start api worker web
```

---

## 4. Secret Protection & Environment Validation

- **Zero Committed Secrets:** Passwords, API keys, and session secrets are loaded strictly from environment variables (`.env`). The `.env` file is explicitly ignored in [`.gitignore`](file:///home/mehndi/Projects/repopilot-fix/.gitignore) and [`.dockerignore`](file:///home/mehndi/Projects/repopilot-fix/.dockerignore).
- **Masked Credentials:** Error handlers in the API and GitHub PR services automatically redact sensitive tokens (`ghp_`, `gho_`, `github_pat_`, `AIza`, `sk-`, `Bearer`).
- **CSRF Token Validation:** State-mutating API operations enforce cryptographic HMAC CSRF token validation bound to the active user session.

---

## 5. Deployment Restrictions & Remaining Risks

1. **In-Memory Rate Limiting Scope:**
   The sliding-window rate limiter runs in-process on each API instance (`_RATE_LIMIT_BUCKETS`). For single-node Docker Compose deployments, this provides robust memory-bounded abuse mitigation. If scaled horizontally across multiple load-balanced API replicas, rate limiting should be moved to a shared Redis cluster.
2. **Docker In Docker Considerations:**
   The worker executes containerized benchmarks. In environments where the host Docker daemon is mounted to the private worker, container capabilities are strictly dropped (`ALL`) and user namespaces are enforced. Public cloud environments with restricted nested virtualization should run workers with `ALLOW_DOCKER_VERIFICATION_EXECUTION=false` (simulated execution).
3. **Zero Billing Verification:**
   All services and configurations documented herein utilize 100% free open-source software (Linux, PostgreSQL, Node.js, Python, Docker). No paid cloud subscriptions or billable APIs were initiated or required.
4. **Public Deployment Status:**
   In accordance with verification guidelines, RepoPilot Fix is verified locally in containerized and bare-metal environments. No public cloud URL is claimed without an active, verified, publicly accessible endpoint.
