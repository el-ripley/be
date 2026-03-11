# Contributing to El Ripley AI Agent

## Local development setup

1. **Prerequisites**: Python 3.11+, Poetry, Docker (for Postgres, Redis, Qdrant).
2. **Install**: `poetry install --with dev`
3. **Infrastructure**: `make infra-up` then `./scripts/init_postgres.sh`
4. **Environment**: Copy `.env.example` to `.env` and set required variables (e.g. `OPENAI_API_KEY`, `JWT_SECRET_KEY`, `ENCRYPTION_KEY`).
5. **Run**: `make dev` (API) or `make worker` (sync worker).

## Branch naming

- `feat/<short-description>` — new features
- `fix/<short-description>` — bug fixes
- `chore/<short-description>` — tooling, deps, refactors

## Code style and quality

- **Format**: `make format` (Black + isort).
- **Lint**: `make lint` (flake8).
- **Types**: `make type-check` (mypy).
- **Pre-commit**: Run `make pre-commit` or `pre-commit run --all-files` before pushing.

## Tests

- Run unit and integration tests: `make test`
- With coverage: `make test-coverage`
- Unit only: `make test-unit`

Before submitting a merge request, ensure tests pass and (if applicable) new code is covered by tests.

## Submitting changes

1. Create a branch from the default branch.
2. Make changes, add tests, run lint/format/type-check and tests.
3. Open a merge request with a short description of the change.
4. CI will run lint and test stages; address any failures.
