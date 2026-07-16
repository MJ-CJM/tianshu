import { useState, type FormEvent } from "react";
import { Button, Select } from "antd";
import { useT } from "../../i18n";

export type DecisionAction =
  | "approve"
  | "reject"
  | "amend"
  | "retry"
  | "cancel"
  | "observe"
  | "promote"
  | "override";

export interface DecisionSubmission {
  decisionRequestId: string;
  action: DecisionAction;
  reason: string;
  expectedVersion: number;
  payload?: Record<string, unknown>;
}

export interface DecisionView {
  id: string;
  status: "pending" | "resolved" | "expired";
  version: number;
  resolvedAction?: DecisionAction;
  resolvedReason?: string;
}

export interface DecisionResolution {
  status: "pending" | "resolved" | "expired";
  version: number;
  action?: DecisionAction;
  reason?: string;
}

export interface DecisionPanelProps {
  decision: DecisionView;
  actions?: readonly DecisionAction[];
  onSubmit: (submission: DecisionSubmission) => Promise<DecisionResolution>;
}

export default function DecisionPanel({
  decision,
  actions = ["approve", "reject"],
  onSubmit,
}: DecisionPanelProps) {
  const t = useT();
  const [durable, setDurable] = useState(decision);
  const [action, setAction] = useState<DecisionAction>(
    decision.resolvedAction ?? actions[0] ?? "approve",
  );
  const [reason, setReason] = useState(decision.resolvedReason ?? "");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const locked = durable.status !== "pending";

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (locked) return;
    const trimmedReason = reason.trim();
    if (!trimmedReason) {
      setError(t("decisionUi.reasonRequired"));
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      const result = await onSubmit({
        decisionRequestId: durable.id,
        action,
        reason: trimmedReason,
        expectedVersion: durable.version,
      });
      setDurable({
        id: durable.id,
        status: result.status,
        version: result.version,
        resolvedAction: result.action,
        resolvedReason: result.reason,
      });
      if (result.reason) setReason(result.reason);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("decisionUi.submitFailed"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section aria-labelledby={`decision-title-${durable.id}`}>
      <h2 id={`decision-title-${durable.id}`}>{t("decisionUi.title")}</h2>
      <p role="status">{t(`decisionUi.status.${durable.status}`)}</p>
      <form onSubmit={(event) => void submit(event)}>
        <label htmlFor={`decision-action-${durable.id}`}>{t("decisionUi.action")}</label>
        <Select
          id={`decision-action-${durable.id}`}
          aria-label={t("decisionUi.action")}
          value={action}
          disabled={locked || submitting}
          onChange={setAction}
          options={actions.map((value) => ({
            value,
            label: t(`decisionUi.actions.${value}`),
          }))}
        />

        <label htmlFor={`decision-reason-${durable.id}`}>{t("decisionUi.reason")}</label>
        <textarea
          id={`decision-reason-${durable.id}`}
          value={reason}
          disabled={locked || submitting}
          onChange={(event) => setReason(event.target.value)}
        />
        {error ? <p role="alert">{error}</p> : null}
        <Button
          htmlType="submit"
          aria-label={t("decisionUi.submit")}
          loading={submitting}
          disabled={locked}
        >
          {t("decisionUi.submit")}
        </Button>
      </form>
    </section>
  );
}
