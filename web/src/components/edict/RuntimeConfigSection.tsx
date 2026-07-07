import { Form, InputNumber, Divider } from "antd";
import PolicyProfilePanel from "../policy/PolicyProfilePanel";
import type { PolicyProfileValue } from "../policy/PolicyProfilePanel";
import NetworkCapabilitySection from "./NetworkCapabilitySection";
import { useT } from "../../i18n";

interface NetworkCapabilityState {
  api_request_hosts: string[];
  api_request_write_hosts: string[];
}

interface RuntimeConfigSectionProps {
  policyProfile: PolicyProfileValue | null;
  setPolicyProfile: React.Dispatch<React.SetStateAction<PolicyProfileValue | null>>;
  netState: NetworkCapabilityState;
  setNetState: React.Dispatch<React.SetStateAction<NetworkCapabilityState>>;
}

export default function RuntimeConfigSection({
  policyProfile,
  setPolicyProfile,
  netState,
  setNetState,
}: RuntimeConfigSectionProps) {
  const t = useT();
  return (
    <>
      <Divider style={{ margin: "12px 0" }} />
      <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 12 }}>
        {t("form.edict.section.runtime")}
      </div>

      <Form.Item name="timeout_seconds" label={t("form.edict.field.timeoutSeconds")}>
        <InputNumber min={10} max={3600} style={{ width: "100%" }} placeholder={t("form.edict.placeholder.timeoutSeconds")} />
      </Form.Item>

      <Form.Item name="max_iterations" label={t("form.edict.field.maxIterations")}>
        <InputNumber min={1} max={200} style={{ width: "100%" }} placeholder={t("form.edict.placeholder.maxIterations")} />
      </Form.Item>

      <Form.Item name="max_concurrency" label={t("form.edict.field.maxConcurrency")}>
        <InputNumber min={1} max={8} style={{ width: "100%" }} placeholder={t("form.edict.placeholder.maxConcurrency")} />
      </Form.Item>

      <Form.Item name="retry_limit" label={t("form.edict.field.retryLimit")}>
        <InputNumber min={0} max={10} style={{ width: "100%" }} placeholder={t("form.edict.placeholder.retryLimit")} />
      </Form.Item>

      <Form.Item name="token_budget" label={t("form.edict.field.tokenBudget")}>
        <InputNumber min={1} style={{ width: "100%" }} placeholder={t("form.edict.placeholder.tokenBudget")} />
      </Form.Item>

      <Form.Item name="cost_budget_cny" label={t("form.edict.field.costBudget")}>
        <InputNumber min={0} step={0.01} style={{ width: "100%" }} placeholder={t("form.edict.placeholder.costBudget")} />
      </Form.Item>

      <Divider style={{ margin: "12px 0" }} />
      <PolicyProfilePanel
        value={policyProfile ?? undefined}
        onChange={setPolicyProfile}
      />

      <NetworkCapabilitySection
        profileTemplate={policyProfile?.template_name ?? null}
        apiRequestHosts={netState.api_request_hosts}
        apiRequestWriteHosts={netState.api_request_write_hosts}
        onChange={(patch) =>
          setNetState((prev) => ({ ...prev, ...patch }))
        }
      />
    </>
  );
}
