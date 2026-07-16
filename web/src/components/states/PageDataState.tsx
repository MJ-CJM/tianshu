import type { ReactNode } from "react";
import { Button, Spin } from "antd";
import type { ApiProblem, PageDataStatus } from "../../contracts/api";
import { useT } from "../../i18n";

export interface PageDataStateProps<T> {
  status: PageDataStatus;
  data: T | null;
  problem?: ApiProblem | null;
  isEmpty: (data: T) => boolean;
  onRetry?: () => void;
  children: (data: T) => ReactNode;
}

function StateMessage({
  role,
  title,
  explanation,
  problem,
  onRetry,
}: {
  role: "status" | "alert";
  title: string;
  explanation: string;
  problem?: ApiProblem | null;
  onRetry?: () => void;
}) {
  const t = useT();
  const canRetry = Boolean(problem?.retryable && onRetry);
  return (
    <section
      role={role}
      style={{
        border: "1px solid var(--ts-color-border)",
        borderRadius: 8,
        padding: 16,
        background: "var(--ts-color-surface)",
        color: "var(--ts-color-text)",
      }}
    >
      <h2 style={{ margin: 0, fontSize: 16 }}>{title}</h2>
      <p style={{ margin: "8px 0 0", color: "var(--ts-color-text-secondary)" }}>
        {explanation}
      </p>
      {problem?.correlationId ? (
        <p style={{ margin: "8px 0 0", fontFamily: "monospace" }}>
          {t("pageDataState.correlation")}: {problem.correlationId}
        </p>
      ) : null}
      {canRetry ? (
        <Button aria-label={t("pageDataState.retry")} style={{ marginTop: 12 }} onClick={onRetry}>
          {t("pageDataState.retry")}
        </Button>
      ) : null}
    </section>
  );
}

export default function PageDataState<T>({
  status,
  data,
  problem,
  isEmpty,
  onRetry,
  children,
}: PageDataStateProps<T>) {
  const t = useT();
  const hasData = data !== null && !isEmpty(data);

  if (status === "loading") {
    return (
      <section role="status" style={{ padding: 24, textAlign: "center" }}>
        <Spin />
        <h2 style={{ margin: "12px 0 0", fontSize: 16 }}>{t("pageDataState.loadingTitle")}</h2>
        <p>{t("pageDataState.loadingDescription")}</p>
      </section>
    );
  }

  if (status === "success-data" && hasData) return <>{children(data)}</>;

  if (status === "success-empty" || (status === "success-data" && !hasData)) {
    return (
      <StateMessage
        role="status"
        title={t("pageDataState.emptyTitle")}
        explanation={t("pageDataState.emptyDescription")}
      />
    );
  }

  if (status === "stale") {
    return (
      <>
        <StateMessage
          role="alert"
          title={t("pageDataState.staleTitle")}
          explanation={problem?.message || t("pageDataState.staleDescription")}
          problem={problem}
          onRetry={onRetry}
        />
        {hasData ? children(data) : null}
      </>
    );
  }

  const stateCopy =
    status === "permission-denied"
      ? [t("pageDataState.permissionTitle"), t("pageDataState.permissionDescription")]
      : status === "service-unavailable"
        ? [t("pageDataState.unavailableTitle"), t("pageDataState.unavailableDescription")]
        : [t("pageDataState.errorTitle"), t("pageDataState.errorDescription")];

  return (
    <StateMessage
      role="alert"
      title={stateCopy[0]!}
      explanation={problem?.message || stateCopy[1]!}
      problem={problem}
      onRetry={onRetry}
    />
  );
}
