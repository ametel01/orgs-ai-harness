---
name: horizon-deploy-upgrade
description: Use this skill when working with Horizon Starknet deployment, declaration, address export, class hash comparison, market initialization, liquidity seeding, devnet/fork Docker stacks, Sepolia/mainnet env files, sncast accounts, or upgrade scripts in deploy/.
---

# Horizon Deploy Upgrade

## Purpose

Guide deployment and upgrade work for Horizon Protocol contracts using `sncast`, Docker devnets, network env files, class hashes, address exports, and market initialization parameters.

## When to use

Use for `deploy/`, `.env.<network>`, `deploy/addresses/*.json`, `docker-compose*.yml`, `docker/Dockerfile.deployer`, market seeding, `calc-anchor.sh`, `declare.sh`, `deploy.sh`, `upgrade.sh`, or devnet/fork startup tasks.

## Inspect first

- `deploy/CLAUDE.md`
- `deploy/README.md`
- `deploy/MARKET_INITIALIZATION.md`
- `Makefile`
- `.env.example`
- Relevant script under `deploy/scripts/`
- Relevant `deploy/addresses/<network>.json`
- `docker-compose.yml` or `docker-compose.fork.yml` for local network changes

## Network model

- `devnet`: local starknet-devnet-rs on `http://localhost:5050`, uses predeployed accounts and mock/test setup.
- `fork`: mainnet fork via `docker-compose.fork.yml`, real Pragma TWAP oracle, typically no seed liquidity.
- `sepolia`: requires deployer credentials in `.env.sepolia` and `deploy/accounts/sepolia.json`.
- `mainnet`: requires secure deployer credentials; avoid automated confirmation and always dry-run upgrades first.

## Standard workflow

1. Build contracts before deployment operations: `make build` or `cd contracts && scarb build`.
2. Verify the target env file exists and contains required non-secret parameters. Do not print or commit private keys.
3. For new markets, calculate anchors with `deploy/scripts/calc-anchor.sh <apy_percent>` and use WAD values for scalar root, anchor, and fee rate.
4. Use the existing scripts rather than hand-written `sncast` sequences:
   - `./deploy/scripts/declare.sh <network>`
   - `./deploy/scripts/deploy.sh devnet|sepolia|mainnet`
   - `./deploy/scripts/upgrade.sh <network> --dry-run`
   - `./deploy/scripts/export-addresses.sh <network>`
5. After deployment, keep `.env.<network>` and `deploy/addresses/<network>.json` consistent so frontend and indexer config can consume the new addresses.
6. If address JSON shape changes, update frontend `src/shared/config/addresses.ts` and indexer `src/lib/constants.ts` in a separate integration step.

## Local commands

```bash
make dev-up
make dev-logs
make dev-down
make dev-fork
make dev-fork-logs
make dev-fork-down
```

Indexer profile services in root `docker-compose.yml` require the `indexer` profile and an `apibara-dna-starknet:latest` image.

## Upgrade workflow

Always preview first:

```bash
./deploy/scripts/upgrade.sh devnet --dry-run
./deploy/scripts/upgrade.sh sepolia --dry-run
./deploy/scripts/upgrade.sh mainnet --dry-run
```

Use `--contract NAME` for scoped upgrades. Avoid `--yes` except on disposable devnet; the script has an extra mainnet confirmation because auto-confirm on mainnet is dangerous.

## Market health checks

- Healthy reserves should remain roughly balanced; use `read_state`.
- Check implied rate with `get_ln_implied_rate`.
- Extreme APY often means failed seeding or severe reserve imbalance.
- Recommended seed liquidity is at least 1,000,000 tokens per market; current scripts use WAD-denominated values.

## Validation commands

```bash
make build
cd contracts && scarb fmt --check
./deploy/scripts/calc-anchor.sh 5
./deploy/scripts/upgrade.sh devnet --dry-run
```

For live scripts, validate against devnet or fork before sepolia/mainnet.

## Common pitfalls

- Never commit secrets from `.env.sepolia`, `.env.mainnet`, or account files.
- `Class already declared` is not automatically fatal; scripts handle already-declared hashes.
- Fork config contains RPC URLs; treat provider keys as sensitive if changing them.
- `deploy/addresses/*.json` may include placeholders like `0x0` for undeployed RouterStatic/PyLpOracle; consumers must handle that.
- Mainnet deploy script support may differ from README claims; inspect the current script before running.

## Escalation rules

Ask for explicit approval before live sepolia/mainnet deploys, mainnet upgrades, changing treasury/admin ownership, changing liquidity seeding defaults, or altering env/account file handling.
