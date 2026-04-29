"""Allow `python -m orgs_ai_harness` to run the CLI."""

from orgs_ai_harness.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
