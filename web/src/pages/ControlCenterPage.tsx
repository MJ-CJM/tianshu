import { Tag, Typography } from "antd";
import { Link } from "react-router-dom";

import type {
  ControlCenterSnapshotV1,
  ControlDecisionSummaryV1,
  ControlEvidenceSummaryV1,
  ControlRunSummaryV1,
} from "../api/control";
import PageContainer from "../components/common/PageContainer";
import PageDataState from "../components/states/PageDataState";
import { useLocaleMode } from "../hooks/useLocale";
import { useControlCenter } from "../hooks/useControlCenter";
import { useT } from "../i18n";

const sectionStyle = {
  border: "1px solid var(--ts-color-border)",
  borderRadius: 8,
  padding: 16,
  background: "var(--ts-color-surface)",
} as const;

const listStyle = {
  listStyle: "none",
  padding: 0,
  margin: "12px 0 0",
  display: "grid",
  gap: 10,
} as const;

const rowStyle = {
  borderTop: "1px solid var(--ts-color-border)",
  paddingTop: 10,
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 16,
} as const;

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <article style={sectionStyle}>
      <Typography.Text type="secondary">{label}</Typography.Text>
      <Typography.Title level={2} style={{ margin: "8px 0 0" }}>
        {value}
      </Typography.Title>
    </article>
  );
}

function RunRow({ run }: { run: ControlRunSummaryV1 }) {
  const t = useT();
  return (
    <li style={rowStyle}>
      <div>
        <Typography.Text strong>{run.edict_title}</Typography.Text>
        <br />
        <Typography.Text type="secondary">
          {run.phase} · {run.memorial_id}
        </Typography.Text>
      </div>
      <Link to={`/edicts/${encodeURIComponent(run.edict_id)}`}>
        {t("page.controlCenter.viewEdict")}
      </Link>
    </li>
  );
}

function DecisionRow({ decision }: { decision: ControlDecisionSummaryV1 }) {
  const t = useT();
  const locale = useLocaleMode();
  const dateLocale = locale === "en" ? "en-US" : "zh-CN";
  return (
    <li style={rowStyle}>
      <div>
        <Typography.Text strong>{decision.edict_title}</Typography.Text>
        <br />
        <Typography.Text type="secondary">
          {decision.kind} · {t("page.controlCenter.deadline")} {" "}
          {new Date(decision.expires_at).toLocaleString(dateLocale)}
        </Typography.Text>
      </div>
      <Link to="/approvals">{t("page.controlCenter.viewDecision")}</Link>
    </li>
  );
}

function EvidenceRow({ evidence }: { evidence: ControlEvidenceSummaryV1 }) {
  const t = useT();
  const isClosed = evidence.status === "closed";
  return (
    <li style={rowStyle}>
      <div>
        <Typography.Text strong>{evidence.edict_title}</Typography.Text>
        <br />
        <Typography.Text type="secondary">
          {isClosed
            ? t("page.controlCenter.evidenceClosed")
            : t("page.controlCenter.evidenceOpen")}
          {evidence.content_hash ? ` · ${evidence.content_hash.slice(0, 12)}…` : ""}
        </Typography.Text>
      </div>
      {isClosed ? (
        <a
          href={`/api/evidence/${encodeURIComponent(evidence.bundle_id)}/download`}
          download
        >
          {t("page.controlCenter.downloadEvidence")}
        </a>
      ) : (
        <Typography.Text type="secondary">
          {t("page.controlCenter.evidenceAwaitingClose")}
        </Typography.Text>
      )}
    </li>
  );
}

function EmptyLine({ children }: { children: string }) {
  return (
    <Typography.Paragraph type="secondary" style={{ margin: "12px 0 0" }}>
      {children}
    </Typography.Paragraph>
  );
}

