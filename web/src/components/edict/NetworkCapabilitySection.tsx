import { Card, Checkbox, Select, Space, Tooltip, Typography } from "antd";
import { useEffect, useState } from "react";
import { listCredentials } from "../../api/credentials";
import { useT } from "../../i18n";

const { Text } = Typography;

type Props = {
  profileTemplate?: string | null; // "safe-explore" | "refactor-in-place" | "trusted-automation"
  apiRequestHosts: string[];
  apiRequestWriteHosts: string[];
  onChange: (patch: {
    api_request_hosts?: string[];
    api_request_write_hosts?: string[];
  }) => void;
};

export default function NetworkCapabilitySection(props: Props) {
  const t = useT();
  const [allowWrite, setAllowWrite] = useState(
    props.apiRequestWriteHosts.length > 0,
  );
  const [credHosts, setCredHosts] = useState<string[]>([]);

  useEffect(() => {
    listCredentials()
      .then((cs) => setCredHosts(cs.map((c) => c.host_pattern)))
      .catch(() => setCredHosts([]));
  }, []);

  const disabled = props.profileTemplate !== "trusted-automation";
  const hostOptions = Array.from(
    new Set([...credHosts, ...props.apiRequestHosts]),
  ).map((h) => ({ value: h, label: h }));

  return (
    <Card size="small" title={t("comp.network.title")} style={{ marginTop: 16 }}>
      <Space direction="vertical" style={{ width: "100%" }}>
        <Tooltip title={disabled ? t("comp.network.tooltipDisabled") : ""}>
          <div>
            <Text strong>{t("comp.network.allowedHosts")}</Text>
            <div style={{ color: "#999", fontSize: 12, marginBottom: 4 }}>
              {t("comp.network.allowedHostsDesc")}
            </div>
            <Select
              mode="tags"
              style={{ width: "100%" }}
              placeholder={t("comp.network.hostPlaceholder")}
              options={hostOptions}
              value={props.apiRequestHosts}
              onChange={(hosts: string[]) =>
                props.onChange({ api_request_hosts: hosts })
              }
              disabled={disabled}
            />
          </div>
        </Tooltip>

        <Checkbox
          disabled={disabled}
          checked={allowWrite}
          onChange={(e) => {
            const v = e.target.checked;
            setAllowWrite(v);
            if (!v) props.onChange({ api_request_write_hosts: [] });
          }}
        >
          {t("comp.network.allowWrite")}
        </Checkbox>

        {allowWrite && !disabled && (
          <div>
            <Text strong>{t("comp.network.writeHosts")}</Text>
            <Select
              mode="multiple"
              style={{ width: "100%", marginTop: 4 }}
              placeholder={t("comp.network.writeHostPlaceholder")}
              options={props.apiRequestHosts.map((h) => ({
                value: h,
                label: h,
              }))}
              value={props.apiRequestWriteHosts}
              onChange={(hosts: string[]) =>
                props.onChange({ api_request_write_hosts: hosts })
              }
            />
          </div>
        )}
      </Space>
    </Card>
  );
}
