# Contributing

Thanks for taking the time to contribute!

## Development setup

Prerequisites:

- Python 3.11+
- `make`

Create a virtual environment and install dev dependencies:

```sh
make venv
```

Run the full local CI suite:

```sh
make ci
```

## Tooling

- Format: `make format`
- Lint: `make lint`
- Typecheck: `make typecheck`
- Test: `make test`
- Coverage: `make coverage` (generates `coverage.xml`)

## Code style

- Keep code formatted and lint-clean (`ruff`).
- Add type hints for new/changed public functions and modules.
- Include tests for behavior changes.

## Commit messages

Use Conventional Commits (e.g., `feat: ...`, `fix: ...`, `chore: ...`).

## Pull requests

1. Keep PRs focused and small when possible.
2. Ensure `make ci` passes locally.
3. Describe the change, the motivation, and any follow-ups.

