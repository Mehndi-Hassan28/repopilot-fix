# Architecture

## Day 3 shape

```text
Browser
  └── apps/web (Next.js / TypeScript / Tailwind)
        └── HTTP ──> apps/api (FastAPI)
                       ├── Fixed public GitHub REST API requests (public metadata and source blobs)
                       └── SQLAlchemy ──> PostgreSQL
```

The API now resolves the default branch to a commit SHA, reads its recursive Git tree and fetches selected blobs through fixed `api.github.com` routes. It never clones a repository or executes its files. A sequential FastAPI background task stores source text and Tree-sitter symbols, tied to that immutable SHA. Index jobs are unique per repository and SHA. Limits are configurable with `INDEX_MAX_FILES`, `INDEX_MAX_FILE_BYTES`, and `INDEX_MAX_API_REQUESTS` (defaults 300, 200000, and 350). Unsupported extensions, hidden/generated/build/vendor paths, lockfiles, and oversized files are skipped. GitHub's unauthenticated public API rate limit applies.

The browser-visible API URL is supplied through `NEXT_PUBLIC_API_BASE_URL`. `DATABASE_URL` configures SQLAlchemy, and Alembic applies schema changes. Day 3 adds `POST /api/repositories/{id}/index`, `GET /api/repositories/{id}/index`, `GET /api/repositories/{id}/files`, and `GET /api/repositories/{id}/search`.

## Planned boundaries

- **Ingestion and parsing:** use Tree-sitter for JavaScript, TypeScript, and Python declarations/imports; parser failures safely yield no symbols while file indexing continues.
- **Retrieval:** store project metadata and code intelligence in PostgreSQL, adding vector retrieval only when the use case is defined.
- **Agent orchestration:** keep model/tool policy in the API; expose reviewable proposed changes to the web client.
- **Execution:** run imported repository code only in isolated, resource-limited containers or sandboxes, never on the host.
- **GitHub integration:** this milestone uses only the unauthenticated public REST API. Private access remains out of scope; a future GitHub App should use least-privilege permissions and short-lived credentials.

## Trust boundaries

Repositories, issues, and generated patches are untrusted input. Import stores metadata and never clones or executes repository code. Secrets remain server-side. Any future code execution must use an isolated workspace with explicit network, CPU, memory, and time limits. The frontend only receives data intended for the user.
