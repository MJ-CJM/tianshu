import { useMemo, useState } from "react";
import { Alert, Button, Spin, Tag, Typography } from "antd";

import type {
  EvolutionPolicyMode,
  EvolutionPolicyV1,
  UpsertEvolutionPolicyV1,
} from "../../api/evolution";
import type { SkillInfo } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { useT } from "../../i18n";
import { useEvolutionPolicies } from "../../hooks/useEvolutionPolicies";
import { useSkills } from "../../hooks/useSystem";

const DEFAULT_SKILL_CANARY_BASIS_POINTS = 1_000;

const rowStyle = {
  border: "1px solid var(--ts-color-border)",
  borderRadius: 8,
  padding: 14,
  background: "var(--ts-color-surface)",
} as const;

interface PolicyRowProps {
  subjectKey: string;
  skill: SkillInfo | null;
  policy: EvolutionPolicyV1 | null;
  isSaving: boolean;
  onSave: (policy: UpsertEvolutionPolicyV1) => Promise<EvolutionPolicyV1>;
}

function curatorProtection(skill: SkillInfo | null): "protected" | "unprotected" | "untracked" {
  if (skill?.pinned === true) return "protected";
  if (skill?.pinned === false) return "unprotected";
  return "untracked";
}

function EvolutionPolicyRow({ subjectKey, skill, policy, isSaving, onSave }: PolicyRowProps) {
  const t = useT();
  const [mode, setMode] = useState<EvolutionPolicyMode>(policy?.mode ?? "canary");
  const [maxCanaryBasisPoints, setMaxCanaryBasisPoints] = useState(
    policy?.max_canary_basis_points ?? DEFAULT_SKILL_CANARY_BASIS_POINTS,
  );

  const validAllocation =
    Number.isInteger(maxCanaryBasisPoints) &&
    maxCanaryBasisPoints >= 0 &&
    maxCanaryBasisPoints <= 1_000 &&
    (mode !== "canary" || maxCanaryBasisPoints > 0);
  const skillName = skill?.name ?? (
    subjectKey.startsWith("skill:") ? subjectKey.slice("skill:".length) : subjectKey
  );
  const titleId = `evolution-policy-${encodeURIComponent(subjectKey)}`;
  const protection = curatorProtection(skill);

  return (
    <article aria-labelledby={titleId} style={rowStyle}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          gap: 16,
          flexWrap: "wrap",
        }}
      >
        <div>
          <Typography.Title
            id={titleId}
            level={5}
            style={{ margin: 0 }}
          >
            {skillName}
          </Typography.Title>
          <Typography.Text type="secondary">
            {skill?.description ?? subjectKey}
          </Typography.Text>
        </div>
        <div>
          <Tag color={skill ? "green" : undefined}>
            {skill
              ? t("page.evolutionCenter.skillAvailable")
              : t("page.evolutionCenter.skillUnavailable")}
          </Tag>
          <Tag>
            {skill?.source ?? t("page.evolutionCenter.skillSourceUnavailable")}
          </Tag>
          <Tag color={protection === "protected" ? "blue" : undefined}>
            {protection === "protected"
              ? t("page.evolutionCenter.curatorProtected")
              : protection === "unprotected"
                ? t("page.evolutionCenter.curatorUnprotected")
                : t("page.evolutionCenter.curatorUntracked")}
          </Tag>
        </div>
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
          gap: 12,
          alignItems: "end",
          marginTop: 14,
        }}
      >
        <label>
          <Typography.Text type="secondary">
            {t("page.evolutionCenter.policyMode")}
          </Typography.Text>
          <select
            aria-label={`${skillName} ${t("page.evolutionCenter.policyMode")}`}
            value={mode}
            disabled={isSaving}
            onChange={(event) => {
              const nextMode = event.target.value as EvolutionPolicyMode;
              setMode(nextMode);
              if (nextMode === "canary" && maxCanaryBasisPoints === 0) {
                setMaxCanaryBasisPoints(DEFAULT_SKILL_CANARY_BASIS_POINTS);
              }
            }}
            style={{ width: "100%", minHeight: 32, marginTop: 4 }}
          >
            <option value="frozen">{t("page.evolutionCenter.modeFrozen")}</option>
            <option value="manual">{t("page.evolutionCenter.modeManual")}</option>
            <option value="canary">{t("page.evolutionCenter.modeCanary")}</option>
          </select>
        </label>

        <label>
          <Typography.Text type="secondary">
            {t("page.evolutionCenter.policyAllocation")}
          </Typography.Text>
          <input
            aria-label={`${skillName} ${t("page.evolutionCenter.policyAllocation")}`}
            type="number"
            min={mode === "canary" ? 1 : 0}
            max={1_000}
            step={1}
            value={maxCanaryBasisPoints}
            disabled={isSaving}
            onChange={(event) => setMaxCanaryBasisPoints(Number(event.target.value))}
            style={{ width: "100%", minHeight: 32, marginTop: 4 }}
          />
        </label>

        <div>
          <Typography.Text type="secondary">
            {policy
              ? `${t("page.evolutionCenter.policyVersion")} ${policy.version}`
              : t("page.evolutionCenter.policyInherited")}
          </Typography.Text>
          <div style={{ marginTop: 4 }}>
            <Button
              type="primary"
              loading={isSaving}
              disabled={!validAllocation}
              onClick={() => {
                void onSave({
                  subject_key: subjectKey,
                  kind: "skill",
                  mode,
                  max_canary_basis_points: maxCanaryBasisPoints,
                  expected_version: policy?.version ?? null,
                }).catch(() => undefined);
              }}
            >
              {t("page.evolutionCenter.policySave")}
            </Button>
          </div>
        </div>
      </div>
    </article>
  );
}

