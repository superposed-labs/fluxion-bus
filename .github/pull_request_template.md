<!-- Keep PRs focused on one logical change. See CONTRIBUTING.md. -->

## What & why

<!-- What does this change do, and why? Link any related issue (e.g. Closes #123). -->

## Config / .env impact

<!-- New or changed settings? Update .env.example with placeholders. "None" is fine. -->

## Checklist

- [ ] `ruff check src tests` and `ruff format --check src tests` pass
- [ ] `pytest -q` passes (added/updated tests for new behavior where practical)
- [ ] Frontend (only if `web/` changed): `cd web && npm run build` passes
- [ ] No secrets, generated files, or personal absolute paths committed
- [ ] Commits are signed off (`git commit -s`) per the DCO — see CONTRIBUTING.md
