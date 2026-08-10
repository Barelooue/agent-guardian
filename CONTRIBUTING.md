# Contributing to Agent Guardian

Thanks for helping make unattended agents safer to operate. This guide covers how to develop, test, and open a pull request.

## Principles

1. **Thin hooks over forks** — prefer adapters that call `ask_human` without rewriting upstream agents.
2. **Protocol stability** — breaking changes to wire formats must bump `protocol_version` and update `docs/protocol.md`.
3. **No secrets in git** — never commit `.env`, bot tokens, private screenshots with PII, or real payment credentials.
4. **Keep the UI intervention-focused** — the Web console is for deciding open interventions, not dashboards.

## Setup

```powershell
git clone <repo-url>
cd agentassistant
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
$env:PYTHONPATH="src"
```

Optional Browser-Use demo deps:

```powershell
pip install -e ".[browser]"
python -m playwright install chromium
```

## Quality gates (run before PR)

```powershell
python -m ruff check src tests
python -m ruff format --check src tests
python -m mypy src/agent_guardian
python -m pytest tests/ -v
```

## Pull request process

1. Open an issue first for large features (new channel, new agent adapter, protocol change).
2. Create a branch: `feat/...`, `fix/...`, or `docs/...`.
3. Keep PRs focused — one concern per PR when practical.
4. Include tests for behavior changes (CAS, channels, exporters, adapters).
5. Update docs when user-facing behavior changes (`README.md`, `docs/protocol.md`, `CHANGELOG.md` under `[Unreleased]` or the next version).
6. Fill the PR description with: **what**, **why**, **how to test**.

### Suggested PR checklist

- [ ] `ruff` / `mypy` / `pytest` clean locally
- [ ] No secrets or large binary dumps committed
- [ ] Changelog note if the change is user-visible
- [ ] Example or doc snippet if adding a channel / adapter

## Contribution ideas

| Area | Examples |
| --- | --- |
| Channels | Discord, Slack,企业微信, email |
| Adapters | Claude Computer Use, Open Interpreter, custom tool wrappers |
| DX | TypeScript client, Docker Compose Daemon, better Windows install docs |
| Data | Redaction helpers for DPO export, Parquet optional writer |

## Reporting bugs

Include: OS / Python version, Daemon logs (redacted), minimal reproduction, whether Telegram proxy is involved.

Security-sensitive reports (token leaks, auth bypass): open a private security advisory or email maintainers rather than a public issue with secrets.

## Code of conduct

Be respectful. Assume good intent. Disagreement about APIs is fine; personal attacks are not.

## License

By contributing, you agree your contributions are licensed under the MIT License (see `LICENSE`).
