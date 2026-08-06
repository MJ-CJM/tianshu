import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Alert, Card, Radio, Space, Tag, Typography } from "antd";
import { Navigate } from "react-router-dom";

import { isApiProblem, toApiProblem } from "../api/client";
import {
  getOnboardingState,
  ONBOARDING_QUERY_KEY,
  type OnboardingState,
} from "../api/onboarding";
import PageContainer from "../components/common/PageContainer";
import PageDataState from "../components/states/PageDataState";
import { problemPageStatus } from "../components/states/problemPageStatus";
import { useT } from "../i18n";
import { EdictCreationForm } from "./EdictCreatePage";

function OnboardingContent({ state }: { state: OnboardingState }) {
  const t = useT();
  const [acknowledgedProfile, setAcknowledgedProfile] = useState<
    "demo" | "live" | null
  >(null);
  const profileLabel =
    state.profile === "demo"
      ? t("page.onboarding.demoLabel")
      : t("page.onboarding.liveLabel");

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      {state.readiness === "degraded" ? (
        <Alert
          type="warning"
          showIcon
          message={t("page.onboarding.degradedTitle")}
          description={t("page.onboarding.degradedDescription")}
        />
      ) : null}

      <Card title={t("page.onboarding.resourcesTitle")}>
        <Typography.Paragraph type="secondary">
          {t("page.onboarding.resourcesDescription")}
        </Typography.Paragraph>
        <Typography.Title level={5}>
          {t("page.onboarding.departmentsTitle")}
        </Typography.Title>
        <ul aria-label={t("page.onboarding.departmentsTitle")}>
          {state.packagedPersonas.map((persona) => (
            <li key={persona.id}>
              <Typography.Text>{persona.name}</Typography.Text>{" "}
              <Tag>{persona.id}</Tag>
            </li>
          ))}
        </ul>
        <Typography.Title level={5}>
          {t("page.onboarding.skillsTitle")}
        </Typography.Title>
        <ul aria-label={t("page.onboarding.skillsTitle")}>
          {state.builtinSkills.map((skill) => (
            <li key={skill.name}>
              <Typography.Text>{skill.name}</Typography.Text>
            </li>
          ))}
        </ul>
      </Card>

      <Card title={t("page.onboarding.profileTitle")}>
        <Typography.Paragraph>
          {t("page.onboarding.profileDescription")}
        </Typography.Paragraph>
        <Radio
          checked={acknowledgedProfile === state.profile}
          onChange={() => setAcknowledgedProfile(state.profile)}
        >
          {profileLabel}
        </Radio>
        <Typography.Paragraph type="secondary" style={{ marginTop: 12 }}>
          {state.profile === "demo"
            ? t("page.onboarding.demoDescription")
            : t("page.onboarding.liveDescription")}
        </Typography.Paragraph>
      </Card>

      {acknowledgedProfile === state.profile ? (
        <section aria-labelledby="onboarding-edict-title">
          <Typography.Title level={4} id="onboarding-edict-title">
            {t("page.onboarding.edictTitle")}
          </Typography.Title>
          <Typography.Paragraph type="secondary">
            {t("page.onboarding.edictDescription")}
          </Typography.Paragraph>
          <EdictCreationForm governanceConfirmation="always" />
        </section>
      ) : null}
    </Space>
  );
}

export default function OnboardingPage() {
  const t = useT();
  const query = useQuery({
    queryKey: ONBOARDING_QUERY_KEY,
    queryFn: getOnboardingState,
    refetchOnMount: "always",
  });
  const problem = query.error
    ? isApiProblem(query.error)
      ? query.error
      : toApiProblem(query.error)
    : null;

  const hasCurrentSuccess =
    query.isFetchedAfterMount && !query.isFetching && !problem;
  if (hasCurrentSuccess && query.data && !query.data.required) {
    return <Navigate to="/control" replace />;
  }

  const status = problem
    ? problemPageStatus(problem)
    : hasCurrentSuccess
      ? "success-data"
      : "loading";

  return (
    <PageContainer title={t("page.onboarding.title")}>
      <PageDataState
        status={status}
        data={query.data ?? null}
        problem={problem}
        isEmpty={() => false}
        onRetry={() => void query.refetch()}
      >
        {(state) => <OnboardingContent state={state} />}
      </PageDataState>
    </PageContainer>
  );
}
