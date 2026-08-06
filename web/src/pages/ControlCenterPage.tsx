import { Tag, Typography } from "antd";
import { Link } from "react-router-dom";

import type {
  ControlCenterSnapshotV1,
  ControlDecisionSummaryV1,
  ControlEvidenceSummaryV1,
  ControlRunSummaryV1,
} from "../api/control";
import { CapabilitySummaryCard } from "../components/capabilities/CapabilityMaturity";
import PageContainer from "../components/common/PageContainer";
import PageDataState from "../components/states/PageDataState";
import { useLocaleMode } from "../hooks/useLocale";
import { useControlCenter } from "../hooks/useControlCenter";
import { useT } from "../i18n";
import styles from "./ControlCenterPage.module.css";

function Metric({
  detail,
  label,
  value,
}: {
  detail?: string;
  label: string;
  value: number;
}) {
  return (
    <article>
      <Typography.Text type="secondary">{label}</Typography.Text>
      <Typography.Title level={2}>{value}</Typography.Title>
      {detail ? (
        <Typography.Text type="secondary">{detail}</Typography.Text>
      ) : null}
    </article>
  );
}

function RunRow({ run }: { run: ControlRunSummaryV1 }) {
  const t = useT();
  return (
    <li>
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
    <li>
      <div>
        <Typography.Text strong>{decision.edict_title}</Typography.Text>
        <br />
        <Typography.Text type="secondary">
          {decision.kind} · {t("page.controlCenter.deadline")}{" "}
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
    <li>
      <div>
        <Typography.Text strong>{evidence.edict_title}</Typography.Text>
        <br />
        <Typography.Text type="secondary">
          {isClosed
            ? t("page.controlCenter.evidenceClosed")
            : t("page.controlCenter.evidenceOpen")}
          {evidence.content_hash
            ? ` · ${evidence.content_hash.slice(0, 12)}…`
            : ""}
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
    <Typography.Paragraph type="secondary">{children}</Typography.Paragraph>
  );
}

function SnapshotContent({ snapshot }: { snapshot: ControlCenterSnapshotV1 }) {
  const t = useT();
  const locale = useLocaleMode();
  const dateLocale = locale === "en" ? "en-US" : "zh-CN";
  const evolutionStatus =
    snapshot.evolution_status === "enabled"
      ? t("page.evolutionCenter.enabled")
      : snapshot.evolution_status === "degraded"
        ? t("page.evolutionCenter.degraded")
        : t("page.controlCenter.evolutionNotEnabled");
  return (
    <div className={styles.pageGrid}>
      <section aria-labelledby="control-status-title">
        <div>
          <div>
            <Typography.Title id="control-status-title" level={4}>
              {t("page.controlCenter.statusTitle")}
            </Typography.Title>
            <Typography.Text type="secondary">
              {t("page.controlCenter.lastUpdated")}{" "}
              {new Date(snapshot.generated_at).toLocaleString(dateLocale)}
            </Typography.Text>
          </div>
          <div>
            <Tag color={snapshot.readiness === "ready" ? "green" : "orange"}>
              {snapshot.readiness === "ready"
                ? t("page.controlCenter.readinessReady")
                : t("page.controlCenter.readinessDegraded")}
            </Tag>
          </div>
        </div>
      </section>

      <section
        aria-label={t("page.controlCenter.realCounts")}
        className={styles.metricsGrid}
      >
        <Metric
          label={t("page.controlCenter.activeRunsMetric")}
          value={snapshot.active_run_total}
        />
        <Metric
          detail={t("page.controlCenter.unarchivedEdictsBreakdown", {
            awaiting: snapshot.awaiting_follow_up_total,
            cancelled: snapshot.cancelled_edict_total,
          })}
          label={t("page.controlCenter.unarchivedEdictsMetric")}
          value={snapshot.unarchived_edict_total}
        />
        <Metric
          label={t("page.controlCenter.pendingDecisionsMetric")}
          value={snapshot.pending_decision_total}
        />
        <Metric
          label={t("page.controlCenter.evidenceMetric")}
          value={snapshot.evidence_total}
        />
      </section>

      <section aria-labelledby="unique-capabilities-title">
        <Typography.Title id="unique-capabilities-title" level={4}>
          {t("page.controlCenter.uniqueCapabilitiesTitle")}
        </Typography.Title>
        <Typography.Paragraph type="secondary">
          {t("page.controlCenter.uniqueCapabilitiesDescription")}
        </Typography.Paragraph>
        <div className={styles.capabilitiesGrid}>
          <CapabilitySummaryCard
            title={t("page.controlCenter.longGovernanceTitle")}
            maturity="stableLimited"
            status={t("page.controlCenter.longGovernanceStatus")}
            description={t("page.controlCenter.longGovernanceDescription")}
            to="/edicts/create"
          />
          <CapabilitySummaryCard
            title={t("page.controlCenter.evolutionCapabilityTitle")}
            maturity="experimental"
            status={evolutionStatus}
            description={t("page.controlCenter.evolutionCapabilityDescription")}
            to="/evolution"
          />
          <CapabilitySummaryCard
            title={t("page.controlCenter.universeCapabilityTitle")}
            maturity="experimental"
            status={t("page.controlCenter.universeCapabilityStatus")}
            description={t("page.controlCenter.universeCapabilityDescription")}
            to="/universes"
          />
          <CapabilitySummaryCard
            title={t("page.controlCenter.keqingCapabilityTitle")}
            maturity="experimental"
            status={t("page.controlCenter.keqingCapabilityStatus")}
            description={t("page.controlCenter.keqingCapabilityDescription")}
            to="/keqing"
          />
        </div>
      </section>

      <div className={styles.activityGrid}>
        <section aria-labelledby="active-runs-title">
          <Typography.Title id="active-runs-title" level={4}>
            {t("page.controlCenter.activeRunsTitle")}
          </Typography.Title>
          {snapshot.active_runs.length === 0 ? (
            <EmptyLine>{t("page.controlCenter.activeRunsEmpty")}</EmptyLine>
          ) : (
            <ul className={styles.list}>
              {snapshot.active_runs.map((run) => (
                <RunRow key={run.memorial_id} run={run} />
              ))}
            </ul>
          )}
        </section>

        <section aria-labelledby="pending-decisions-title">
          <Typography.Title id="pending-decisions-title" level={4}>
            {t("page.controlCenter.pendingDecisionsTitle")}
          </Typography.Title>
          {snapshot.pending_decisions.length === 0 ? (
            <EmptyLine>
              {t("page.controlCenter.pendingDecisionsEmpty")}
            </EmptyLine>
          ) : (
            <ul className={styles.list}>
              {snapshot.pending_decisions.map((decision) => (
                <DecisionRow
                  key={decision.decision_request_id}
                  decision={decision}
                />
              ))}
            </ul>
          )}
        </section>
      </div>

      <section aria-labelledby="recent-evidence-title">
        <Typography.Title id="recent-evidence-title" level={4}>
          {t("page.controlCenter.recentEvidenceTitle")}
        </Typography.Title>
        {snapshot.recent_evidence.length === 0 ? (
          <EmptyLine>{t("page.controlCenter.recentEvidenceEmpty")}</EmptyLine>
        ) : (
          <ul className={styles.list}>
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
  const hasAuthoritativeEmptySnapshot =
    status === "success-empty" && content !== null;

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