export default function EvolutionPolicyPanel() {
  const t = useT();
  const { principal } = useAuth();
  const isAdmin = principal?.scopes.includes("admin") ?? false;
  const skillsQuery = useSkills();
  const policies = useEvolutionPolicies(isAdmin);
  const policyRows = useMemo(() => {
    const skillBySubject = new Map<string, SkillInfo>(
      (skillsQuery.data ?? []).map((skill) => [`skill:${skill.name}`, skill] as const),
    );
    const policyBySubject = new Map(
      policies.policies
        .filter((policy) => policy.kind === "skill")
        .map((policy) => [policy.subject_key, policy] as const),
    );
    const subjectKeys = new Set([...skillBySubject.keys(), ...policyBySubject.keys()]);

    return [...subjectKeys]
      .sort((left, right) => (left < right ? -1 : left > right ? 1 : 0))
      .map((subjectKey) => ({
        subjectKey,
        skill: skillBySubject.get(subjectKey) ?? null,
        policy: policyBySubject.get(subjectKey) ?? null,
      }));
  }, [policies.policies, skillsQuery.data]);

  return (
    <section aria-labelledby="evolution-policies-title" style={{ display: "grid", gap: 12 }}>
      <div>
        <Typography.Title id="evolution-policies-title" level={4} style={{ margin: 0 }}>
          {t("page.evolutionCenter.policiesTitle")}
        </Typography.Title>
        <Typography.Paragraph type="secondary" style={{ margin: "6px 0 0" }}>
          {t("page.evolutionCenter.policiesDescription")}
        </Typography.Paragraph>
      </div>

      {!isAdmin ? (
        <Alert
          type="info"
          showIcon
          message={t("page.evolutionCenter.policyAdminOnly")}
        />
      ) : skillsQuery.isPending || policies.isLoading ? (
        <div role="status" style={{ padding: 16, textAlign: "center" }}>
          <Spin size="small" />
          <span style={{ marginLeft: 8 }}>{t("page.evolutionCenter.policiesLoading")}</span>
        </div>
      ) : skillsQuery.error || policies.problem ? (
        <Alert
          type="warning"
          showIcon
          message={
            policies.problem?.status === 403
              ? t("page.evolutionCenter.policyAdminOnly")
              : t("page.evolutionCenter.policiesUnavailable")
          }
        />
      ) : (
        <>
          {policies.saveProblem ? (
            <Alert
              type="warning"
              showIcon
              message={
                policies.saveProblem.status === 409
                  ? t("page.evolutionCenter.policyConflict")
                  : policies.saveProblem.status === 403
                    ? t("page.evolutionCenter.policyAdminOnly")
                    : t("page.evolutionCenter.policySaveFailed")
              }
            />
          ) : null}
          {policyRows.length === 0 ? (
            <Alert type="info" message={t("page.evolutionCenter.noAvailableSkills")} />
          ) : (
            policyRows.map(({ subjectKey, skill, policy }) => (
              <EvolutionPolicyRow
                key={`${subjectKey}:${policy?.version ?? 0}`}
                subjectKey={subjectKey}
                skill={skill}
                policy={policy}
                isSaving={policies.savingSubjectKey === subjectKey}
                onSave={policies.savePolicy}
              />
            ))
          )}
        </>
      )}
    </section>
  );
}
