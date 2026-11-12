# Architecture

## Day 1 shape

```text
Browser
  └── apps/web (Next.js / TypeScript / Tailwind)
        └── HTTP GET /health ──> apps/api (FastAPI)
                                  └── PostgreSQL (configured, not used yet)
```

The browser-visible API URL is supplied through `NEXT_PUBLIC_API_BASE_URL`. The API exposes a minimal health route. PostgreSQL connection configuration is reserved in `.env.example`; no database connection is opened in this milestone.

## Planned boundaries

- **Ingestion and parsing:** accept repository metadata and use Tree-sitter for syntax-aware indexing.
- **Retrieval:** store project metadata and code intelligence in PostgreSQL, adding vector retrieval only when the use case is defined.
- **Agent orchestration:** keep model/tool policy in the API; expose reviewable proposed changes to the web client.
- **Execution:** run imported repository code only in isolated, resource-limited containers or sandboxes, never on the host.
- **GitHub integration:** add a GitHub App with least-privilege permissions and short-lived credentials.

## Trust boundaries

Repositories, issues, and generated patches are untrusted input. Secrets remain server-side. Any future code execution must use an isolated workspace with explicit network, CPU, memory, and time limits. The frontend only receives data intended for the user.
