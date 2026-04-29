#!/usr/bin/env python3
"""Deterministic local check for generated draft pack shape."""
from pathlib import Path
import sys

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
required = [
    'onboarding-summary.md',
    'unknowns.yml',
    'resolvers.yml',
    'evals/onboarding.yml',
    'pack-report.md',
]
missing = [relative for relative in required if not (root / relative).is_file()]
if missing:
    print('missing generated artifact(s): ' + ', '.join(missing), file=sys.stderr)
    raise SystemExit(1)
print('draft pack shape ok')
