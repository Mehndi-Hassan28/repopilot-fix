# Contributing to RepoPilot Fix

Thank you for your interest in contributing to RepoPilot Fix! We are committed to building an open, robust, and secure autonomous software repair platform.

---

## 1. Code of Conduct

We are dedicated to providing a welcoming, inclusive, and harassment-free experience for everyone. Please be respectful, constructive, and collaborative in all communications.

---

## 2. Development Setup

### Prerequisites
- Python 3.11+ (Python 3.12 recommended)
- Node.js 22+ and npm 10+
- PostgreSQL 14+ (local or Docker)
- Docker Engine 24+ (required for running real sandbox verification tests)

### Local Environment Installation
```bash
# Clone the repository
git clone https://github.com/Mehndi-Hassan28/repopilot-fix.git
cd repopilot-fix

# Configure local environment
cp .env.example .env

# Set up backend virtual environment
python -m venv apps/api/.venv
source apps/api/.venv/bin/activate
pip install -e "./apps/api[dev]"

# Set up frontend dependencies
npm install

# Apply database migrations
alembic -c apps/api/alembic.ini upgrade head
```

---

## 3. Development Workflow & Testing

Before submitting a pull request, ensure all linters, static type checks, and test suites pass:

```bash
# 1. Run Python style and linter checks
ruff check apps/api

# 2. Run backend pytest suite (unit and mocked tests)
pytest apps/api/tests -m "not docker"

# 3. Run real Docker sandbox tests (if Docker daemon is accessible)
pytest apps/api/tests/test_proof_of_fix_e2e_docker.py -m docker

# 4. Run frontend linters and tests
npm run lint
npm run test

# 5. Verify Next.js production build
npm run build
```

---

## 4. Coding & Security Guidelines

- **Never Commit Secrets:** Do not commit `.env`, API keys, session tokens, or private certificates.
- **Strict Boundary Separation:** Public API endpoints must never directly mount the Docker socket or execute untrusted shell commands. All execution must proceed through the private worker boundary.
- **Database Migrations:** Schema changes must be written as forward migrations in `apps/api/migrations/versions/`. Migrations must never silently drop data and must include pre-flight duplicate guards when adding unique constraints.
- **Non-Root Execution:** Container definitions must run under unprivileged non-root users (`repopilot:1000` or `nextjs:1001`).

---

## 5. Pull Request Process

1. Fork the repository and create your branch from `main`:
   ```bash
   git checkout -b feat/your-feature-name
   ```
2. Write clean, modular code with accompanying unit tests.
3. Commit with concise, descriptive commit messages adhering to Conventional Commits:
   - `feat(...)`: New feature or capability.
   - `fix(...)`: Bug fix or patch.
   - `docs(...)`: Documentation changes.
   - `test(...)`: Adding or updating test suites.
4. Push your branch to GitHub and open a Pull Request against `main`.
5. Ensure GitHub Actions CI workflows (`CI/api` and `CI/web`) pass cleanly.
6. A maintainer will review your code. Address any feedback promptly!
