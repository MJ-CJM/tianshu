import { Tag, Typography } from "antd";

import type { EvolutionCenterSnapshotV1 } from "../api/evolution";
import {
  CapabilityBoundary,
  MaturityBadge,
} from "../components/capabilities/CapabilityMaturity";
import PageContainer from "../components/common/PageContainer";
import MonoText from "../components/common/MonoText";
import EvolutionGate from "../components/evolution/EvolutionGate";
import PageDataState from "../components/states/PageDataState";
import { useT } from "../i18n";
import {
  isEvolutionSnapshotEmpty,
  useEvolutionCenter,
} from "../hooks/useEvolutionCenter";

const panelStyle = {
  border: "1px solid var(--ts-color-border)",
  borderRadius: 8,
  padding: 16,
  background: "var(--ts-color-surface)",
} as const;

function DisabledSnapshot({ snapshot }: { snapshot: EvolutionCenterSnapshotV1 }) {
  const t = useT();
  return (
    <section role="status" aria-labelledby="evolution-disabled-title" style={panelStyle}>
      <Typography.Title id="evolution-disabled-title" level={4} style={{ margin: 0 }}>
        {t("page.evolutionCenter.notEnabledTitle")}
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ margin: "8px 0 0" }}>
        {t("page.evolutionCenter.notEnabledReason")}
      </Typography.Paragraph>
      <div style={{ marginTop: 12 }}>
        <Typography.Text type="secondary">{t("page.evolutionCenter.reasonCode")}: </Typography.Text>
        <MonoText>{snapshot.reason_code}</MonoText>
      </div>
    </section>
  );
}

function EnabledEmptySnapshot() {
  const t = useT();
  return (
    <section
      role="status"
      aria-labelledby="evolution-enabled-empty-title"
      style={panelStyle}
    >
      <Typography.Title
        id="evolution-enabled-empty-title"
        level={4}
        style={{ margin: 0 }}
      >
        {t("page.evolutionCenter.enabledEmptyTitle")}
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ margin: "8px 0 0" }}>
        {t("page.evolutionCenter.enabledEmptyDescription")}
      </Typography.Paragraph>
    </section>
  );
}

function SnapshotContent({ snapshot }: { snapshot: EvolutionCenterSnapshotV1 }) {
  const t = useT();
  if (snapshot.status === "not_enabled") return <DisabledSnapshot snapshot={snapshot} />;
  const routingByCandidate = new Map(
    snapshot.routing.map((item) => [item.candidate_id, item] as const),
  );
  return (
    <div style={{ display: "grid", gap: 16 }}>
      <section aria-labelledby="evolution-status-title" style={panelStyle}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 16 }}>
          <div>
            <Typography.Title id="evolution-status-title" level={4} style={{ margin: 0 }}>
              {t("page.evolutionCenter.statusTitle")}
            </Typography.Title>
            <Typography.Text type="secondary">{snapshot.reason_code}</Typography.Text>
          </div>
          <div>
            <Tag>Lean Core Gate</Tag>
            <Tag color={snapshot.status === "enabled" ? "green" : "orange"}>
              {snapshot.status === "enabled"
                ? t("page.evolutionCenter.enabled")
                : t("page.evolutionCenter.degraded")}
            </Tag>
          </div>
        </div>
        {snapshot.last_gate_hash ? (
          <div style={{ marginTop: 12 }}>
            <Typography.Text type="secondary">{t("page.evolutionCenter.lastGateHash")}: </Typography.Text>
            <MonoText>{snapshot.last_gate_hash}</MonoText>
          </div>
        ) : null}
      </section>

      <section aria-labelledby="evolution-candidates-title" style={{ display: "grid", gap: 12 }}>
        <Typography.Title id="evolution-candidates-title" level={4} style={{ margin: 0 }}>
          {t("page.evolutionCenter.candidatesTitle")}
        </Typography.Title>
        {snapshot.candidates.map((candidate) => (
          <EvolutionGate
            key={candidate.candidate_id}
            candidate={candidate}
            routing={routingByCandidate.get(candidate.candidate_id) ?? null}
          />
        ))}
      </section>
    </div>
  );
}

export default function EvolutionCenterPage() {
  const t = useT();
  const { data, status, problem, refetch } = useEvolutionCenter();
  const isEnabledEmpty = status === "success-empty" && data !== null;
  return (
    <PageContainer
      title={t("nav.evolution")}
      titleBadge={<MaturityBadge maturity="experimental" />}
    >
      <CapabilityBoundary
        maturity="experimental"
        canDo={t("page.evolutionCenter.capabilityCanDo")}
        boundary={t("page.evolutionCenter.capabilityBoundary")}
      />
      {isEnabledEmpty ? (
        <EnabledEmptySnapshot />
      ) : (
        <PageDataState
          status={status}
          data={data}
          problem={problem}
          isEmpty={isEvolutionSnapshotEmpty}
          onRetry={refetch}
        >
          {(snapshot) => <SnapshotContent snapshot={snapshot} />}
        </PageDataState>
      )}
    </PageContainer>
  );
}
