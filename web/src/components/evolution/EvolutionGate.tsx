import { useEffect, useRef, useState } from "react";
import { Tag, Typography } from "antd";

import type {
  EvolutionCandidateSummaryV1,
  EvolutionRoutingSummaryV1,
} from "../../api/evolution";
import { useT } from "../../i18n";
import { evolutionCandidateAnchor } from "../../utils/evolutionCandidateNavigation";
import MonoText from "../common/MonoText";

export interface EvolutionGateProps {
  candidate: EvolutionCandidateSummaryV1;
  routing: EvolutionRoutingSummaryV1 | null;
}

const panelStyle = {
  border: "1px solid var(--ts-color-border)",
  borderRadius: 8,
  padding: 16,
  background: "var(--ts-color-surface)",
} as const;

const factStyle = {
  display: "grid",
  gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
  gap: 12,
  margin: "12px 0 0",
} as const;

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <Typography.Text type="secondary">{label}</Typography.Text>
      <div style={{ marginTop: 4 }}>{children}</div>
    </div>
  );
}

export default function EvolutionGate({ candidate, routing }: EvolutionGateProps) {
  const t = useT();
  const cardRef = useRef<HTMLElement>(null);
  const [isHashTargeted, setIsHashTargeted] = useState(false);
  const anchorId = evolutionCandidateAnchor(candidate.candidate_id);
  const titleId = `${anchorId}-title`;
  const blockingGates = candidate.gates.filter((gate) => gate.blocking);

  useEffect(() => {
    const syncHashTarget = () => {
      const targeted = window.location.hash.slice(1) === anchorId;
      setIsHashTargeted(targeted);
      if (targeted) cardRef.current?.scrollIntoView?.({ block: "center" });
    };

    syncHashTarget();
    window.addEventListener("hashchange", syncHashTarget);
    return () => window.removeEventListener("hashchange", syncHashTarget);
  }, [anchorId]);

  return (
    <article
      ref={cardRef}
      id={anchorId}
      aria-labelledby={titleId}
      data-hash-targeted={isHashTargeted ? "true" : undefined}
      style={{
        ...panelStyle,
        scrollMarginBlock: 24,
        boxShadow: isHashTargeted ? "0 0 0 2px var(--ts-color-accent)" : undefined,
        transition: "box-shadow 160ms ease",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", gap: 16 }}>
        <div>
          <Typography.Title id={titleId} level={4} style={{ margin: 0 }}>
            {candidate.candidate_id}
          </Typography.Title>
          <Typography.Text type="secondary">
            {candidate.kind} · {t("evolutionUi.version")} {candidate.version}
          </Typography.Text>
        </div>
        <div>
          <Tag>{candidate.lifecycle}</Tag>
          <Tag color={candidate.promotion_allowed ? "green" : "red"}>
            {candidate.promotion_allowed
              ? t("evolutionUi.promotionAllowed")
              : t("evolutionUi.promotionBlocked")}
          </Tag>
        </div>
      </div>

      <div style={factStyle}>
        <Fact label={t("evolutionUi.artifactHash")}><MonoText>{candidate.artifact_hash}</MonoText></Fact>
        <Fact label={t("evolutionUi.rollbackState")}>{candidate.rollback_state}</Fact>
        <Fact label={t("evolutionUi.routingVersion")}>
          {routing ? routing.routing_version : t("evolutionUi.routingUnavailable")}
        </Fact>
      </div>

      {routing ? (
        <section aria-label={t("evolutionUi.routingTitle")} style={{ ...factStyle, marginTop: 16 }}>
          <Fact label={t("evolutionUi.subject")}>{routing.subject_key}</Fact>
          <Fact label={t("evolutionUi.allocation")}>{routing.allocation_percent}%</Fact>
          <Fact label={t("evolutionUi.championAssignments")}>
            {routing.champion_assignment_count}
          </Fact>
          <Fact label={t("evolutionUi.challengerAssignments")}>
            {routing.challenger_assignment_count}
          </Fact>
        </section>
      ) : null}

      {blockingGates.length > 0 ? (
        <section aria-labelledby={`blocking-${candidate.candidate_id}`} style={{ marginTop: 18 }}>
          <Typography.Title id={`blocking-${candidate.candidate_id}`} level={5} style={{ margin: 0 }}>
            {t("evolutionUi.blockingGates")}
          </Typography.Title>
          <ul style={{ margin: "10px 0 0", paddingLeft: 20 }}>
            {blockingGates.map((gate) => (
              <li key={gate.code} style={{ marginTop: 10 }}>
                <div>
                  <MonoText>{gate.code}</MonoText> · {gate.status}
                  {gate.current !== null && gate.required !== null ? (
                    <>
                      {" · "}
                      <span>{gate.current} / {gate.required}</span>
                    </>
                  ) : null}
                </div>
                {gate.evidence_hash ? (
                  <div style={{ marginTop: 4 }}>
                    <Typography.Text type="secondary">
                      {t("evolutionUi.evidenceHash")}: {" "}
                    </Typography.Text>
                    <MonoText>{gate.evidence_hash}</MonoText>
                    {gate.evidence_bundle_id ? (
                      <>
                        {" · "}
                        <a
                          href={`/api/evidence/${encodeURIComponent(gate.evidence_bundle_id)}/download`}
                        >
                          {t("evolutionUi.viewEvidence")}
                        </a>
                      </>
                    ) : null}
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </article>
  );
}
