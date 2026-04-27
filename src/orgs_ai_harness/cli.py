"""Command line interface for the Org Skill Harness."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from orgs_ai_harness.org_pack import OrgPackError, init_org_pack, resolve_default_root
from orgs_ai_harness.validation import validate_org_pack


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="harness")
    subparsers = parser.add_subparsers(dest="command", required=True)

    org_parser = subparsers.add_parser("org", help="Manage org skill packs")
    org_subparsers = org_parser.add_subparsers(dest="org_command", required=True)
    org_init = org_subparsers.add_parser("init", help="Initialize an org skill pack")
    org_init.add_argument("--name", required=True, help="Organization name for the skill pack")

    subparsers.add_parser("validate", help="Validate the org skill pack")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "org" and args.org_command == "init":
            root = init_org_pack(Path.cwd(), args.name)
            print(f"Initialized org skill pack at {root}")
            return 0

        if args.command == "validate":
            root = resolve_default_root(Path.cwd())
            result = validate_org_pack(root)
            if result.ok:
                print(f"Validation passed for {root}")
                return 0
            for error in result.errors:
                print(f"error: {error}", file=sys.stderr)
            return 1

    except OrgPackError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    parser.error("unsupported command")
    return 2

