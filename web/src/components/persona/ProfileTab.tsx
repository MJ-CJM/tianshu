import { Spin, Empty, Tag, Space, Alert } from "antd";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { usePersonaProfile } from "../../hooks/usePersonaProfile";

interface Props {
  personaId: string;
}

export default function ProfileTab({ personaId }: Props) {
  const { data, isLoading, error } = usePersonaProfile(personaId);

  if (isLoading) {
    return (
      <div style={{ padding: 24, textAlign: "center" }}>
        <Spin tip="加载成长档案…" />
      </div>
    );
  }

  if (error) {
    return (
      <Alert
        type="error"
        showIcon
        message="加载失败"
        description={String((error as Error).message ?? error)}
        style={{ margin: 16 }}
      />
    );
  }

  if (!data?.exists) {
    return (
      <Empty
        description='暂无成长档案。首版将在 AGENT_END 每 20 次 或每日 03:00 自动生成；也可在 Task 21 上线后点击"立即合成"。'
        style={{ padding: 48 }}
      />
    );
  }

  const fm = data.frontmatter;
  const degraded = !!fm?.degraded;

  return (
    <div style={{ padding: 16 }}>
      <Space wrap style={{ marginBottom: 16 }}>
        {fm && <Tag color="blue">v{fm.version}</Tag>}
        {fm?.data_window && <Tag>window {fm.data_window}</Tag>}
        {fm?.last_synthesized && (
          <Tag color="default">合成于 {fm.last_synthesized.slice(0, 10)}</Tag>
        )}
        {fm?.synthesizer_model && (
          <Tag color="purple">{fm.synthesizer_model}</Tag>
        )}
        {fm?.manually_edited && <Tag color="cyan">用户手改</Tag>}
        {degraded && <Tag color="warning">⚠️ 降级</Tag>}
      </Space>
      <div className="tianshu-markdown">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>
          {data.markdown}
        </ReactMarkdown>
      </div>
    </div>
  );
}
