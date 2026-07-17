import { Descriptions, Space, Tag, Typography } from "antd";

import type {
  DurableDecisionAction,
  DurableDecisionKind,
  EdictDetailSnapshotV1,
  GovernedReplaySource,
  ResolveEdictDecisionInput,
} from "../../api/edicts";
import { useT } from "../../i18n";
import GlowCard from "../common/GlowCard";
import EvidenceBundlePanel, {
  type EvidenceBundleView,
} from "../evidence/EvidenceBundlePanel";
import DecisionPanel, {
  type DecisionAction,
  type DecisionResolution,
} from "./DecisionPanel";
import GovernanceContractCard, {
  type GovernanceCapabilityView,
} from "./GovernanceContractCard";

interface EdictDurableGovernanceProps {
  detail: EdictDetailSnapshotV1;
  onResolve: (input: ResolveEdictDecisionInput) => Promise<DecisionResolution>;
  onReplay: (source: GovernedReplaySource) => Promise<string>;
  onConflict: () => void;
}

function record(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null
    ? value as Record<string, unknown>
    : {};
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function requestedExecutor(contract: Record<string, unknown>): string {
  const value = record(contract.executor).adapter_id;
  return typeof value === "string" ? value : "—";
}

function requestedModes(contract: Record<string, unknown>) {
  const capabilities = record(contract.capabilities);
  return {
    mandatory: new Set(strings(capabilities.mandatory)),
    advisory: new Set(strings(capabilities.advisory)),
  };
}

const TOOL_ACTIONS: readonly DecisionAction[] = ["approve", "reject", "guide"];
const OUTER_LOOP_ACTIONS: readonly DecisionAction[] = ["continue", "accept_as_is", "abort"];
const REVIEW_ACTIONS: readonly DecisionAction[] = ["approve", "reject"];

function actionsFor(kind: DurableDecisionKind): readonly DecisionAction[] {
  if (kind === "outer_loop") return OUTER_LOOP_ACTIONS;
  if (kind === "tool") return TOOL_ACTIONS;
  return REVIEW_ACTIONS;
}

function asDurableAction(action: DecisionAction): DurableDecisionAction {
  return action as DurableDecisionAction;
}

function evidenceView(
  evidence: EdictDetailSnapshotV1["evidence"][number],
  detail: EdictDetailSnapshotV1,
): EvidenceBundleView {
  const run = detail.runs.find(({ memorial_id }) => memorial_id === evidence.memorial_id);
  const contract = run?.effective_contract;
  const policies = contract
    ? [
        `network: ${String(record(contract.network).mode ?? "—")}`,
        `workspace: ${String(record(contract.workspace).staging_mode ?? "—")}`,
        `apply: ${String(record(contract.workspace).apply_mode ?? "—")}`,
        `review: ${String(record(contract.permissions).review_policy ?? "—")}`,
      ]
    : [];
  return {
    id: evidence.bundle_id,
    status: evidence.status,
    version: evidence.version,
    digest: evidence.content_hash,
    downloadUrl:
      evidence.status === "closed" && evidence.download_available
        ? `/api/evidence/${encodeURIComponent(evidence.bundle_id)}/download`
        : null,
    executor: {
      id: evidence.executor.adapter_id,
      displayName: evidence.executor.display_name,
      level: evidence.executor.level,
    },
    artifacts: evidence.artifacts.map((artifact) => ({
      digest: artifact.digest,
      mediaType: artifact.media_type,
      sizeBytes: artifact.size_bytes,
    })),
    checks: evidence.checks.map((check) => ({
      name: check.name,
      status: check.status,
      exitCode: check.exit_code,
    })),
    policies,
    cost: `${evidence.cost.currency} ${String(evidence.cost.actual_cost)}`,
    environment: [
      `Tianshu ${evidence.environment.tianshu_version}`,
      `Python ${evidence.environment.python_version}`,
      `${evidence.environment.platform}/${evidence.environment.architecture}`,
      `lock ${evidence.environment.dependency_lock_hash}`,
      `fingerprint ${evidence.environment.environment_fingerprint}`,
    ],
    auditor: {
      id: evidence.auditor.auditor_id,
      verdict: evidence.auditor.verdict,
      reason: evidence.auditor.reason,
    },
    missingMandatory: evidence.auditor.missing_evidence,
    replayAvailable: evidence.status === "closed",
  };
}

export default function EdictDurableGovernance({
  detail,
  onResolve,
  onReplay,
  onConflict,
}: EdictDurableGovernanceProps) {
  const t = useT();
  const latestRun = detail.runs[0] ?? null;
  const latestEvidence = latestRun
    ? detail.evidence.find(({ memorial_id }) => memorial_id === latestRun.memorial_id) ?? null
    : null;
  const requested = detail.edict.governance_contract ?? {};
  const requestedMode = requestedModes(requested);
  const effective = latestRun?.effective_contract ?? null;
  const capabilities: GovernanceCapabilityView[] = (effective?.effective_controls ?? []).map(
    (control) => ({
      id: control.capability,
      label: control.capability,
      requested: requestedMode.mandatory.has(control.capability)
        ? "mandatory"
        : requestedMode.advisory.has(control.capability)
          ? "advisory"
          : "unrequested",
      effective: control.state,
    }),
  );
  const mandatoryMismatches = capabilities
    .filter(({ requested: mode, effective: state }) => mode === "mandatory" && state !== "enforced")
    .map(({ id, effective: state }) => `${id}: ${state}`);

  const replaySource: GovernedReplaySource = {
    title: detail.edict.title,
    goal: detail.edict.goal,
    context: detail.edict.context,
    priority: detail.edict.priority,
    governanceContract: requested,
  };

  return (
    <section aria-label={t("page.edictDetail.durable.workspaceTitle")}>
      <GlowCard style={{ marginBottom: 24 }}>
        {effective ? (
          <GovernanceContractCard
            executorLevel={latestEvidence?.executor.level ?? null}
            requestedExecutor={requestedExecutor(requested)}
            effectiveExecutor={effective.executor.adapter_id}
            capabilities={capabilities}
            mandatoryMismatches={mandatoryMismatches}
            advisoryGaps={effective.unsupported_advisory}
          />
        ) : (
          <>
            <Typography.Title level={2}>{t("governanceUi.title")}</Typography.Title>
            <Typography.Text type="secondary">
              {t("page.edictDetail.durable.effectivePending")}
            </Typography.Text>
          </>
        )}
      </GlowCard>

      <GlowCard title={t("page.edictDetail.durable.runStateTitle")} style={{ marginBottom: 24 }}>
        {detail.runs.length === 0 ? (
          <Typography.Text type="secondary">{t("page.edictDetail.durable.runsEmpty")}</Typography.Text>
        ) : detail.runs.map((run) => (
          <Descriptions
            key={run.memorial_id}
            size="small"
            column={3}
            items={[
              { key: "memorial", label: t("page.edictDetail.durable.memorial"), children: <code>{run.memorial_id}</code> },
              { key: "phase", label: t("page.edictDetail.durable.phase"), children: <Tag>{run.phase}</Tag> },
              { key: "version", label: t("page.edictDetail.durable.version"), children: run.version },
              { key: "cursor", label: t("page.edictDetail.durable.sideEffectCursor"), children: run.side_effect_cursor },
              { key: "checkpoint", label: t("page.edictDetail.durable.checkpoint"), children: run.checkpoint_present ? t("page.edictDetail.durable.yes") : t("page.edictDetail.durable.no") },
              {
                key: "lineage",
                label: t("page.edictDetail.durable.planLineage"),
                children: run.plan_lineage.length > 0
                  ? <Space wrap>{run.plan_lineage.map((revision) => <code key={revision.revision_id}>{revision.revision_id}</code>)}</Space>
                  : "—",
              },
            ]}
          />
        ))}
      </GlowCard>

      <GlowCard title={t("page.edictDetail.durable.decisionsTitle")} style={{ marginBottom: 24 }}>
        {detail.decisions.length === 0 ? (
          <Typography.Text type="secondary">{t("page.edictDetail.durable.decisionsEmpty")}</Typography.Text>
        ) : detail.decisions.map((decision) => (
          <article key={decision.request.decision_request_id} style={{ marginBottom: 20 }}>
            <Space wrap>
              <code>{decision.request.decision_request_id}</code>
              <Tag>{decision.request.kind}</Tag>
              <Tag>v{decision.request.version}</Tag>
            </Space>
            {Object.keys(decision.request.payload).length > 0 ? (
              <dl>
                {Object.entries(decision.request.payload).map(([key, value]) => (
                  <div key={key}>
                    <dt>{key}</dt>
                    <dd>{typeof value === "string" || typeof value === "number" ? String(value) : JSON.stringify(value)}</dd>
                  </div>
                ))}
              </dl>
            ) : null}
            <DecisionPanel
              decision={{
                id: decision.request.decision_request_id,
                status: decision.request.status,
                version: decision.request.version,
                expiresAt: decision.request.expires_at,
                resolvedAction: decision.resolution?.action as DecisionAction | undefined,
                resolvedReason: decision.resolution?.reason,
                resolvedBy: decision.resolution
                  ? `${decision.resolution.actor_display_name} (${decision.resolution.actor_principal_id})`
                  : undefined,
                resolvedAt: decision.resolution?.resolved_at,
              }}
              actions={actionsFor(decision.request.kind)}
              onConflict={onConflict}
              onSubmit={(submission) => onResolve({
                decisionRequestId: submission.decisionRequestId,
                kind: decision.request.kind,
                action: asDurableAction(submission.action),
                reason: submission.reason,
                expectedVersion: submission.expectedVersion,
              })}
            />
          </article>
        ))}
      </GlowCard>

      <GlowCard title={t("page.edictDetail.durable.evidenceTitle")} style={{ marginBottom: 24 }}>
        {detail.evidence.length === 0 ? (
          <Typography.Text type="secondary">{t("page.edictDetail.durable.evidenceEmpty")}</Typography.Text>
        ) : detail.evidence.map((evidence) => (
          <EvidenceBundlePanel
            key={evidence.bundle_id}
            bundle={evidenceView(evidence, detail)}
            onReplay={() => void onReplay(replaySource)}
          />
        ))}
      </GlowCard>
    </section>
  );
}
