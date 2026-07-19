# Lean Developer Preview security threat model / 安全威胁模型

This document is the public security boundary for the S2 Lean Developer Preview. It is
not a declaration that full G1.6 has passed.

## Status language

- **Proven**: implemented behavior covered by repository tests in the named boundary.
- **Disabled by design**: the path is rejected or remains off in the stated mode.
- **Deferred**: not an available security promise; reopening it requires the linked backlog.
- **Not proven**: no current evidence supports the claim, even if adjacent code exists.

## Supported installation and deployment boundary

The **source checkout and exact Wheel are the current official installation paths**. The
Wheel means the artifact built and tested from that same checkout; this statement does not
mean that a public package registry has been populated.

The current runtime is trusted-local or explicitly configured secure-remote on one host,
with **single-node SQLite** persistence. Multi-node, multi-writer, PostgreSQL, Kubernetes,
and replica failover behavior are outside the proven boundary.

## S2 Lean control status

| Control | Status | Current evidence and boundary |
| --- | --- | --- |
| System audit | **Proven** | **SystemAudit tamper-evident chain: implemented** with canonical hashes, previous-hash linkage, append-only SQLite triggers, full-chain verification, scoped admin read/export, and transaction-coupled security mutations. This is not external WORM storage and does not survive a privileged attacker replacing the database and its trust root. See [`test_system_audit.py`](../../tests/storage/test_system_audit.py), [`test_system_audit_api.py`](../../tests/gateway/test_system_audit_api.py), and [`test_system_audit_transactions.py`](../../tests/security/test_system_audit_transactions.py). |
| Persisted MCP mappings | **Proven** | **MCP persisted secret mappings at rest: implemented as ciphertext**. Migration v8 removes the legacy plaintext columns, verifies encrypted round trips before schema mutation, and fails closed when the vault is unavailable or ciphertext cannot be decrypted. See [`test_mcp_secret_migration.py`](../../tests/secrets/test_mcp_secret_migration.py). |
| Master-key rotation | **Proven** | Network, channel, and MCP ciphertext families are validated, backed up once, re-encrypted, and audited in one local transaction; concurrent snapshot drift fails closed. See [`test_secrets_rotate.py`](../../tests/cli/test_secrets_rotate.py). |
| Remote MCP in secure-remote | **Disabled by design** / **Deferred** | **secure-remote remote MCP: disabled and deferred** because a trusted egress boundary is unavailable. This is only a denial boundary, not proof of remote MCP security. See [`test_mcp_lean_admission.py`](../../tests/security/test_mcp_lean_admission.py) and [P2-A1](../cc-fable-v1/06-deferred-work-backlog.md#p2-a1-remote-mcp-公开安全s24). |
| stdio MCP Lean admission | **Proven** for the narrow rule; stronger binding **Deferred** | An enabled stdio server is admitted only with an explicit non-empty tool allowlist: the **current Lean stdio boundary is an explicit non-empty `tools.include` allowlist**. **stdio persistent exact grant and executable binding: deferred**; command, argv, environment, working directory, executable realpath/digest, actor/reason/expiry, revocation, and discovered-tool drift are not persistently bound. See [P2-A2](../cc-fable-v1/06-deferred-work-backlog.md#p2-a2-stdio-mcp-准入与漂移绑定s25). |
| Public artifacts | **Deferred** | **container images, PyPI, GHCR, and artifact signing: deferred**. There is no official container, registry publication, SBOM/provenance promise, or signed artifact in this Gate. See [P2-A3](../cc-fable-v1/06-deferred-work-backlog.md#p2-a3-官方-exact-wheel-容器s26s67-部分) and [P2-A4](../cc-fable-v1/06-deferred-work-backlog.md#p2-a4-供应链与发行安全基线s27s68-部分). |

## Threats and current response

| Threat | Current response | Status |
| --- | --- | --- |
| Audit row update, delete, replace, chain gap, or earlier-prefix corruption | SQLite triggers reject ordinary mutation; reads and exports verify the chain from genesis. | **Proven** inside the local database/process boundary. |
| Secret values copied into SystemAudit | Action-specific metadata keys and primitive types fail closed; focused tests scan API, database, and WAL sentinels. | **Proven** for covered audit append paths. |
| Plaintext MCP env/header mappings in the live v8 schema | Fernet ciphertext plus key-name metadata replaces legacy plaintext columns; wrong/missing keys fail closed. | **Proven** for persisted MCP overrides. |
| Remote MCP SSRF, redirects, proxy inheritance, DNS rebinding, or resolution drift | Remote MCP is rejected in secure-remote. | **Disabled by design**; full SSRF defense and DNS pinning are **Not proven**. |
| PATH replacement, executable drift, argument/environment drift, or newly discovered stdio tools | Non-empty `tools.include` narrows registration and the runtime gateway receives admitted command snapshots. | Persistent exact binding and drift approval are **Deferred** and **Not proven**. |
| Host compromise, master-key disclosure, plaintext in process memory, swap, backups, or external logs | Operational key separation and local file permissions reduce exposure. | Resistance to a privileged host attacker is **Not proven**. |
| Distributed durability, consensus, replica recovery, or cross-node audit ordering | No mitigation is claimed. | **Not proven**; this release is single-node SQLite only. |

## Legacy plaintext migration recovery warning

If the v8 plaintext-to-ciphertext migration fails, it may retain **exactly one mode `0600`
`legacy-sensitive` recovery backup** beside the database. The backup intentionally preserves
the recoverable legacy plaintext and is therefore sensitive even though its mode is `0600`.
Use the path attached to the migration error, keep it off shared or synchronized storage,
**protect it from disclosure and remove it manually after recovery**. A later successful
startup removes the deterministic recovery backup, but operators must not rely on that as
the only cleanup procedure.

## Explicit non-claims

This Gate does not prove full G1.6, full SSRF protection, DNS pinning, remote MCP security,
container hardening, PyPI or GHCR publication, SBOM/provenance, or artifact signing. It also
does not turn a local hash chain into an external immutable audit service.
