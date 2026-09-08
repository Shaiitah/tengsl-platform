# TENGSL — GitHub workflow

## Source of truth

GitHub is the single source of truth for the project. ZIP archives are release
artifacts and are never used as the development baseline.

Recommended branches:

- `main` — stable, releasable code.
- `develop` — active integration.
- `feature/*` — individual changes.

## Normal change flow

1. Create `feature/<short-name>` from `develop`.
2. Make the change in the working tree.
3. Run `python scripts/release_check.py`.
4. Run the relevant backend/edge tests.
5. Open a Pull Request into `develop`.
6. GitHub Actions must pass.
7. Merge only after review.
8. When the version is ready, merge/promote to `main`.
9. Update `VERSION` and `BUILD_INFO.json`.
10. Create a Git tag in the exact form `vX.Y.Z`.
11. The release workflow creates the ZIP and SHA-256 checksum.

## Release rule

Never manually assemble a release ZIP from several previous ZIP files.
`git archive` creates the release directly from the tagged commit.

## Regression protection

`PLATFORM_FEATURES.yml` is the release contract. `scripts/release_check.py`
checks the presence of critical TENGSL capabilities, including database
backup/restore/merge, diagnostics, agent authentication, Alembic, and the
dashboard database functions.

If a feature is intentionally removed, that removal must be explicit:
update the feature contract and document the change in the Pull Request.

## Secrets

Do not commit `.env`, production credentials, database dumps, ntfy tokens,
agent tokens, or private keys. Use GitHub Actions Secrets for CI/CD credentials
when a future deployment workflow needs them.

## Database safety

Before schema changes or restore/merge operations:

1. Create a database backup.
2. Make the change in a feature branch.
3. Test migration/restore behavior.
4. Merge only after CI passes.

## Repository settings to enable on GitHub

For `main`:

- Require a Pull Request before merging.
- Require status checks to pass.
- Require branches to be up to date.
- Block force-pushes.
- Restrict deletion of the branch.
- Keep the repository private until deployment/security policy is finalized.
