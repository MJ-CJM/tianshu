<div align="center">

<img src="web/public/brand.png" alt="Tianshu" width="128">

# Tianshu

**Tianshu is a governable, verifiable Agent OS designed to learn and evolve continuously.**

[中文](README.md) · [Lean Preview guide](docs/usage/lean-developer-preview.md) · [Capability matrix](docs/launch/capability-matrix.md)

</div>

## Lean Developer Preview Candidate

Tianshu is a governable, verifiable Agent OS designed to learn and evolve continuously. The
Candidate composes one local, reproducible path from Edict through Decision, run, Evidence, skill
candidate, evidence-bound gate, canary assignment, and rollback.

- **Governance:** managed Native runs use durable Decision and RunState authority, attempt
  leases/fencing, and declared effect intent/receipt records inside the documented boundary.
- **Verification:** the SystemAudit hash chain, content-addressed ArtifactStore, and Evidence
  Bundle v1 bind behavior, decisions, artifacts, and limits. The strict verifier recomputes hashes
  and checks source/exact-Wheel provenance.
- **Lean evolution:** a skill candidate reaches a real candidate overlay only after its evidence
  gate, and rollback closes new candidate traffic. This is Lean Core evidence, not full G4.
- **Desktop product:** Control Center, Edict detail, and Evolution Center consume authoritative
  APIs without mock product data. Automation passed; final visual/interaction approval remains
  `user_approval_pending`.

The retained golden batch passed all 13 steps and strict verification. See the
[usage guide](docs/usage/lean-developer-preview.md) and its
[immutable report](docs/cc-fable-v1/evidence/lean-preview/20260718T072917Z-b27f525fe4ef/demo-report.json).

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
`experimental`, `external_pending`, and `user_approval_pending`. See the
[capability matrix](docs/launch/capability-matrix.md) for each capability's default, supported
scope, verified guarantee, explicit non-guarantees, and evidence. Recovery conditions for deferred
work are in the [deferred roadmap](docs/cc-fable-v1/06-deferred-work-backlog.md).

## Brand and desktop shell

The production desktop Web uses [`web/public/brand.png`](web/public/brand.png), whose SHA-256 is
`3f2bb6cfdcac70092fce3a9b8b534c4a0627f444cb9db38a9651087688ace799`. The frozen motto is
“成功只有一个——按照自己的方式，去度过人生。” and the five right-side labels are
“彩蛋 / 通用 / English / 实时 / 通政”. The fourteen-department navigation remains, while this
Candidate makes deep product claims only for the three core pages.

## Contributing, security, and license

Read [CONTRIBUTING.md](CONTRIBUTING.md) before changing behavior. See [SECURITY.md](SECURITY.md)
for vulnerability reporting and the single-node/host-administrator boundary. The license is
[MIT](LICENSE).
