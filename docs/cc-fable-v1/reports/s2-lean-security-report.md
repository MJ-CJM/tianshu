# S2 Lean Security Gate Report

- status: passed
- scope: S2 Lean security boundary only
- entry_commit: `8c2303df525b05a69d1a6902c83b06c5fd50102d`
- source_commit: `bbf672e560ecd2c793a1a80d0cc262b41550a4db`
gate_evidence_window_utc: `2026-07-14T09:27:19Z` to `2026-07-14T09:45:02Z`

This Gate closes the reduced S2 Lean boundary: local tamper-evident SystemAudit,
transaction-coupled security events, encrypted persisted MCP mappings and all-family key
rotation, plus fail-closed Lean MCP admission. It does **not** declare full G1.6 or full MCP
security passed. The public boundary is the
[Lean threat model](../../security/lean-preview-threat-model.md).

## Gate history

All three runs used:

```text
env -u VIRTUAL_ENV .venv/bin/python -m pytest -m "not slow" -q
```

The failed attempts are retained as Gate evidence rather than rewritten as passes.

| Attempt | Source state | Exact result | Disposition |
| --- | --- | --- | --- |
| Attempt 1 | before health-fixture amendment | `4 failed, 966 passed, 1 skipped, 24 deselected, 8 warnings in 278.76s` | Stopped on the four stale MCP health fixtures. Fixture-only fix committed as `6a76f55`; production health/admission behavior was unchanged. |
| Attempt 2 | after `6a76f55` | `10 failed, 2231 passed, 2 skipped, 24 deselected, 18 warnings in 754.24s` | Stopped on nine stale v6-as-live-tail assertions and one unadmitted MCP create fixture. Test-only fix committed as `bbf672e`; production migrations and admission were unchanged. |
| Attempt 3 | `bbf672e560ecd2c793a1a80d0cc262b41550a4db` | `2925 passed, 2 skipped, 24 deselected, 20 warnings in 751.37s (0:12:31)` | Passed. |

## Final Gate commands

Focused security Gate:

```text
env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/storage/test_system_audit.py tests/gateway/test_system_audit_api.py tests/security/test_system_audit_transactions.py tests/secrets/test_mcp_secret_migration.py tests/security/test_mcp_lean_admission.py tests/gateway/test_mcp_admin.py tests/cli/test_secrets_rotate.py tests/test_public_docs_truth.py -q
106 passed, 4 warnings in 19.73s
```

No focused test was skipped or deselected. The required security tests all executed.

Static Gate:

| Command | Exact result |
| --- | --- |
| `.venv/bin/ruff check src tests` | `All checks passed!` |
| `.venv/bin/ruff format --check src tests` | `709 files already formatted` |
| `.venv/bin/mypy` | `Success: no issues found in 110 source files` |
| `.venv/bin/lint-imports` | `415 files / 1298 dependencies; 2 contracts kept, 0 broken` |

## Live migration evidence

A fresh temporary database applied the live immutable ledger below. The live tail is v8,
not v6 or v7.

| Version | Name | Runtime checksum |
| ---: | --- | --- |
| 1 | `0001_adopt_v042_baseline` | `9672603c12dd858ea714b291d6ed94f1a27cb373bfcff97665b6316b4aa552a6` |
| 2 | `0002_auth_tokens` | `a2bbf753e0c3244fccc86be2d4588af2c926399f6dfa0dba0af5d0c060179c5a` |
| 3 | `0003_governance_contracts` | `07cb59c354035674fbcabcf1a037b4b273ae43b4e1e4dd8427cf90361bff2ff8` |
| 4 | `0004_workspace_foundation` | `1c0a028e0ea16475b9de5eb0c843f81aa275ddf62c0aca3c067bf8408dd9bee5` |
| 5 | `0005_governed_apply_bindings` | `c73294984096ea15e32d6ce80294f82323408cda12e82efea645ad8f35c5abc6` |
| 6 | `0006_seed_default_personas` | `596e672919bbe16b111fe3793e183b17666c7c5cad588d5532d7b2875501fca1` |
| 7 | `0007_system_audit_events` | `b24d3152f2b5aaa2d7dbf5776a5c865d336e025e861f8ca110e8be0c6a42e10b` |
| 8 | `0008_encrypt_mcp_secret_mappings` | `f03ad9148472267b754f6e4f1f03cefc947795c2a6717e0b89206b38244706ad` |