function SnapshotContent({ snapshot }: { snapshot: ControlCenterSnapshotV1 }) {
  const t = useT();
  const locale = useLocaleMode();
  const dateLocale = locale === "en" ? "en-US" : "zh-CN";
  const evolutionLabel =
    snapshot.evolution_status === "not_enabled"
      ? t("page.controlCenter.evolutionNotEnabled")
      : snapshot.evolution_status === "degraded"
        ? t("page.controlCenter.evolutionDegraded")
        : t("page.controlCenter.evolutionEnabled");
  return (
    <div style={{ display: "grid", gap: 16 }}>
      <section aria-labelledby="control-status-title" style={sectionStyle}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 16 }}>
          <div>
            <Typography.Title id="control-status-title" level={4} style={{ margin: 0 }}>
              {t("page.controlCenter.statusTitle")}
            </Typography.Title>
            <Typography.Text type="secondary">
              {t("page.controlCenter.lastUpdated")} {" "}
              {new Date(snapshot.generated_at).toLocaleString(dateLocale)}
            </Typography.Text>
          </div>
          <div>
            <Tag color={snapshot.readiness === "ready" ? "green" : "orange"}>
              {snapshot.readiness === "ready"
                ? t("page.controlCenter.readinessReady")
                : t("page.controlCenter.readinessDegraded")}
            </Tag>
            <Tag>{evolutionLabel}</Tag>
          </div>
        </div>
      </section>

      <section
        aria-label={t("page.controlCenter.realCounts")}
        style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 12 }}
      >
        <Metric
          label={t("page.controlCenter.activeRunsMetric")}
          value={snapshot.active_runs.length}
        />
        <Metric
          label={t("page.controlCenter.pendingDecisionsMetric")}
          value={snapshot.pending_decisions.length}
        />
        <Metric
          label={t("page.controlCenter.recentEvidenceMetric")}
          value={snapshot.recent_evidence.length}
        />
      </section>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "minmax(0, 2fr) minmax(280px, 1fr)",
          gap: 16,
        }}
      >
        <section aria-labelledby="active-runs-title" style={sectionStyle}>
          <Typography.Title id="active-runs-title" level={4} style={{ margin: 0 }}>
            {t("page.controlCenter.activeRunsTitle")}
          </Typography.Title>
          {snapshot.active_runs.length === 0 ? (
            <EmptyLine>{t("page.controlCenter.activeRunsEmpty")}</EmptyLine>
          ) : (
            <ul style={listStyle}>
              {snapshot.active_runs.map((run) => (
                <RunRow key={run.memorial_id} run={run} />
              ))}
            </ul>
          )}
        </section>

        <section aria-labelledby="pending-decisions-title" style={sectionStyle}>
          <Typography.Title id="pending-decisions-title" level={4} style={{ margin: 0 }}>
            {t("page.controlCenter.pendingDecisionsTitle")}
          </Typography.Title>
          {snapshot.pending_decisions.length === 0 ? (
            <EmptyLine>{t("page.controlCenter.pendingDecisionsEmpty")}</EmptyLine>
          ) : (
            <ul style={listStyle}>
              {snapshot.pending_decisions.map((decision) => (
                <DecisionRow key={decision.decision_request_id} decision={decision} />
              ))}
            </ul>
          )}
        </section>
      </div>

      <section aria-labelledby="recent-evidence-title" style={sectionStyle}>
        <Typography.Title id="recent-evidence-title" level={4} style={{ margin: 0 }}>
          {t("page.controlCenter.recentEvidenceTitle")}
        </Typography.Title>
        {snapshot.recent_evidence.length === 0 ? (
          <EmptyLine>{t("page.controlCenter.recentEvidenceEmpty")}</EmptyLine>
        ) : (
          <ul style={listStyle}>
            {snapshot.recent_evidence.map((evidence) => (
              <EvidenceRow key={evidence.bundle_id} evidence={evidence} />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

export default function ControlCenterPage() {
  const t = useT();
  const { data, status, problem, refetch } = useControlCenter();
  const content = data ? <SnapshotContent snapshot={data} /> : null;
  const hasAuthoritativeEmptySnapshot = status === "success-empty" && content !== null;

  return (
    <PageContainer title={t("nav.control")}>
      {hasAuthoritativeEmptySnapshot ? (
        content
      ) : (
        <PageDataState
          status={status}
          data={data}
          problem={problem}
          isEmpty={() => false}
          onRetry={refetch}
        >
          {(snapshot) => <SnapshotContent snapshot={snapshot} />}
        </PageDataState>
      )}
    </PageContainer>
  );
}
