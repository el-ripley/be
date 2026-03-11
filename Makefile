.PHONY: install dev test test-unit test-integration test-coverage lint format type-check clean run worker pre-commit infra-up infra-down infra-reset

install:
	poetry install --with dev

dev:
	poetry run python src/main.py

worker:
	poetry run python src/workers/sync_worker.py

test:
	poetry run pytest tests/unit/ tests/integration/ -v

test-unit:
	poetry run pytest tests/unit/ -v

test-integration:
	poetry run pytest tests/integration/ -v

test-coverage:
	poetry run pytest tests/unit/ tests/integration/ --cov=src --cov-report=html --cov-report=term-missing

lint:
	poetry run flake8 src/ tests/

format:
	poetry run black src/ tests/
	poetry run isort src/ tests/

type-check:
	poetry run mypy src/

pre-commit:
	pre-commit run --all-files

infra-up:
	docker compose -f docker-compose.infra.yml up -d

infra-down:
	docker compose -f docker-compose.infra.yml down

infra-reset:
	docker compose -f docker-compose.infra.yml down -v && docker compose -f docker-compose.infra.yml up -d && sleep 3 && ./scripts/init_postgres.sh

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .coverage htmlcov/ coverage.xml .pytest_cache 2>/dev/null || true
