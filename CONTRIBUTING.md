# Contributing to Agent Browser

Thank you for helping build a small, inspectable browser runtime for agents. The most useful
contributions preserve the v0.x authority boundary, keep behavior deterministic, and make claims
only when an exact commit proves them.

## Before you start

- Read the [API contract](docs/API.md), [architecture](docs/ARCHITECTURE.md), and
  [security model](docs/SECURITY.md).
- Search existing issues before proposing a change.
- For vulnerabilities, use the private process in [SECURITY.md](SECURITY.md), not an issue.
- Keep credentials, cookies, private URLs, host details, and real user data out of fixtures,
  screenshots, logs, commits, and discussions.

## Development setup

Agent Browser requires Python 3.11 or later. The repository lock includes the development
tools used by CI.

```bash
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install --require-hashes -r requirements.lock
python -m pip install --no-build-isolation --no-deps -e .
```

`--no-build-isolation` keeps editable installation inside the already hash-locked environment;
do not allow an isolated build backend to resolve undeclared packages from the network.

Run the deterministic checks before opening a pull request:

```bash
python -m ruff format --check .
python -m ruff check .
python -m mypy src
python -m pytest -q
python scripts/verify_release.py --manifest-only
python scripts/verify_release.py --security-only
```

The Docker Compose quickstart targets Linux Docker Engine. Rootless container acceptance and
dedicated-runner qualification are maintainer release gates; a contributor should not weaken or
replace them to make a pull request pass.

## Change design

1. Keep each pull request focused on one contract change.
2. Add tests for success, denial, boundary, cleanup, and regression behavior as applicable.
3. Update public documentation in the same pull request when behavior changes.
4. Preserve closed request models and stable error codes unless the API revision is intentional.
5. Do not add arbitrary script execution, raw DevTools access, credential import, or a wider
   network listener as a convenience feature.
6. Do not commit generated demo media as proof unless it was captured from the exact tested
   commit and its evidence record is complete.

## Pull requests

Use the repository pull-request template. Describe the base and exact head commits, commands
run, security implications, rollback, known limitations, and dependency ordering. GitHub-hosted
CI evaluates pull-request code; trusted self-hosted jobs only run through the protected
maintainer path.

Dependency changes must update both `requirements.in` and the hash-locked
`requirements.lock`. Do not hand-edit resolved versions or hashes. The repository's lock-refresh
workflow produces a candidate artifact for review.

By contributing, you agree that your contribution is licensed under the repository's
[Apache License 2.0](LICENSE).

## Community

Follow the [Code of Conduct](CODE_OF_CONDUCT.md). Be specific, kind, and willing to separate a
product preference from a security or compatibility requirement.
