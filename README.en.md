<div align="center">

<img src="web/public/brand.png" alt="Tianshu" width="128">

# Tianshu

**Tianshu is a governable, verifiable Agent OS designed to learn and evolve continuously.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-blue.svg)](pyproject.toml)
[![Version 0.5.0](https://img.shields.io/badge/version-0.5.0-informational.svg)](https://github.com/MJ-CJM/tianshu/releases)

[中文](README.md) · [Current implementation](docs/CURRENT-STATE.md) · [Getting started](docs/usage/getting-started.en.md) · [Capability matrix](docs/launch/capability-matrix.md)

</div>

## Product tour

Tianshu brings task execution, collaborative decision-making, knowledge and integrations,
controlled experimentation, and system governance into one auditable workspace. Every page below
comes from the current implementation; maturity labels describe the supported boundary, not a
release promise. [Open the full feature guide →](docs/usage/feature-tour.en.md) ·
[Current implementation and verification →](docs/CURRENT-STATE.md)

<p align="center">
  <a href="docs/assets/features/control.jpg">
    <img src="docs/assets/features/control.jpg" alt="Tianshu Control Center" width="100%">
  </a><br>
  <a href="docs/usage/feature-tour.en.md#control-center"><b>Control Center</b></a> · <code>Available</code><br>
  <sub>Shows governance posture through real run counts, pending decisions, evidence closure, and direct links to four differentiating capabilities.</sub>
</p>

### Tasks and governance

<table>
  <tr>
    <td width="50%" align="center" valign="top">
      <a href="docs/assets/features/task-workspace.jpg">
        <img src="docs/assets/features/task-workspace.jpg" alt="Tianshu Task Workspace" width="100%">
      </a><br>
      <a href="docs/usage/feature-tour.en.md#royal-study"><b>Task Workspace</b></a> · <code>Available</code><br>
      <sub>Brings immediate, scheduled, long-running, conversational, and Keqing work into one view, with progress derived from recent execution facts.</sub>
    </td>
    <td width="50%" align="center" valign="top">
      <a href="docs/assets/features/edict-create.jpg">
        <img src="docs/assets/features/edict-create.jpg" alt="Tianshu New Task form" width="100%">
      </a><br>
      <a href="docs/usage/feature-tour.en.md#issue-an-edict"><b>New Task</b></a> · <code>Available</code><br>
      <sub>Choose the task type, execution mode, assigned official, and budget in one form.</sub>
    </td>
  </tr>
  <tr>
    <td width="50%" align="center" valign="top">
      <a href="docs/assets/features/long-task-governance.jpg">
        <img src="docs/assets/features/long-task-governance.jpg" alt="Tianshu long-running task governance" width="100%">
      </a><br>
      <a href="docs/usage/feature-tour.en.md#long-running-task-governance"><b>Long-running Governance</b></a> · <code>Available · Bounded</code><br>
      <sub>Govern long-running work with acceptance criteria, checkpoints, pause and resume, in-run guidance, and human decisions.</sub>
    </td>
    <td width="50%" align="center" valign="top">
      <a href="docs/assets/features/scheduler.jpg">
        <img src="docs/assets/features/scheduler.jpg" alt="Tianshu Scheduler" width="100%">
      </a><br>
      <a href="docs/usage/feature-tour.en.md#imperial-observatory"><b>Scheduler</b></a> · <code>Available</code><br>
      <sub>Manage one-time, Cron, and interval schedules, including next-run state and execution history.</sub>
    </td>
  </tr>
  <tr>
    <td colspan="2" align="center" valign="top">
      <a href="docs/assets/features/audit.jpg">
        <img src="docs/assets/features/audit.jpg" alt="Tianshu Audit" width="100%">
      </a><br>
      <a href="docs/usage/feature-tour.en.md#censorate"><b>Audit</b></a> · <code>Available</code><br>
      <sub>Brings audits, failure attribution, policies, and network records together so actions and evidence remain traceable.</sub>
    </td>
  </tr>
</table>

### Collaboration and knowledge

<table>
  <tr>
    <td width="50%" align="center" valign="top">
      <a href="docs/assets/features/officials.jpg">
        <img src="docs/assets/features/officials.jpg" alt="Tianshu Officials" width="100%">
      </a><br>
      <a href="docs/usage/feature-tour.en.md#officials"><b>Officials</b></a> · <code>Available</code><br>
      <sub>Configure roles, departments, routing, delegation, tool permissions, skills, and models.</sub>
    </td>
    <td width="50%" align="center" valign="top">
      <a href="docs/assets/features/consultation.jpg"><img src="docs/assets/features/consultation.jpg" alt="Tianshu Consultation" width="49%"></a>
      <a href="docs/assets/features/cabinet.jpg"><img src="docs/assets/features/cabinet.jpg" alt="Tianshu Cabinet read-only view" width="49%"></a><br>
      <a href="docs/usage/feature-tour.en.md#consultation-and-cabinet"><b>Consultation &amp; Cabinet</b></a> · <code>Available</code><br>
      <sub>Consultation convenes multi-official review; Cabinet provides a read-only view of planning assignments and collaboration history.</sub>
    </td>
  </tr>
  <tr>
    <td colspan="2" align="center" valign="top">
      <a href="docs/assets/features/memory.jpg"><img src="docs/assets/features/memory.jpg" alt="Tianshu Knowledge and Memory" width="32%"></a>
      <a href="docs/assets/features/external.jpg"><img src="docs/assets/features/external.jpg" alt="Tianshu External connections" width="32%"></a>
      <a href="docs/assets/features/notifications.jpg"><img src="docs/assets/features/notifications.jpg" alt="Tianshu Notifications" width="32%"></a><br>
      <a href="docs/usage/feature-tour.en.md#academy-external-affairs-and-messaging"><b>Knowledge &amp; Communications</b></a> · <code>Available · Bounded</code><br>
      <sub>Knowledge manages memory, External handles integrations, and Notifications centralizes messages and delivery.</sub>
    </td>
  </tr>
</table>

### Frontier Lab [Experimental]

Frontier Lab is introduced through Universes; Evolution, Evals, and Keqing provide separate governance, evaluation, and external-agent views.

<table>
  <tr>
    <td width="50%" align="center" valign="top">
      <a href="docs/assets/features/universes.jpg">
        <img src="docs/assets/features/universes.jpg" alt="Tianshu Universes experimental capability" width="100%">
      </a><br>
      <a href="docs/usage/feature-tour.en.md#universes-experimental"><b>Universes</b></a> · <code>Experimental</code><br>
      <sub>Uses universe lineages to isolate experimental branches, code candidates, evaluation, archival, and recovery.</sub>
    </td>
    <td width="50%" align="center" valign="top">
      <a href="docs/assets/features/evolution.jpg">
        <img src="docs/assets/features/evolution.jpg" alt="Tianshu Evolution experimental capability" width="100%">
      </a><br>
      <a href="docs/usage/feature-tour.en.md#evolution-experimental"><b>Evolution</b></a> · <code>Experimental</code><br>
      <sub>Provides read-only visibility into skill candidates, evidence gates, canary routing, promotion, and rollback state.</sub>
    </td>
  </tr>
  <tr>
    <td width="50%" align="center" valign="top">
      <a href="docs/assets/features/evals.jpg">
        <img src="docs/assets/features/evals.jpg" alt="Tianshu Evals beta capability" width="100%">
      </a><br>
      <a href="docs/usage/feature-tour.en.md#evaluations-trial"><b>Evals</b></a> · <code>Beta</code><br>
      <sub>Manage evaluation sets and compare scores, success rates, failure distribution, and historical deltas.</sub>
    </td>
    <td width="50%" align="center" valign="top">
      <a href="docs/assets/features/keqing.jpg">
        <img src="docs/assets/features/keqing.jpg" alt="Tianshu Keqing experimental capability" width="100%">
      </a><br>
      <a href="docs/usage/feature-tour.en.md#guest-agents-experimental"><b>Keqing</b></a> · <code>Experimental</code><br>
      <sub>Probes the current environment for Pi and other external coding agents—their versions, capabilities, readiness, and governance state.</sub>
    </td>
  </tr>
</table>

### Administration and cost

<table>
  <tr>
    <td width="50%" align="center" valign="top">
      <a href="docs/assets/features/system.jpg"><img src="docs/assets/features/system.jpg" alt="Tianshu System management" width="49%"></a>
      <a href="docs/assets/features/session-rules.jpg"><img src="docs/assets/features/session-rules.jpg" alt="Tianshu Session Rules" width="49%"></a><br>
      <a href="docs/usage/feature-tour.en.md#system-and-session-rules"><b>System &amp; Session Rules</b></a> · <code>Available · Administration</code><br>
      <sub>Centralizes models, tools, skills, plugins, and credentials while keeping reusable session authorization rules explicit.</sub>
    </td>
    <td width="50%" align="center" valign="top">
      <a href="docs/assets/features/cost.jpg">
        <img src="docs/assets/features/cost.jpg" alt="Tianshu Finance and budgets" width="100%">
      </a><br>
      <a href="docs/usage/feature-tour.en.md#cost-and-budgets"><b>Finance</b></a> · <code>Available · Bounded</code><br>
      <sub>Tracks token and provider costs, cache usage, budgets, and pricing assumptions.</sub>
    </td>
  </tr>
</table>

## Architecture overview

Tianshu organizes each task as one auditable core loop: Edict → Scheduler → Planner → execution
(Agent/DAG/Outer Loop) → Auditor → Notifier → growth through Memory/Profile/Skill. Edicts can be
issued from the Web UI, API, CLI, Feishu, or Telegram, and the system turns each goal into a
schedulable, auditable, decidable, and replayable execution chain. Long-running work does not rely
on a single terminal LLM output; an outer loop repeats actor → checks → critic → completion audit
until acceptance passes or the budget is exhausted. See the
[documentation guide](docs/README.md) for the architecture, domain model, and per-subsystem
design docs.

## Capability maturity

Tianshu composes a locally re-verifiable chain from Edict through Decision, run, Evidence, skill
candidate, evidence-bound gate, canary assignment, and rollback. The current code and retained
phase evidence cover the following capabilities:

- **Governance:** managed Native runs use durable Decision and RunState authority, attempt
  leases/fencing, and declared effect intent/receipt records inside the documented boundary.
- **Verification:** the SystemAudit hash chain, content-addressed ArtifactStore, and Evidence
  Bundle v1 bind behavior, decisions, artifacts, and limits. The strict verifier recomputes hashes
  and checks source/exact-Wheel provenance.
- **Lean evolution:** a skill candidate reaches a real candidate overlay only after its evidence
  gate, and rollback closes new candidate traffic; the full self-evolution loop remains
  experimental.
- **Desktop product:** the default navigation has six top-level destinations—Control Center
  (中枢), Task Workspace (御书房), Collaboration (朝堂), Operations (百司), Frontier Lab
  [Experimental] (天工院〔实验〕), and Administration (内府). The Task Workspace contains All Edicts, New Task,
  Scheduler, and Audit; Collaboration contains Personas, Consultation, and Planning; Operations
  contains Knowledge, External, and Notifications. Frontier Lab keeps Evolution, Universes, and
  Keqing marked Experimental and Evals marked Beta (`试行` in the classic Chinese locale).
  Administration retains System, Session Rules, and Finance. The Task Workspace defaults to every
  unarchived task visible to the current principal, uses overlapping tags for immediate, scheduled,
  long-running, conversational, and Keqing tasks, and shows progress derived from the latest
  execution facts. The legacy `/edicts` URL redirects to the workspace. Four “Unique Capabilities”
  cards present long-running governance, Evolution, Universes, and Keqing in the Control Center;
  Evolution renders the authoritative backend `evolution_status` instead of mock product data.

Each page and its primary interactions have been walked through and repaired locally; see the
[Web functional validation and repair report](docs/launch/web-functional-validation-2026-07-31.md)
for the clicked paths and remaining boundaries. The complete historical verification runs and
their immutable evidence are archived under [docs/cc-fable-v1/](docs/cc-fable-v1/).
Per-capability maturity conclusions are in the
[capability matrix](docs/launch/capability-matrix.md) and the
[current implementation](docs/CURRENT-STATE.md).

## Supported boundary

- The first official target is **Ubuntu + Python 3.12**, serving local desktop Web only; no
  mobile product commitment is made.
- The retained exact-Wheel batch was verified locally on `Darwin/arm64/Python 3.12.12`; it is not
  evidence that the Ubuntu external validation has already run.
- Persistence is single-host, single-node SQLite. A host administrator can read the database,
  master key, process memory, and local artifacts and is outside this threat boundary.
- The official local installation paths are a source checkout and an exact Wheel built from that
  checkout. An official container, PyPI, GHCR, signing, and release provenance are `deferred`.
- Persisted MCP env/header mappings are ciphertext. remote MCP and open stdio MCP remain
  `disabled` in the current support surface; their full admission work is `deferred`.
- managed OpenHands, executor compatibility, ROI, and cost calibration are `external_pending`;
  the fuller automated evolution gating is `deferred`.

## Install and verify locally

```bash
git clone https://github.com/MJ-CJM/tianshu.git
cd tianshu
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env   # edit .env and set TIANSHU_LLM_API_KEY
cd web && npm install && npm run build && cd ..
tianshu serve
```

Then open http://127.0.0.1:8000 for the Web UI. Building the frontend requires Node.js >= 20.
For day-to-day development, prefer the one-command script `./scripts/local.sh start --dev`
(hot reload + managed processes). See the
[getting started guide](docs/usage/getting-started.en.md) for the development mode,
environment variables, and deployment notes. For the strict re-verification path (exact-Wheel
installation, the golden demo, and provenance verification), follow the
[Lean Developer Preview guide](docs/usage/lean-developer-preview.md).

## Evidence states

Public documentation keeps these states distinct: `implemented`, `disabled`, `deferred`,
`experimental`, and `external_pending`. Start with the
[current implementation](docs/CURRENT-STATE.md), then see the
[capability matrix](docs/launch/capability-matrix.md) for each capability's default, supported
scope, verified guarantee, explicit non-guarantees, and evidence. Recovery conditions for deferred
work are in the [deferred roadmap](docs/cc-fable-v1/06-deferred-work-backlog.md).

## Brand and desktop shell

- **Brand asset**: the production desktop Web uses
  [`web/public/brand.png`](web/public/brand.png), whose SHA-256 is
  `3f2bb6cfdcac70092fce3a9b8b534c4a0627f444cb9db38a9651087688ace799`.
- **Motto**: “成功只有一个——按照自己的方式，去度过人生。”
- **Five right-side labels**: “彩蛋 / 通用 / English / 实时 / 通政”.
- **Top-level navigation**: Control Center, Task Workspace, Collaboration, Operations,
  Frontier Lab, and Administration (中枢、御书房、朝堂、百司、天工院〔实验〕、内府 in the
  classic Chinese locale).
- **Second-level structure**:
  - Task Workspace — All Edicts, New Task, Scheduler, Audit;
  - Collaboration — Personas, Consultation, Planning;
  - Operations — Knowledge, External, Notifications;
  - Frontier Lab — Evolution, Universes, Evals, Keqing (Evolution, Universes, and Keqing
    are labelled Experimental; Evals is Beta, `试行` in the classic Chinese locale);
  - Administration — System, Session Rules, Finance.
- **Task entry**: the Task Workspace combines all tasks, current progress, and items
  needing human intervention; `/edicts` remains only as a compatibility redirect.

This product structure is implemented in the current version. The original
[approval proposal](docs/launch/final-approval-proposal.md) remains a decision-process record;
[Current implementation](docs/CURRENT-STATE.md) is authoritative for present status.

## Contributing, security, and license

Read [CONTRIBUTING.md](CONTRIBUTING.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) before
participating. See [SECURITY.md](SECURITY.md) for vulnerability reporting and the
single-node/host-administrator boundary. The project is licensed under [MIT](LICENSE);
[third-party notices](THIRD_PARTY_NOTICES.md) cover included or adapted upstream material.
