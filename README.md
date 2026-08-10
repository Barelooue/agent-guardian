# Agent Guardian

**Lightweight, non-invasive Human-in-the-Loop middleware** — pause any unattended agent at a breakpoint (captcha, login wall, payment, low confidence), notify a human over Telegram / Web UI / Terminal, then resume, steer, or safely abort.

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![Version](https://img.shields.io/badge/version-0.2.0-informational.svg)](./CHANGELOG.md)
[![Protocol](https://img.shields.io/badge/protocol-1.0.0-green.svg)](./docs/protocol.md)

> When your agent is about to do something irreversible, call `ask_human` or `guard_step`. One line. The rest is our job.

---

## Why

Desktop / browser agents fail silently on captchas, expire on logins, and over-click payments. Rewriting every agent is expensive. Agent Guardian sits beside them as a **shadow intervener**: same-process SDK, local Daemon, multi-channel cards, swarm dashboard, and preference-data export — no cloud lock-in.

## Architecture

```text
  ┌─────────────┐   ask_human / guard_step   ┌──────────────┐
  │ Your Agent  │ ─────────────────────────► │  Python SDK  │
  │ Browser-Use │                            │ AgentGuardian│
  │ Custom loop │ ◄───────────────────────── │              │
  └─────────────┘   approve / deny / spatial └──────┬───────┘
                                                    │ HTTP / WS
                                                    ▼
                                             ┌──────────────┐
                                             │    Daemon    │
                                             │ FastAPI+SQL  │
                                             │ Swarm hub    │
                                             └──────┬───────┘
                    ┌───────────────┬───────────────┼───────────────┐
                    ▼               ▼               ▼               ▼
              ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
              │ Telegram │   │  Web UI  │   │  Swarm   │   │ Terminal │
              │  +Bark   │   │ canvas   │   │ dashboard│   │  prompt  │
              └──────────┘   └──────────┘   └──────────┘   └──────────┘
```

Docs: [Protocol](./docs/protocol.md) · [Roadmap](./ROADMAP.md) · [Changelog](./CHANGELOG.md) · [Demo guide](./docs/demo_guide.md) · [Browser-Use](./examples/browser_use_integration/README.md)

---

## Features

| Capability | Notes |
| --- | --- |
| **CAS state lock** | SQLite WAL + optimistic concurrency; concurrent `ask_human` safe |
| **Auto screenshot** | JPEG ≤512 KiB; permission / no-display → text-only degrade |
| **Web console** | `/ui/` — intervention queue, snapshot, one-click decide |
| **Spatial canvas** | Point / BBox mark → SoM-style prompt injection + optional rollback |
| **Smart `guard_step`** | Loop / risk / uncertainty detectors before irreversible actions |
| **Swarm dashboard** | Multi-agent grid, shadow Thought/Action, Force Takeover → SQLite |
| **Telegram cards** | Buttons + optional photo; proxy-aware; exponential backoff |
| **Browser-Use hook** | Thin `@tools.action` gate before pay / submit / captcha |
| **DPO / ORPO export** | `export` + `export-dpo` (Qwen2-VL / LLaVA); takeover traces included |
| **Train recipe + benchmark** | `train` (Unsloth / LLaMA-Factory YAML); 10-task synthetic benchmark |
| **Exception tree** | timeout / deny / cancel / failed — typed, awaitable |

---

## Quickstart (≈5 minutes)

### 1. Install & start Daemon

```powershell
git clone https://github.com/<you>/agent-guardian.git
cd agent-guardian
pip install -e ".[dev]"

# Terminal A
python -m agent_guardian serve --host 127.0.0.1 --port 8787
# same as: agent-guardian serve
```

Open the console: [http://127.0.0.1:8787/ui/](http://127.0.0.1:8787/ui/)  
Tabs: **干预队列** (decide + canvas) · **Swarm 大厅** (multi-agent monitor / Force Takeover).

> Editable install (`-e`) does not require `PYTHONPATH`. If you run from a source tree without installing, set `$env:PYTHONPATH="src"`.

### 2. Minimal `ask_human`

```powershell
# Terminal B
python examples/hello_ask_human.py
```

Or in your agent:

```python
import asyncio
from agent_guardian import AgentGuardian

async def main():
    async with AgentGuardian("http://127.0.0.1:8787") as guardian:
        result = await guardian.ask_human(
            reason="Agent 即将提交支付，请确认",
            options=["approve", "deny"],
            include_screenshot=True,  # optional; degrades if capture fails
        )
        print("human chose:", result.selected_option_id)

asyncio.run(main())
```

Decide in the Web UI (or Terminal prompt / Telegram if configured).

### 3. Smart gate + Browser demo (no LLM key)

```powershell
pip install -e ".[browser]"
python -m playwright install chromium

# Daemon already running
python examples/browser_use_integration/demo_guard_step.py --headed
```

Approve loop / payment interventions in `/ui/` (optional canvas mark).  
Captcha-only demo: `demo_captcha_loop.py`. Full walkthrough: [`examples/browser_use_integration/`](./examples/browser_use_integration/).

### 4. Wire into your own agent

```python
from agent_guardian import AgentGuardian
from agent_guardian.smart import SmartInterventionEngine, LoopDetector

engine = SmartInterventionEngine(loop=LoopDetector(repeat_threshold=3))

async with AgentGuardian("http://127.0.0.1:8787", smart_engine=engine) as g:
    result = await g.guard_step(
        action_name="click",
        target="#pay",
        dom_action="click pay",
        agent_id="my-agent",
        timeout=120,
    )
    if not result.proceeded:
        return  # human denied / timeout
    # execute the real action only after proceed
```

Browser-Use: use `GuardianBrowserHook` / `register_browser_use_tools` (see example README).

---

## Telegram (optional remote channel)

```powershell
$env:TELEGRAM_BOT_TOKEN="123456:ABC..."
$env:TELEGRAM_CHAT_ID="123456789"
# China / corporate networks often need:
$env:TELEGRAM_PROXY="http://127.0.0.1:7890"

python -m agent_guardian serve
python examples/hello_telegram.py
```

Diagnose: `python examples/diagnose_telegram.py`.

| Variable | Purpose |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Bot credentials |
| `TELEGRAM_PROXY` | Outbound HTTP(S) proxy |
| `BARK_URL` / `WEBHOOK_URL` | Extra push channels |

---

## Alignment export & benchmark

```powershell
# Classic preference pairs from RESOLVED interventions
python -m agent_guardian export --db agent_guardian.db --output dataset.jsonl

# Multimodal DPO/ORPO (+ spatial + Force Takeover rows from SQLite)
python -m agent_guardian export-dpo --output swarm_dpo.jsonl --format qwen2_vl

# Emit Unsloth / LLaMA-Factory recipe (does not run GPU training)
python -m agent_guardian train --dataset swarm_dpo.jsonl --backend llamafactory

# Synthetic 10-task compare: no_guardian / with_guardian / after_dpo
python -m agent_guardian benchmark --output benchmark_report.json
```

---

## Project layout

```text
src/agent_guardian/
  client/       # SDK: ask_human, guard_step, checkpoints
  daemon/       # FastAPI + channels + Web UI + swarm API
  smart/        # loop / risk / uncertainty engine
  swarm/        # AgentHubManager, takeover, shadow observer
  align/        # DatasetCurator, AgentBenchmark, train recipes
  adapters/     # Browser-Use thin hook
  schemas/      # protocol 1.0.0 (+ spatial)
  ui/           # spatial prompt injector
  exporter.py   # classic DPO JSONL
  snapshot.py   # screen capture + degrade
examples/       # hello_*, browser_use_integration/
docs/           # protocol.md, demo_guide.md
tests/          # Phase 1–8 suite
```

```powershell
pip install -e ".[dev]"
pytest -q
mypy src/agent_guardian
ruff check .
```

---

## Packaging (maintainers)

Built with **Hatchling** (`pyproject.toml`). Wheel includes the Web UI (`daemon/static/ui/index.html`).

```powershell
pip install build
python -m build
# → dist/agent_guardian-0.2.0.tar.gz
# → dist/agent_guardian-0.2.0-py3-none-any.whl

pip install dist/agent_guardian-0.2.0-py3-none-any.whl
agent-guardian --help
```

Publish (when ready): create a GitHub Release `v0.2.0`, attach wheel/sdist, optionally `twine upload dist/*` to PyPI.  
Update `project.urls` in `pyproject.toml` to your real GitHub org/repo before publishing.

---

## Status

| Phase | Theme | Status |
| --- | --- | --- |
| 1 | MVP engine + Telegram | ✅ |
| 2 | Screenshot + Web UI | ✅ |
| 3 | Browser-Use adapter + demos | ✅ (Claude Computer Use next) |
| 4 | Community / channels / release | ⏳ open-source publish in progress |
| 5 | Smart `guard_step` | ✅ |
| 6 | Spatial canvas + rollback | ✅ |
| 7 | Swarm control plane + dashboard | ✅ |
| 8 | DPO curator / train recipe / benchmark | ✅ |

Roadmap: [`ROADMAP.md`](./ROADMAP.md). Demo recording: [`docs/demo_guide.md`](./docs/demo_guide.md).

---

## Contributing

Issues and PRs welcome — especially new **channels**, **agent adapters**, and **demo clips**.

- Protocol changes must bump `protocol_version` and update `docs/protocol.md`.
- Prefer thin hooks over rewriting upstream agents.
- Do not commit secrets (`.env`, bot tokens, real payment screenshots with PII).
- See [`CONTRIBUTING.md`](./CONTRIBUTING.md).

## License

MIT — see [`LICENSE`](./LICENSE).