Runtime source fingerprints:

| Frozen behavior | SHA-256 fingerprint |
| --- | --- |
| v7 callback `_system_audit_upgrade` | `d46142290fbe10412291c6f0d3b73d6c83835c0a0247cb43bcd599d504afb070` |
| v8 callback `_mcp_secret_mapping_upgrade` | `c0576edfc0637532b1488aae740e658f122e837b2155648f0dabf351671bd3a5` |
| `migrations._parse_legacy_secret_mapping` | `6c29b81b3339a2c85c85ac628e2196d1074d4d338e31c9c446fb89341918429` |
| `migrations._encrypt_verified_mapping` | `6e70862523c3614fe3d701976e0b9240117fdf02d8ce41f9181d1d162d9ef291` |
| `vault.encrypt_canonical_mapping` | `7d44ac2228b6f041aaa5ece476301180b4d166c5732873b07fb3e75e4a6a9bc0` |
| `vault.decrypt_canonical_mapping` | `c752b957ea17b7936ccc2818b3356eb8e73ce8646cd429d0b6519f74cca09f09` |
| `vault.require_mcp_vault` | `0336ca958f13df4125d97ca2a3cc5b248ad0b571d17164e5548cbc767330f236` |
| `storage_base._truncate_sensitive_migration_wal` | `ed0fa42bcad3116fc412702e3da6b93d743352875d1a219d933e92529746b71c` |

## Run-specific SystemAudit and ciphertext evidence

Command:

```text
env -u VIRTUAL_ENV .venv/bin/python - <<'PY'
# Create TemporaryDirectory; generate a per-run Fernet key; initialize public Storage;
# use Storage.upsert_mcp_override_with_audit(...) and Storage.append_system_audit(...);
# query the live migration ledger/ciphertext columns; verify/export the public audit chain;
# scan the temporary DB/WAL/SHM for synthetic sentinels; print JSON; remove the directory.
PY
```

The accepted run used
`/var/folders/fd/5plhpn4543xbt5dj4xk7s5lw0000gn/T/tianshu-s2-task7-wruzfhp6/system-audit.sqlite3`.
It was a fresh run-specific path and `temporary_directory_removed=true`; no database or key
is committed.

- Legal safe actions appended through public `Storage` methods:
  `mcp.config.created`, then `mcp.admission.denied`.
- Chain result: `verified=true`, `reason_code=verified`, `event_count=2`, sequences `1..2`.
- Run-specific terminal hash:
  `a0b9804cb51ff5764a74578c4ba446b79498869f1a59774bc2944ed3c90b3959`.
- Synthetic sentinels: `S2_TASK7_SECRET_SENTINEL_7C92F5` and
  `S2_TASK7_HEADER_SENTINEL_1AD843`.
- Public decrypted readback matched both synthetic mappings, while both sentinels were absent
  from the DB/WAL/SHM bytes and the SystemAudit export.
- `env_json` and `headers_json` were absent; `env_ciphertext` and `headers_ciphertext` were
  present. Key-name metadata was exactly `["TOKEN"]` and `["Authorization"]`.
- Ciphertext SHA-256 values were
  `ea38efcaf59684c08e2ee5287609390c5b118297918410ce4daa716ff28f302a`
  (env) and
  `47343cc71bd0efe02e90ee3ea72fb0d94b9476e94e137839e8aedde1588e64d0`
  (headers). These hashes are run-specific because Fernet encryption is randomized.

## Lean disabled-path matrix

The same runtime probe called the immutable public `admission_for` decision API.

