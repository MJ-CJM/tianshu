import { Card, Checkbox, Select, Space, Tooltip, Typography } from "antd";
import { useEffect, useState } from "react";
import { listCredentials } from "../../api/credentials";

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
    <Card size="small" title="网络能力" style={{ marginTop: 16 }}>
      <Space direction="vertical" style={{ width: "100%" }}>
        <Tooltip
          title={
            disabled
              ? '需要切换到 "trusted-automation" profile 才能启用 api_request'
              : ""
          }
        >
          <div>
            <Text strong>允许调用的 API host</Text>
            <div style={{ color: "#999", fontSize: 12, marginBottom: 4 }}>
              api_request 工具仅允许访问这些 host
            </div>
            <Select
              mode="tags"
              style={{ width: "100%" }}
              placeholder="从已有凭证选择或输入 host"
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
          允许写方法 (POST/PUT/DELETE/PATCH) — 需要审批
        </Checkbox>

        {allowWrite && !disabled && (
          <div>
            <Text strong>允许写入的 host（必须是上面列表的子集）</Text>
            <Select
              mode="multiple"
              style={{ width: "100%", marginTop: 4 }}
              placeholder="从上面的 host 中选择"
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
