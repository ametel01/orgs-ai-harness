.PHONY: sync format lint typecheck test coverage verify security build

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

verify: lint typecheck test

security:
	@echo "security gate is not configured yet; see issue #70"

build:
	uv build
