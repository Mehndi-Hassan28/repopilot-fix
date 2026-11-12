# Architecture

## Day 4 shape

```text
Browser
  └── apps/web (Next.js / TypeScript / Tailwind)
        └── HTTP ──> apps/api (FastAPI)
                       ├── Fixed public GitHub REST API requests (public metadata and source blobs)
                       ├── Keyword retrieval ──> indexed files at an immutable commit SHA
                       ├── Mock or configured OpenAI-compatible chat provider
                       └── SQLAlchemy ──> PostgreSQL (repositories, index, sessions, messages)
```

The API now resolves the default branch to a commit SHA, reads its recursive Git tree and fetches selected blobs through fixed `api.github.com` routes. It never clones a repository or executes its files. A sequential FastAPI background task stores source text and Tree-sitter symbols, tied to that immutable SHA. Index jobs are unique per repository and SHA. Limits are configurable with `INDEX_MAX_FILES`, `INDEX_MAX_FILE_BYTES`, and `INDEX_MAX_API_REQUESTS` (defaults 300, 200000, and 350). Unsupported extensions, hidden/generated/build/vendor paths, lockfiles, and oversized files are skipped. GitHub's unauthenticated public API rate limit applies.

The browser-visible API URL is supplied through `NEXT_PUBLIC_API_BASE_URL`. `DATABASE_URL` configures SQLAlchemy, and Alembic applies schema changes. Chat adds `POST /api/chat/sessions`, `GET /api/chat/sessions`, `GET /api/chat/sessions/{id}`, and `POST /api/chat/sessions/{id}/messages`. Sessions bind to the latest completed indexing job's SHA; retrieval requires both repository ID and SHA. Keyword matching selects up to five 11-line source windows and caps evidence at 12,000 characters. Questions are capped at 2,000 characters and responses at 500 provider tokens. Citations come from those selected indexed source windows, and unknown citation labels are discarded.

## Planned boundaries

- **Ingestion and parsing:** use Tree-sitter for JavaScript, TypeScript, and Python declarations/imports; parser failures safely yield no symbols while file indexing continues.
- **Retrieval:** case-insensitive keyword frequency ranking over bounded indexed source windows; no external search service or embedding model is required.
- **Chat provider:** `CHAT_PROVIDER=mock` is the no-cost default. `openai_compatible` uses server-side `CHAT_BASE_URL`, `CHAT_API_KEY`, and `CHAT_MODEL`; requests have a 20-second timeout.
- **Agent orchestration:** keep model/tool policy in the API; expose reviewable proposed changes to the web client.
- **Execution:** run imported repository code only in isolated, resource-limited containers or sandboxes, never on the host.
- **GitHub integration:** this milestone uses only the unauthenticated public REST API. Private access remains out of scope; a future GitHub App should use least-privilege permissions and short-lived credentials.

## Trust boundaries

Indexed source text is untrusted evidence. The chat provider is instructed to ignore instructions inside source and answer only from retrieved context; application citations are checked against retrieved file paths and line windows.

Repositories, issues, and generated patches are untrusted input. Import stores metadata and never clones or executes repository code. Secrets remain server-side. Any future code execution must use an isolated workspace with explicit network, CPU, memory, and time limits. The frontend only receives data intended for the user.
