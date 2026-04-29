---
name: horizon-cairo-contracts
description: Use this skill when editing Horizon Protocol Cairo contracts, snforge tests, WAD math, PT/YT/SY token flows, Router functions, Market AMM logic, Oracle integration, access control, or upgradeable Starknet components in contracts/src and contracts/tests.
---

# Horizon Cairo Contracts

## Purpose

Guide safe contract changes in `contracts/` for Horizon's Pendle-style Starknet protocol: SY wrappers, PT/YT split, Market AMM, Factory, MarketFactory, Router, oracles, mocks, and snforge tests.

## When to use

Use for Cairo source or test edits, new contract APIs, event changes, math changes, slippage/expiry behavior, upgradeability, access control, or anything that changes generated ABIs consumed by the frontend or indexer.

## Inspect first

- `contracts/CLAUDE.md`
- `contracts/Scarb.toml`
- The target file in `contracts/src/`
- At least one related test in `contracts/tests/`
- Related interface in `contracts/src/interfaces/` when changing public functions or events
- `contracts/tests/utils.cairo` for setup helpers before adding tests

## Repository map

- `contracts/src/tokens/`: `sy.cairo`, `sy_with_rewards.cairo`, `pt.cairo`, `yt.cairo`
- `contracts/src/market/`: AMM, market factory, WAD/cairo_fp market math
- `contracts/src/router.cairo` and `router_static.cairo`: user entry points and previews
- `contracts/src/libraries/`: WAD math, fixed-point helpers, roles, errors, oracle utilities
- `contracts/src/oracles/`: Pragma and PT/YT/LP pricing helpers
- `contracts/tests/`: focused snforge tests by domain, plus `utils.cairo`

## Standard workflow

1. Check the pinned tool versions in `.tool-versions` and `contracts/Scarb.toml`; this repo currently uses Scarb/Starknet 2.16.x and Starknet Foundry 0.58.x even if older docs mention 2.15/0.54.
2. Read the contract, its interface, and a related test before editing.
3. Preserve the protocol model: underlying -> SY -> PT + YT -> Market; Router remains the user-facing entry point with slippage/deadline protection.
4. Use WAD-scaled math (`10^18`) consistently for amounts, rates, scalar roots, anchors, and fees. Prefer existing helpers in `libraries/math.cairo`, `libraries/math_fp.cairo`, and `market/market_math*.cairo`.
5. Keep access-control invariants: only YT mints/burns PT/YT where intended; core contracts remain owner-upgradeable through OpenZeppelin Ownable/Upgradeable components.
6. Add or update snforge tests before adapting implementation behavior. Tests should expose contract bugs, not be rewritten to bless broken behavior.
7. If public ABIs or events change, note that frontend and indexer codegen must be rerun by a separate integration step.

## Validation commands

Run the narrowest command that covers the edit:

```bash
cd contracts && snforge test test_name
cd contracts && snforge test
cd contracts && scarb build
cd contracts && scarb fmt --check
cd contracts && scarb check
```

For full contract CI parity:

```bash
make build
make test
```

## Common pitfalls

- Do not mix WAD, raw token decimals, and cairo_fp values without explicit conversion.
- Do not bypass Router slippage/deadline checks for user-facing paths.
- Expiry behavior is split: before expiry PT+YT redeem together; after expiry PT redeems 1:1 and YT has no principal value.
- Market parameters are sensitive: `MARKET_SCALAR_ROOT` defaults to `5e18`, fee rate to `0.003e18`, and anchors should be computed with `deploy/scripts/calc-anchor.sh`.
- Event signature changes ripple to `packages/indexer/src/lib/validation.ts`, `packages/indexer/src/indexers/*`, Drizzle schema, docs, and frontend generated ABIs.

## Escalation rules

Stop and ask before changing mainnet/sepolia deployment assumptions, owner/upgrade semantics, fee routing, oracle trust assumptions, or anything that could invalidate deployed addresses.
