import { Button, Card, Empty, Popconfirm, Table, Tag, Typography, message } from "antd";
import { RollbackOutlined } from "@ant-design/icons";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import { listSnapshots, revertSnapshot } from "../../api/keqing";
import type { ShadowSnapshot } from "../../api/types";
import MonoText from "../common/MonoText";
import { formatTime } from "../../utils/format";
import { useT } from "../../i18n";
import { toApiProblem } from "../../api/client";
import PageDataState from "../states/PageDataState";
import { problemPageStatus } from "../states/problemPageStatus";

export default function ShadowSnapshotPanel({ edictId }: { edictId: string }) {
  const t = useT();
  const qc = useQueryClient();
  const snapshotsQuery = useQuery({
    queryKey: ["snapshots", edictId],
    queryFn: () => listSnapshots(edictId),
  });
  const { data, isLoading } = snapshotsQuery;
  const snaps = data?.data ?? [];

  if (snapshotsQuery.error) {
    const problem = toApiProblem(snapshotsQuery.error);
    return (
      <Card size="small" title={t("shadow.title")} style={{ marginTop: 16 }}>
        <PageDataState
          status={problemPageStatus(problem)}
          data={null}
          problem={problem}
          isEmpty={(items: ShadowSnapshot[]) => items.length === 0}
          onRetry={() => void snapshotsQuery.refetch()}
        >
          {() => null}
        </PageDataState>
      </Card>
    );
  }

  // 客卿未执行 → 无快照,不渲染面板(避免噪音)
  if (!isLoading && snaps.length === 0) return null;

  const onRevert = async (sha: string) => {
    try {
      await revertSnapshot(edictId, sha);
      message.success(t("shadow.reverted", { sha: sha.slice(0, 10) }));
      qc.invalidateQueries({ queryKey: ["snapshots", edictId] });
    } catch {
      message.error(t("shadow.revertFailed"));
    }
  };

  const columns: ColumnsType<ShadowSnapshot> = [
    { title: "SHA", dataIndex: "sha", width: 110, render: (s: string) => <MonoText>{s.slice(0, 10)}</MonoText> },
    { title: t("shadow.label"), dataIndex: "label", ellipsis: true },
    { title: t("shadow.time"), dataIndex: "created_at", width: 170, render: (ts: string) => formatTime(ts) },
    {
      title: "",
      key: "action",
      width: 100,
      render: (_: unknown, rec: ShadowSnapshot) => (
        <Popconfirm
          title={t("shadow.revertConfirm")}
          onConfirm={() => onRevert(rec.sha)}
          okText={t("shadow.revert")}
          cancelText={t("common.cancel")}
        >
          <Button size="small" icon={<RollbackOutlined />}>
            {t("shadow.revert")}
          </Button>
        </Popconfirm>
      ),
    },
  ];

  return (
    <Card
      size="small"
      title={
        <span>
          {t("shadow.title")} <Tag color="purple">{t("shadow.keqing")}</Tag>
        </span>
      }
      style={{ marginTop: 16 }}
    >
      <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
        {t("shadow.intro")}
      </Typography.Paragraph>
      {snaps.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        <Table<ShadowSnapshot>
          rowKey="id"
          columns={columns}
          dataSource={snaps}
          loading={isLoading}
          size="small"
          pagination={false}
        />
      )}
    </Card>
  );
}
