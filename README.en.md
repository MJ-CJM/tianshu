<div align="center">

<img src="web/public/brand.png" alt="Tianshu" width="128">

# Tianshu

**Tianshu is a governable, verifiable Agent OS designed to learn and evolve continuously.**

[中文](README.md) · [Current implementation](docs/CURRENT-STATE.md) · [Lean Preview guide](docs/usage/lean-developer-preview.md) · [Capability matrix](docs/launch/capability-matrix.md)

</div>

## Lean Developer Preview Candidate

Tianshu is a governable, verifiable Agent OS designed to learn and evolve continuously. Its code
and retained phase evidence compose a local path from Edict through Decision, run, Evidence, skill
candidate, evidence-bound gate, canary assignment, and rollback.

> The previous Candidate JSON and aggregate report have been withdrawn fail-closed because they
> relied on composite summaries and did not bind tracked raw Gate logs and build provenance. No
> Candidate is currently accepted; a new final-source Gate, artifact provenance, and demo must be
> recorded before the strict checker can rebuild one.

- **Governance:** managed Native runs use durable Decision and RunState authority, attempt
  leases/fencing, and declared effect intent/receipt records inside the documented boundary.
- **Verification:** the SystemAudit hash chain, content-addressed ArtifactStore, and Evidence
  Bundle v1 bind behavior, decisions, artifacts, and limits. The strict verifier recomputes hashes
  and checks source/exact-Wheel provenance.
- **Lean evolution:** a skill candidate reaches a real candidate overlay only after its evidence
  gate, and rollback closes new candidate traffic. This is Lean Core evidence, not full G4.
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

The current product decision and release boundary remain separate:

- `design_status`: `approved`
- `implementation_status`: `verified_local`
- `visual_status`: `user_approval_pending`
- `publication_status`: `not_authorized`

The user approved the final six-destination structure, and it is implemented and verified locally.
The latest source has also completed an isolated Demo/Eval browser walkthrough of the pages and
their primary interactions, including repair-and-retest of issues found during that walkthrough.
The retained 48 visual baselines and hashes still cover the preceding six-route shell. The Task
Workspace expands the current matrix to seven routes and an expected 56 visual images, but those
images and hashes have not been regenerated. `visual_status` therefore remains
`user_approval_pending`; `verified_local` does not mean a new Candidate is accepted. See the
[Web functional validation and repair report](docs/launch/web-functional-validation-2026-07-31.md)
for the clicked paths, fixes, and remaining boundaries.

The historical retained golden batch passed all 13 steps and strict verification, but it is not
reused for a new Candidate. See the
[usage guide](docs/usage/lean-developer-preview.md) and its
[immutable report](docs/cc-fable-v1/evidence/lean-preview/20260719T083725Z-01da3844dde7/demo-report.json).

## Supported boundary

- The first official target is **Ubuntu + Python 3.12**, serving local desktop Web only.
- The retained exact-Wheel batch was verified locally on `Darwin/arm64/Python 3.12.12`; it is not
  evidence that the Ubuntu external validation has already run.
- Persistence is single-host, single-node SQLite. A host administrator can read the database,
  master key, process memory, and local artifacts and is outside this threat boundary.
- The official local installation paths are a source checkout and an exact Wheel built from that
  checkout. An official container, PyPI, GHCR, signing, and release provenance are `deferred`.
- Persisted MCP env/header mappings are ciphertext. remote MCP and open stdio MCP remain
  `disabled` in the Candidate support surface; their full admission work is `deferred`.
- managed OpenHands, executor compatibility, ROI, cost calibration, and full G4 are
  `external_pending`; full G5 is `deferred`.

`publication_status`: `not_authorized`. This private-branch Candidate is not permission to push,
tag, release, publish packages or images, make the repository public, or announce a final release.

## Install and verify locally

Follow the [Lean Developer Preview guide](docs/usage/lean-developer-preview.md) for source and
exact-Wheel installation, the one golden demo, and strict provenance verification. The legacy
Dockerfile is not an official distribution path for this Candidate.

## Evidence states

Public documentation keeps these states distinct: `implemented`, `disabled`, `deferred`,
`experimental`, `external_pending`, and `user_approval_pending`. Start with the
[current implementation](docs/CURRENT-STATE.md), then see the
[capability matrix](docs/launch/capability-matrix.md) for each capability's default, supported
scope, verified guarantee, explicit non-guarantees, and evidence. Recovery conditions for deferred
work are in the [deferred roadmap](docs/cc-fable-v1/06-deferred-work-backlog.md).

## Brand and desktop shell

The production desktop Web uses [`web/public/brand.png`](web/public/brand.png), whose SHA-256 is
`3f2bb6cfdcac70092fce3a9b8b534c4a0627f444cb9db38a9651087688ace799`. The frozen motto is
“成功只有一个——按照自己的方式，去度过人生。” and the five right-side labels are
“彩蛋 / 通用 / English / 实时 / 通政”. The left navigation has six top-level destinations:
Control Center, Task Workspace, Collaboration, Operations, Frontier Lab, and Administration. In
the classic Chinese locale these are 中枢、御书房、朝堂、百司、天工院〔实验〕、内府. Their children
map respectively to All Edicts / New Task / Scheduler / Audit; Personas / Consultation / Planning;
Knowledge / External / Notifications; Evolution / Universes / Evals / Keqing; and System / Session
Rules / Finance. Evolution, Universes, and Keqing are labelled Experimental; Evals is labelled
Beta (`试行` in the classic Chinese locale). The Task Workspace combines all tasks, current
progress, and items needing human intervention; `/edicts` remains only as a compatibility redirect.
This product structure is approved and implemented locally. The original
[approval proposal](docs/launch/final-approval-proposal.md) remains a decision-process record;
[Current implementation](docs/CURRENT-STATE.md) is authoritative for present status.

## Contributing, security, and license

Read [CONTRIBUTING.md](CONTRIBUTING.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) before
participating. See [SECURITY.md](SECURITY.md) for vulnerability reporting and the
single-node/host-administrator boundary. The project is licensed under [MIT](LICENSE);
[third-party notices](THIRD_PARTY_NOTICES.md) cover included or adapted upstream material.
