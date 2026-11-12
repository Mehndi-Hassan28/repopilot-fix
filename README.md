# RepoPilot Fix

AI codebase intelligence and autonomous fix agent. Day 1 establishes a small, runnable web and API monorepo.

## Requirements

- Node.js 22 or newer and npm
- Python 3.11 or newer
- PostgreSQL is configurable for upcoming persistence work; the Day 1 health endpoint does not require a database.

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

Open http://localhost:3000. The UI checks `NEXT_PUBLIC_API_BASE_URL/health` (defaults to `http://localhost:8000`). Set `CORS_ORIGINS` for deployment; the API currently allows the local web origin. Do not put secrets in `NEXT_PUBLIC_*` variables.

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
- `apps/api`: FastAPI service and pytest checks
- `docs/architecture.md`: initial system boundaries and security direction

## Security direction

Imported repositories must be treated as untrusted input. Never execute their code on the host; future analysis and execution features need isolated, resource-limited sandboxes. Keep credentials server-side and out of source control.
