# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] — 2026-08-10

Phase 5–8 control plane: smart intervention, spatial canvas, swarm dashboard, DPO self-evolution loop.

### Added

- **Smart intervention engine** — loop / risk / uncertainty detectors; `AgentGuardian.guard_step()`, checkpoint / rollback
- **Spatial canvas (Phase 6)** — point / BBox annotations on Web UI; SoM-style prompt injection
- **Swarm control plane (Phase 7)** — `AgentHubManager`, REST `/api/swarm/agents`, WS `/ws/swarm`, Force Takeover
- **Swarm Dashboard** — `/ui/` multi-agent grid with Thought / Action / screenshot + takeover modal
- **Force Takeover persistence** — SQLite `takeover_events` (instruction, shadow context, screenshot path); auto-included by `export-dpo`
- **Align module (Phase 8)** — `DatasetCurator` (Qwen2-VL / LLaVA / ORPO), `agent-guardian train` recipes, 10-task `AgentBenchmark`

### CLI

- `agent-guardian export-dpo --output swarm_dpo.jsonl`
- `agent-guardian train --dataset … --backend unsloth|llamafactory`
- `agent-guardian benchmark`

## [0.1.0] — 2026-08-10

First public release of **Agent Guardian** — lightweight Human-in-the-Loop middleware for unattended agents.

### Added

- **CAS concurrency + SQLite optimistic locking** — WAL mode, compare-and-set status transitions, idempotent `client_request_id`, stress-tested concurrent `ask_human`
- **Protocol `1.0.0`** — typed request / decision / cancel envelopes (`docs/protocol.md`)
- **Python SDK** — `AgentGuardian.ask_human`, `async with guard(...)`, typed exception tree (timeout / deny / cancel / failed)
- **Local Daemon** — FastAPI + `agent-guardian serve`, WebSocket + HTTP wait paths
- **Auto screenshot pipeline** — JPEG ≤512 KiB compression; permission / no-display → text-only degrade
- **Web intervention console** — `/ui/` pending list, snapshot preview, one-click decide
- **Telegram Bot channel** — inline buttons, optional `sendPhoto`, proxy support, exponential backoff
- **Bark / Webhook / Terminal** fallback channels
- **Browser-Use adapter** — thin `@tools.action` hooks + Playwright checkout/captcha demo (`examples/browser_use_integration/`)
- **DPO preference export** — `agent-guardian export --output dataset.jsonl` (multimodal JSONL: `id` / `image_path` / `prompt` / `chosen` / `rejected`)

### Documentation

- Root README (architecture, 5‑minute quickstart)
- Demo recording guide (`docs/demo_guide.md`)
- Contributing guide (`CONTRIBUTING.md`)

### Notes

- Claude Computer Use adapter remains on the Phase 3/4 backlog
- Optional extras: `pip install -e ".[browser]"` / `".[browser-use]"` / `".[dev]"`

[0.2.0]: https://github.com/agent-guardian/agent-guardian/releases/tag/v0.2.0
[0.1.0]: https://github.com/agent-guardian/agent-guardian/releases/tag/v0.1.0
