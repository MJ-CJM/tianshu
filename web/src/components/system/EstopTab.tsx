import {
  Alert,
  Button,
  Card,
  Input,
  Space,
  Switch,
  Tag,
  Typography,
  message,
} from "antd";
import { PoweroffOutlined, ThunderboltOutlined } from "@ant-design/icons";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { engageEstop, getEstop, resumeEstop } from "../../api/estop";
import { useT } from "../../i18n";
import PageDataState from "../states/PageDataState";
import PageQueryError from "../states/PageQueryError";

export default function EstopTab() {
  const t = useT();
  const qc = useQueryClient();
  const [reason, setReason] = useState("");

  const query = useQuery({
    queryKey: ["estop"],
    queryFn: getEstop,
    refetchInterval: 5000,
  });
  const state = query.data?.data;

  const refresh = () => qc.invalidateQueries({ queryKey: ["estop"] });

  const doEngage = async (
    payload: Parameters<typeof engageEstop>[0],
    label: string,
  ) => {
    try {
      await engageEstop({ ...payload, reason: reason || label });
      message.warning(t("estop.engaged", { what: label }));
      refresh();
    } catch {
      message.error(t("estop.actionFailed"));
    }
  };

  const doResume = async (
    payload: Parameters<typeof resumeEstop>[0],
    label: string,
  ) => {
    try {
      await resumeEstop(payload);
      message.success(t("estop.resumed", { what: label }));
      refresh();
    } catch {
      message.error(t("estop.actionFailed"));
    }
  };

  if (query.isLoading) {
    return (
      <PageDataState status="loading" data={null} isEmpty={() => false}>
        {() => null}
      </PageDataState>
    );
  }

  if (query.error) {
    return (
      <PageQueryError
        error={query.error}
        onRetry={() => void query.refetch()}
      />
    );
  }

  if (state && !state.available) {
    return <Alert type="info" showIcon message={t("estop.unavailable")} />;
  }

  const engaged = state?.engaged ?? false;

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="large">
      <Alert
        type={engaged ? "error" : "success"}
        showIcon
        icon={<ThunderboltOutlined />}
        message={
          engaged ? (
            <Space>
              {t("estop.statusEngaged")}
              {state?.kill_all && <Tag color="red">{t("estop.killAll")}</Tag>}
              {state?.network_kill && (
                <Tag color="orange">{t("estop.networkKill")}</Tag>
              )}
              {(state?.frozen_tools ?? []).map((tool) => (
                <Tag key={tool} color="gold">
                  {tool}
                </Tag>
              ))}
            </Space>
          ) : (
            t("estop.statusClear")
          )
        }
        description={
          state?.reason ? `${t("estop.reason")}: ${state.reason}` : undefined
        }
      />

      <Typography.Paragraph type="secondary">
        {t("estop.intro")}
      </Typography.Paragraph>

      <Input
        placeholder={t("estop.reasonPlaceholder")}
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        allowClear
        style={{ maxWidth: 480 }}
      />

      <Card size="small" title={t("estop.killAllTitle")}>
        <Space>
          <Switch
            checked={state?.kill_all ?? false}
            onChange={(v) =>
              v
                ? doEngage({ kill_all: true }, t("estop.killAll"))
                : doResume({ kill_all: true }, t("estop.killAll"))
            }
          />
          <Typography.Text type="secondary">
            {t("estop.killAllDesc")}
          </Typography.Text>
        </Space>
      </Card>

      <Card size="small" title={t("estop.networkKillTitle")}>
        <Space>
          <Switch
            checked={state?.network_kill ?? false}
            onChange={(v) =>
              v
                ? doEngage({ network_kill: true }, t("estop.networkKill"))
                : doResume({ network_kill: true }, t("estop.networkKill"))
            }
          />
          <Typography.Text type="secondary">
            {t("estop.networkKillDesc")}
          </Typography.Text>
        </Space>
      </Card>

      {engaged && (
        <Button
          danger
          type="primary"
          icon={<PoweroffOutlined />}
          onClick={() => doResume({ all_clear: true }, t("estop.allClear"))}
        >
          {t("estop.allClear")}
        </Button>
      )}
    </Space>
  );
}
