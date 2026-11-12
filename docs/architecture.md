# Architecture

## Day 2 shape

```text
Browser
  └── apps/web (Next.js / TypeScript / Tailwind)
        └── HTTP ──> apps/api (FastAPI)
                       ├── Fixed public GitHub REST API request (metadata only)
                       └── SQLAlchemy ──> PostgreSQL
```

The browser-visible API URL is supplied through `NEXT_PUBLIC_API_BASE_URL`. `DATABASE_URL` configures SQLAlchemy, and Alembic applies schema changes. The API exposes health, repository import, list, and detail endpoints. It validates GitHub repository URLs and constructs requests only to `https://api.github.com/repos/{owner}/{repo}`. Timeouts and GitHub not-found, rate-limit, and upstream errors are translated to API errors. Unique GitHub IDs and full names make imports idempotent.

## Planned boundaries

- **Ingestion and parsing:** accept repository metadata and use Tree-sitter for syntax-aware indexing.
- **Retrieval:** store project metadata and code intelligence in PostgreSQL, adding vector retrieval only when the use case is defined.
- **Agent orchestration:** keep model/tool policy in the API; expose reviewable proposed changes to the web client.
- **Execution:** run imported repository code only in isolated, resource-limited containers or sandboxes, never on the host.
- **GitHub integration:** this milestone uses only the unauthenticated public REST API. Private access remains out of scope; a future GitHub App should use least-privilege permissions and short-lived credentials.

## Trust boundaries

Repositories, issues, and generated patches are untrusted input. Import stores metadata and never clones or executes repository code. Secrets remain server-side. Any future code execution must use an isolated workspace with explicit network, CPU, memory, and time limits. The frontend only receives data intended for the user.