| Configuration | Allowed | Exact reason code |
| --- | --- | --- |
| explicit `enabled=false` | no | `disabled` |
| `secure-remote` + remote `streamable_http` | no | `trusted_egress_unavailable` |
| enabled stdio + empty `tools.include` | no | `approved_tools_required` |
| enabled approved-tools stdio outside configured server allowlist | no | `server_not_allowlisted` |
| enabled stdio + non-empty include + allowed server | yes | `admitted` |

Denied configurations construct no admitted session/tool surface in the focused regression.
The MCP YAML example keeps both servers disabled; its stdio example retains a non-empty include,
and its remote example documents no secure enablement path.

## Independent review and Critical/Important closure

Independent/adversarial review findings were consumed in separate fix commits and rechecked by
the final focused/full Gates:

| Area | Fixed Critical/Important findings | Fix commit(s) |
| --- | --- | --- |
| SystemAudit immutability/read integrity | `INSERT OR REPLACE` bypass, deep-page prefix validation gap, raw metadata coercion | `0fc5663` |
| Audited security transitions | false-success repeated revocation, target-owner actor fallback, estop cache/DB race | `b74ca96` |
| v8 migration secrecy | busy legacy WAL accepted, unfrozen security helpers, cleanup marker ignored on later startup | `39f2081`, `4da4896` |
| Master-key rotation | nullable business-key omission, stale per-row plan, equivalent-key bypass, new ciphertext target after backup | `856cb2e`, `08459bf` |
| MCP fail-closed lifecycle | stale stdio gateway command after reload, then revocation delayed behind session shutdown | `51ca6d3`, `f907da9` |
| Task 7 Gate integrity | stale health fixtures; stale v6 tail assertions; unadmitted/non-hermetic MCP fixture | `6a76f55`, `bbf672e` |

The two Task 7 amendments received independent read-only `Approved` / `Ready to merge: Yes`
verdicts with test-only diffs and no production behavior changes. Earlier S2 security/spec review
fixes were validated with real SQLite, real gateway/admission, failure injection, concurrency,
sentinel and source-fingerprint probes rather than mock-only assertions.

**Unresolved Critical findings: 0. Unresolved Important findings: 0.**

## Known limits and deferred work

- SystemAudit is a tamper-evident chain inside single-node SQLite. It is not external WORM
  storage and cannot detect a privileged attacker replacing both the database and trust root.
- Remote MCP stays disabled in `secure-remote`; SSRF, redirect/proxy policy, DNS pinning and
  resolution drift are not proven. Reopening it is [P2-A1](../06-deferred-work-backlog.md#p2-a1-remote-mcp-公开安全s24).
- Lean stdio admission requires a non-empty `tools.include`, but persistent exact grants,
  executable identity/digest binding, revocation and discovered-tool drift approval remain
  deferred to [P2-A2](../06-deferred-work-backlog.md#p2-a2-stdio-mcp-准入与漂移绑定s25).
- The current official install paths are source checkout and an exact Wheel built from that
  checkout. An official container remains [P2-A3](../06-deferred-work-backlog.md#p2-a3-官方-exact-wheel-容器s26s67-部分); PyPI/GHCR publication, SBOM/provenance and signing remain [P2-A4](../06-deferred-work-backlog.md#p2-a4-供应链与发行安全基线s27s68-部分).
- A failed v8 plaintext migration may retain exactly one mode-`0600`
  `legacy-sensitive` recovery backup. It still contains legacy plaintext and requires manual
  protection and cleanup.
- Host compromise, master-key disclosure and plaintext in process memory are outside this
  proven boundary.

The final full run's 20 warnings are retained maintenance signals: four third-party
`lark_oapi`/`websockets` deprecations, two unawaited `AsyncMock` warnings, twelve unawaited
`OpenAIChatCompletion.acompletion` warnings, and two subprocess-transport
`PytestUnraisableExceptionWarning` warnings. They did not fail the Gate and were not suppressed.

## Exit

S2 Lean Security is passed at the boundary above. The next stage is **S3 Core Governance**.
