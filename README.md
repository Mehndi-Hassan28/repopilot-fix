# RepoPilot Fix

AI codebase intelligence and autonomous fix agent. Day 4 adds grounded repository chat over indexed source with file and line citations.

## Requirements

- Node.js 22 or newer and npm
- Python 3.11 or newer
- PostgreSQL 14 or newer (the API reads `DATABASE_URL`)

## Local development

```sh
cp .env.example .env
npm install
npm run dev
```

In another terminal, install and run the API:

```sh
python -m venv apps/api/.venv
source apps/api/.venv/bin/activate
python -m pip install -e "./apps/api[dev]"
uvicorn repopilot_api.main:app --app-dir apps/api/src --reload --port 8000
```

Create a PostgreSQL database/user using your local PostgreSQL workflow, then set `DATABASE_URL` in `.env`. The application does not create databases automatically. Apply the schema and start the API:

```sh
alembic -c apps/api/alembic.ini upgrade head
uvicorn repopilot_api.main:app --app-dir apps/api/src --reload --port 8000
```

Open http://localhost:3000 to import public repositories with a `https://github.com/owner/repository` URL. Index a repository and use Repository chat to ask questions and browse prior conversations. Set `CORS_ORIGINS` for deployment; do not put secrets in `NEXT_PUBLIC_*` variables. Public GitHub requests are subject to GitHub's unauthenticated API rate limits.

Imported repositories can be indexed from the dashboard. Indexing is limited to public GitHub API access and supports JavaScript, TypeScript, and Python source files. It does not clone or execute repository code. Tune `INDEX_MAX_FILES`, `INDEX_MAX_FILE_BYTES`, and `INDEX_MAX_API_REQUESTS` in `.env` to control per-job resource use. Chat uses lightweight keyword retrieval over the latest completed commit and returns citations resolved from indexed source line ranges. `CHAT_PROVIDER=mock` is free and needs no key. For an OpenAI-compatible endpoint, set `CHAT_PROVIDER=openai_compatible`, `CHAT_BASE_URL`, `CHAT_API_KEY`, and `CHAT_MODEL` in the backend environment. Provider credentials stay server-side.

Day 4 adds chat session and message tables. Apply the migration only to your intended database after reviewing its URL: `alembic -c apps/api/alembic.ini upgrade head`. Starting a conversation requires a completed index, and each session remains pinned to the commit SHA it started with.

## GitHub sign-in and user data

The authentication ownership migration is prepared as `0004_auth_ownership` and is **not applied automatically**. It adds user identities, opaque server-side sessions, repository access links, and nullable chat ownership. Existing repositories and conversations remain unassigned and inaccessible until an administrator explicitly verifies and assigns them; the migration does not backfill ownership.

To test sign-in locally, create a GitHub OAuth App with homepage `http://localhost:3000` and callback `http://localhost:8000/api/auth/github/callback`. Put its client ID and secret only in the backend `.env` as `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET`; set `GITHUB_OAUTH_REDIRECT_URI` to that exact callback, `FRONTEND_URL=http://localhost:3000`, `CORS_ORIGINS=http://localhost:3000`, and `COOKIE_SECURE=false` for localhost only. For deployment, use HTTPS for both origins and `COOKIE_SECURE=true`. Never use `NEXT_PUBLIC_` for credentials. Sign-in requests only `read:user`; repository import and indexing remain public-only. A GitHub login does not authorize private repository access.

Before enabling this version against a database, take and verify a PostgreSQL backup, review the additive revision, and rehearse it on a staging copy. Then explicitly apply `alembic -c apps/api/alembic.ini upgrade head` to the intended database. No migration is run by application startup.

## Checks

```sh
npm run lint
npm run build
python -m pip install -e "./apps/api[dev]"
ruff check apps/api
pytest apps/api
```

CI runs these web and API checks on pushes to `main` and pull requests.

## Layout

- `apps/web`: Next.js App Router, TypeScript, Tailwind CSS
- `apps/api`: FastAPI service, SQLAlchemy models, Alembic migrations, and pytest checks
- `docs/architecture.md`: initial system boundaries and security direction

## Security direction

Repository import stores public metadata only; it does not clone or execute imported code. Treat repositories as untrusted input. Any future code execution needs an isolated, resource-limited sandbox. Keep credentials server-side and out of source control.
