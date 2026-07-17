import { useEffect, useRef, useState, type FormEvent } from "react";
import { Button, Select } from "antd";
import { isApiProblem } from "../../api/client";
import { useT } from "../../i18n";

export type DecisionAction =
  | "approve"
  | "reject"
  | "amend"
  | "retry"
  | "cancel"
  | "observe"
  | "promote"
  | "override"
  | "guide"
  | "continue"
  | "accept_as_is"
  | "abort"
  | "modify_acceptance";

export interface DecisionSubmission {
  decisionRequestId: string;
  action: DecisionAction;
  reason: string;
  expectedVersion: number;
  payload?: Record<string, unknown>;
}

export interface DecisionView {
  id: string;
  status: "pending" | "resolved" | "expired" | "cancelled";
  version: number;
  expiresAt?: string;
  resolvedAction?: DecisionAction;
  resolvedReason?: string;
  resolvedBy?: string;
  resolvedAt?: string;
}

export interface DecisionResolution {
  status: "pending" | "resolved" | "expired" | "cancelled";
  version: number;
  action?: DecisionAction;
  reason?: string;
  actor?: string;
  resolvedAt?: string;
}

export interface DecisionPanelProps {
  decision: DecisionView;
  actions?: readonly DecisionAction[];
  onSubmit: (submission: DecisionSubmission) => Promise<DecisionResolution>;
  onConflict?: () => void;
}

const DEFAULT_ACTIONS: readonly DecisionAction[] = ["approve", "reject"];

export default function DecisionPanel({
  decision,
  actions = DEFAULT_ACTIONS,
  onSubmit,
  onConflict,
}: DecisionPanelProps) {
  const t = useT();
  const headingRef = useRef<HTMLHeadingElement>(null);
  const [durable, setDurable] = useState(decision);
  const [action, setAction] = useState<DecisionAction>(
    decision.resolvedAction ?? actions[0] ?? "approve",
  );
  const [reason, setReason] = useState(decision.resolvedReason ?? "");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expired, setExpired] = useState(false);
  const visibleStatus = expired ? "expired" : durable.status;
  const locked = visibleStatus !== "pending";

  useEffect(() => {
    setDurable(decision);
    setAction(decision.resolvedAction ?? actions[0] ?? "approve");
    setReason(decision.resolvedReason ?? "");
  }, [decision.id, decision.status, decision.version, decision.resolvedAction, decision.resolvedReason, decision.expiresAt, actions]);

  useEffect(() => {
    if (durable.status !== "pending" || durable.expiresAt === undefined) return;
    const expiresAt = new Date(durable.expiresAt).getTime();
    let timer: number;
    const scheduleExpiry = () => {
      const remaining = expiresAt - Date.now();
      if (remaining <= 0) {
        timer = window.setTimeout(() => setExpired(true), 0);
      } else {
        timer = window.setTimeout(scheduleExpiry, Math.min(remaining, 2_147_483_647));
      }
    };
    scheduleExpiry();
    return () => window.clearTimeout(timer);
  }, [durable.status, durable.expiresAt]);

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
        expiresAt: durable.expiresAt,
        resolvedAction: result.action,
        resolvedReason: result.reason,
        resolvedBy: result.actor,
        resolvedAt: result.resolvedAt,
      });
      if (result.reason) setReason(result.reason);
      headingRef.current?.focus();
    } catch (caught) {
      if (isApiProblem(caught) && (caught.status === 409 || caught.status === 412)) {
        setError(t("decisionUi.versionConflict"));
        onConflict?.();
      } else {
        setError(caught instanceof Error ? caught.message : t("decisionUi.submitFailed"));
      }
      headingRef.current?.focus();
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section aria-labelledby={`decision-title-${durable.id}`}>
      <h2 ref={headingRef} id={`decision-title-${durable.id}`} tabIndex={-1}>
        {t("decisionUi.title")}
      </h2>
      <p role="status">{t(`decisionUi.status.${visibleStatus}`)}</p>
      {durable.resolvedBy ? (
        <dl>
          <dt>{t("decisionUi.resolvedBy")}</dt>
          <dd>{durable.resolvedBy}</dd>
          {durable.resolvedAt ? (
            <>
              <dt>{t("decisionUi.resolvedAt")}</dt>
              <dd>{durable.resolvedAt}</dd>
            </>
          ) : null}
        </dl>
      ) : null}
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
