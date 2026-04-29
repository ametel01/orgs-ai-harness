.PHONY: sync format lint typecheck test coverage verify security pre-commit build

sync:
	uv sync --frozen

format:
	uv run ruff format .

lint:
	uv run ruff format --check .
	uv run ruff check .

typecheck:
	uv run pyright

test:
	uv run pytest -q

coverage:
	uv run pytest -q --cov=orgs_ai_harness --cov-report=term-missing

verify: lint typecheck coverage

security:
	uv run pip-audit
	uv run bandit -r src/orgs_ai_harness
	uv run detect-secrets-hook --baseline .secrets.baseline $$(git ls-files)

pre-commit:
	uv run pre-commit run --all-files

build:
	uv build
