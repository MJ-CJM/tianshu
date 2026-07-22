# Lean governed-evolution golden scenario

This is the single fixed Lean Developer Preview scenario. The runner performs the canonical
13-step sequence through loopback public HTTP endpoints only. After installation it imports no
Tianshu package modules; its runtime dependencies are the Python standard library and the public
server contract.

The committed provenance values are deliberately fixture placeholders. A qualifying exact-Wheel
run must export independently measured values before invoking the runner:

```bash
export TIANSHU_BOOTSTRAP_TOKEN="..."
export TIANSHU_LEAN_SOURCE_COMMIT="$(git rev-parse HEAD)"
export TIANSHU_LEAN_WHEEL_SHA256="$(shasum -a 256 /absolute/path/to/tianshu.whl | awk '{print $1}')"
export TIANSHU_LEAN_ENVIRONMENT_FINGERPRINT="<canonical environment digest>"
export TIANSHU_LEAN_FIXTURE=false

tianshu-lean-demo \
  --base-url http://127.0.0.1:7998 \
  --scenario examples/lean-governed-evolution/scenario.json \
  --batch-id "$BATCH_ID" \
  --output-root docs/cc-fable-v1/evidence/lean-preview
```

The base URL is restricted to explicit loopback HTTP. Polling is bounded by the scenario. The raw
request trail records only method, relative public path, and the canonical request-body SHA-256; it
does not retain authorization, decision reasons, promotion reasons, or skill content. Canary and
rollback observations additionally retain non-secret action, expected-version, batch-derived
idempotency-key, and request-hash bindings. The authenticated public principal plus those keys let
the verifier recompute each completed receipt's deterministic journal ID. There is no public
journal-entry read endpoint, so the verifier does not claim to inspect or verify journal entry
bodies. Server response hashes and correlation IDs remain available for audit.

Verify the retained batch against the independently measured commit and exact Wheel:

```bash
python scripts/verify_lean_preview_evidence.py \
  --report "docs/cc-fable-v1/evidence/lean-preview/$BATCH_ID/demo-report.json" \
  --artifact-root "docs/cc-fable-v1/evidence/lean-preview/$BATCH_ID/artifacts" \
  --expected-source-commit "$TIANSHU_LEAN_SOURCE_COMMIT" \
  --expected-wheel-sha256 "$TIANSHU_LEAN_WHEEL_SHA256"
```

Batch directories are immutable. A failed step writes a `failed` result, marks the unexecuted
steps `blocked`, retains the artifacts already collected, and exits non-zero. Corrected reruns must
use a new batch ID.
