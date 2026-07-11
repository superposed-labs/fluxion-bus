# Contributing to Fluxion

Thanks for your interest in Fluxion! This guide covers how to set up a dev
environment, the checks your change must pass, and how to send a pull request.

## Project layout

- `src/fluxion/` — the Python package (gateway, executors, channels, scheduler,
  usage analytics, web API, MCP server).
- `web/` — the React + Vite frontend for the observation deck. `npm run build`
  emits into `src/fluxion/web/static/` (gitignored), which `fluxion-web` serves.
- `desktop/` — the optional macOS menu-bar companion (Swift).
- `docs/` — architecture and feature docs. `tests/` — the pytest suite.

See [docs/architecture.md](docs/architecture.md) for the full system diagram and
[AGENTS.md](AGENTS.md) if you are driving Fluxion via the sub-agent MCP.

## Development setup

```bash
git clone git@github.com:superposed-labs/fluxion-bus.git
cd fluxion-bus

python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"     # installs ruff + pytest

# Frontend (only if you touch web/)
cd web && npm ci && cd ..
```

Requires Python >= 3.12 and (for the frontend) Node >= 18.

### macOS development app

Run the desktop app against the current working tree without changing the saved
production backend:

```bash
scripts/run-dev-app.sh
```

Use `--skip-web` for Python-only changes and `--rebuild-app` after changing
Swift sources. Run `scripts/run-dev-app.sh --help` for all options.

## Before you open a PR

Run the same checks CI runs (`.github/workflows/ci.yml`). All must pass:

```bash
# Python
ruff check src tests          # lint
ruff format src tests         # auto-format (or `--check` to verify only)
pytest -q                     # tests

# Frontend (only if you changed web/)
cd web && npm run build       # runs tsc typecheck + vite build
```

- **Formatting and linting are enforced.** Run `ruff format` before committing;
  CI fails on unformatted code or lint errors.
- **Add tests** for new behavior and bug fixes where practical. The suite is
  fast (`pytest -q` runs in seconds).
- **Don't commit generated or local files** — build output, `data/`, caches,
  and `.env` are gitignored. Never commit real secrets; update `.env.example`
  with placeholders when you add a new setting.
- **Keep personal absolute paths out of source and docs.**

## Commit and PR conventions

- This repo uses **Conventional Commits**, e.g.
  `feat(scheduler): add weekly reset trigger`,
  `fix(web): no-cache the SPA shell`,
  `refactor(channels/wechat): split adapter`. Keep the subject imperative and
  scoped to one logical change.
- Branch off `main` and open a PR against `main`. Keep PRs focused; split
  unrelated changes.
- Describe **what** changed and **why**, and note any config/`.env` impact.

## Sign your work (Developer Certificate of Origin)

Contributions are accepted under the **Developer Certificate of Origin (DCO)** —
a lightweight, one-line affirmation that you wrote the patch (or otherwise have
the right to submit it) under the project's license. There is **no CLA**, and you
keep the copyright to your contribution.

Add a `Signed-off-by` line to each commit by committing with `-s`:

```bash
git commit -s -m "fix(web): no-cache the SPA shell"
```

This appends a trailer from your `git config user.name` / `user.email`:

```
Signed-off-by: Jane Doe <jane@example.com>
```

By signing off you certify the [DCO](https://developercertificate.org/) (v1.1).
Forgot to sign a commit? `git commit --amend -s` (or `git rebase --signoff` for
a series) fixes it before you push.

## Reporting bugs and requesting features

- Bugs and feature requests: open a GitHub issue with steps to reproduce,
  expected vs. actual behavior, and your OS / Python / executor versions.
- Security issues: **do not** file a public issue — follow
  [SECURITY.md](SECURITY.md).

## Code of conduct

We expect all contributors to adhere to the project's [Code of Conduct](CODE_OF_CONDUCT.md) to maintain a welcoming, respectful, and safe community.

## License

By contributing, you agree that your contributions are licensed under the
project's [Apache License 2.0](LICENSE), and you certify the
[Developer Certificate of Origin](https://developercertificate.org/) by signing
off your commits (see "Sign your work" above).
