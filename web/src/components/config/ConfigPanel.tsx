import { SettingOutlined } from "@ant-design/icons";
import { Button, Input, InputNumber, Switch, Spin, notification } from "antd";
import { useEffect, useState } from "react";
import { useConfig, useUpdateConfig } from "../../hooks/useConfig";
import type { LLMConfigUpdateRequest } from "../../api/types";
import styles from "./ConfigPanel.module.css";

export default function ConfigPanel() {
  const [open, setOpen] = useState(false);
  const { data: config, isLoading } = useConfig();
  const mutation = useUpdateConfig();

  const [form, setForm] = useState<LLMConfigUpdateRequest>({});

  useEffect(() => {
    if (config) {
      setForm({
        model: config.model,
        max_retries: config.max_retries,
        enabled: config.enabled,
      });
    }
  }, [config]);

  const handleApply = () => {
    const payload: LLMConfigUpdateRequest = {};
    if (config) {
      if (form.model !== undefined && form.model !== config.model)
        payload.model = form.model;
      if (
        form.max_retries !== undefined &&
        form.max_retries !== config.max_retries
      )
        payload.max_retries = form.max_retries;
      if (form.enabled !== undefined && form.enabled !== config.enabled)
        payload.enabled = form.enabled;
      if (form.api_key) payload.api_key = form.api_key;
    }

    if (Object.keys(payload).length === 0) {
      notification.info({ message: "无变更" });
      return;
    }

    mutation.mutate(payload, {
      onSuccess: () => {
        notification.success({ message: "配置已更新" });
        setForm((prev) => ({ ...prev, api_key: undefined }));
      },
    });
  };

  if (!open) {
    return (
      <div className={styles.trigger} onClick={() => setOpen(true)}>
        <SettingOutlined />
      </div>
    );
  }

  return (
    <>
      <div className={styles.trigger} onClick={() => setOpen(false)}>
        <SettingOutlined />
      </div>
      <div className={styles.panel}>
        <div className={styles.title}>
          <SettingOutlined /> LLM 配置
        </div>

        {isLoading ? (
          <Spin />
        ) : (
          <>
            <div className={styles.field}>
              <div className={styles.label}>Model</div>
              <Input
                size="small"
                value={form.model ?? ""}
                onChange={(e) =>
                  setForm((prev) => ({ ...prev, model: e.target.value }))
                }
              />
            </div>

            <div className={styles.field}>
              <div className={styles.label}>
                API Key ({config?.api_key_masked})
              </div>
              <Input.Password
                size="small"
                placeholder="输入新 Key 以更新"
                value={form.api_key ?? ""}
                onChange={(e) =>
                  setForm((prev) => ({ ...prev, api_key: e.target.value }))
                }
              />
            </div>

            <div className={styles.field}>
              <div className={styles.label}>Max Retries</div>
              <InputNumber
                size="small"
                min={0}
                max={10}
                value={form.max_retries}
                onChange={(v) =>
                  setForm((prev) => ({
                    ...prev,
                    max_retries: v ?? 0,
                  }))
                }
              />
            </div>

            <div className={styles.field}>
              <div className={styles.label}>Enabled</div>
              <Switch
                checked={form.enabled}
                onChange={(v) =>
                  setForm((prev) => ({ ...prev, enabled: v }))
                }
              />
            </div>

            <div className={styles.footer}>
              <Button size="small" onClick={() => setOpen(false)}>
                关闭
              </Button>
              <Button
                size="small"
                type="primary"
                loading={mutation.isPending}
                onClick={handleApply}
              >
                应用
              </Button>
            </div>
          </>
        )}
      </div>
    </>
  );
}
