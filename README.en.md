<div align="center">

<img src="docs/launch/assets/logo.png" alt="Tianshu" width="220">

# Tianshu

**Tianshu is a governable, verifiable Agent OS designed to learn and evolve continuously.**

*[中文 README](README.md) · Tianshu (天枢) is the first star of the Big Dipper — the pivot the sky turns around.*

[![CI](https://github.com/MJ-CJM/tianshu/actions/workflows/ci.yml/badge.svg)](https://github.com/MJ-CJM/tianshu/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Stars](https://img.shields.io/github/stars/MJ-CJM/tianshu?style=flat&logo=github&label=Star&color=CDA95C)](https://github.com/MJ-CJM/tianshu/stargazers)

</div>

---

## What is this

Tianshu is a governable, verifiable Agent OS designed to learn and evolve continuously. You issue an **Edict** via Web, API, CLI, Feishu, or Telegram; the system turns that goal into a schedulable, decision-aware, auditable execution chain and records execution results, a timeline, cost entries, memory, and supervision reports.

Its organizing metaphor is the six-ministry bureaucracy of Ming-dynasty China: the system is a set of **officials (Personas)**, each with a job — the **Cabinet** plans, the **Ministry of War** executes, the **Censorate** audits, the **Bureau of Transmission** notifies, the **Library** holds memory, the **Ministry of Revenue** tracks cost. The metaphor is just a shell; in code it's cleanly decoupled modules.

```text
Edict → Scheduler → Planner → Agent/DAG/long-task loop
   → Auditor → Notifier → Memory / Profile / Skill growth
```

> **Current v0.4.2 boundary:** Tianshu is for a **trusted local**, single-node environment. Native execution has pre-tool policy and decision hooks. Claude Code/Codex through Keqing is only `contained + experimental`. The local HTTP, WebSocket, and MCP surfaces do not yet have unified authentication and **must not be exposed to an untrusted network**. See the [capability matrix](docs/launch/capability-matrix.md) for verified guarantees and explicit non-guarantees.
>
> Unlike a chat-style "ask-and-answer" agent, Tianshu targets async, long-horizon work where supported milestones can be inspected after execution.

## Positioning: the supervising office above Claude Code

Claude Code is the knife in your hand when you're at the keyboard. Tianshu is an office that can coordinate work around it, in two paths with different maturity boundaries:

- **An MCP host can issue an Edict** — the local MCP server can submit work, check status, and read supported results. It is not an authenticated public endpoint in v0.4.2.
- **Keqing can dispatch an external CLI** — Claude Code or Codex runs in an independent workspace with clean-env and an outer timeout; captured final results and tool events are normalized for the outer chain. This adapter is `contained + experimental`: internal event completeness and Native pre-tool controls are not guaranteed.

The two directions are intentionally not presented as equivalent until the external executor contract is verified at G4.

## Product direction: governance × evidence-backed growth

Tianshu is being built around the intersection of two concerns:

- **Governance with explicit boundaries** — Native tool tiers, policy and human Decision hooks, outbound redaction, clean-env, and emergency stop are implemented for the documented local scope.
- **Growth that must earn promotion** — memory, personas, skill candidates, Universe snapshots, and paired evaluation exist at experimental maturity. Online challenger routing and trusted automatic promotion are planned, not current behavior.

This direction is the intended differentiation; the repository does not claim unsupported market uniqueness or a completed self-evolution loop.

## Current safeguards and their limits

The current safeguards are useful within the trusted-local boundary, but they are not a blanket safety guarantee:

1. **Best-effort cost guardrails** — observed usage is attributed and checked, but a provider can report usage after work has already exceeded the threshold.
2. **Decision surfaces** — Web and Telegram support current-process decisions; Feishu uses command replies. Pending decisions are not yet restart-durable.
3. **Post-run shadow snapshots** — when a Keqing run produces a snapshot, its independent `GIT_DIR` can help inspect or revert file state. This is not a pre-run restore point.
4. **Local emergency stop and redaction** — useful defense in depth, not container or OS isolation.

## Feature highlights

- **🏛️ Six ministries** — planning / execution / audit / notify / memory / cost officials, coordinating over a shared "court" context.
- **🏛️ Local Native chain (stable within limits)** — scheduling, planning, Native execution, audit, and SQLite timeline records on one trusted node.
- **🧠 Memory + growth candidates (experimental)** — layered memory, profile synthesis, and skill candidate records; task-level benefit still needs evidence gates.
- **🥷 Runtime defense in depth (limited scope)** — outbound redaction, per-segment bash grading, clean-env, and tiered emergency stop. See [SECURITY.md](SECURITY.md).
- **🌌 Universe operations (experimental)** — snapshot, branch, diff, and manual switch. Current routing remains champion-only.
- **📏 Paired evaluation (experimental)** — historical samples run in local subprocesses with separate ports and databases. They still share host privileges and network, so this is not a security sandbox.
- **🤝 External CLI interop (experimental)** — Keqing provides an outer process boundary, not internal tool interception, a hard cost cap, or a pre-run restore point.
- **💸 Cost records (stable within limits)** — metering and attribution with best-effort budget checks that may overshoot.

## Quick start

```bash
# Backend
uv sync --extra all --extra dev
cp .env.example .env   # fill in TIANSHU_LLM_API_KEY
uv run tianshu doctor  # startup self-check
uv run uvicorn tianshu.app:create_app --factory --reload

# Frontend (separate terminal)
cd web && npm install && npm run dev
```

Drive it from Claude Code:

```bash
claude mcp add --transport http tianshu http://localhost:8000/mcp
```

This command configures a local endpoint. Keep it on a trusted machine/network until the G1 authentication boundary is delivered.

## Cost transparency

A platform that sells cost governance must report its own cost. The typical monthly range has not yet been measured; [docs/launch/cost-baseline.md](docs/launch/cost-baseline.md) records the repeatable method and the evidence gate required before publishing a number. The factory daily budget guardrail is on by default, but it is a best-effort check rather than a provider-side hard limit.

## Governance defaults (privacy first)

| Setting | Default | Toggle |
|---|---|---|
| Telemetry | **off** | `TIANSHU_TELEMETRY=on` (version + startup event only, one env to disable forever) |
| Self-evolution | **off** | experimental candidates remain subject to a manual Decision |
| OTel tracing | **off** | set `TIANSHU_OTEL_ENDPOINT` |
| Daily budget guardrail | **on**, ¥20 | `TIANSHU_DAILY_BUDGET_GUARDRAIL_CNY` |

The full status, evidence, and target gate for every major claim is maintained in the [public capability matrix](docs/launch/capability-matrix.md).

## Contributing

Narrow gate during launch: issues / docs / small fixes welcome; feature PRs need an issue first to align. Best-effort 48h response, no SLA. See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).

## ⭐ Star history

If Tianshu got the job done for you, leave a Star — your star lands on the chart below and marks this project's growth.

<div align="center">

<a href="https://star-history.com/#MJ-CJM/tianshu&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=MJ-CJM/tianshu&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=MJ-CJM/tianshu&type=Date" />
    <img src="https://api.star-history.com/svg?repos=MJ-CJM/tianshu&type=Date" alt="Tianshu star history" width="640">
  </picture>
</a>

</div>

## License

MIT. See [LICENSE](LICENSE).
