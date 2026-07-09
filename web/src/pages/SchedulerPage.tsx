import { Button, Popconfirm, Table, Tag, message } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import { useNavigate } from "react-router-dom";
import { useSchedulerJobs, useCancelJob } from "../hooks/useScheduler";
import PageContainer from "../components/common/PageContainer";
import MonoText from "../components/common/MonoText";
import { formatTime, truncateId } from "../utils/format";
import { SCHEDULE_TYPE_LABELS } from "../utils/constants";
import type { SchedulerJob } from "../api/types";
import { useT } from "../i18n";

export default function SchedulerPage() {
  const t = useT();
  const navigate = useNavigate();
  const { data: jobs, isLoading, refetch } = useSchedulerJobs();
  const cancelMutation = useCancelJob();

  const handleCancel = async (jobId: string) => {
    try {
      await cancelMutation.mutateAsync(jobId);
      message.success(t("scheduler.toast.cancelled"));
    } catch {
      message.error(t("scheduler.toast.cancelFailed"));
    }
  };

  const columns: ColumnsType<SchedulerJob> = [
    {
      title: t("scheduler.table.jobId"),
      dataIndex: "job_id",
      width: 140,
      render: (id: string) => (
        <MonoText style={{ fontSize: 12 }}>{truncateId(id)}</MonoText>
      ),
    },
    {
      title: t("scheduler.table.edictId"),
      dataIndex: "edict_id",
      width: 140,
      render: (id: string) => (
        <a onClick={() => navigate(`/edicts/${id}`)}>
          <MonoText style={{ fontSize: 12 }}>{truncateId(id)}</MonoText>
        </a>
      ),
    },
    {
      title: t("scheduler.table.scheduleType"),
      dataIndex: "schedule_type",
      width: 100,
      render: (type: string) => (
        <Tag>{SCHEDULE_TYPE_LABELS[type] ?? type}</Tag>
      ),
    },
    {
      title: t("scheduler.table.nextRun"),
      dataIndex: "next_run",
      width: 180,
      render: (v: string | null) =>
        v ? formatTime(v) : <span style={{ color: "var(--ts-color-text-secondary)" }}>—</span>,
    },
    {
      title: t("scheduler.table.actions"),
      width: 100,
      render: (_, record) => (
        <Popconfirm
          title={t("scheduler.popconfirm.cancel")}
          onConfirm={() => handleCancel(record.job_id)}
          okText={t("common.confirm")}
          cancelText={t("common.cancel")}
        >
          <Button type="link" danger size="small">
            {t("action.cancel")}
          </Button>
        </Popconfirm>
      ),
    },
  ];

  return (
    <PageContainer
      title={t("scheduler.title")}
      extra={
        <Button icon={<ReloadOutlined />} onClick={() => refetch()}>
          {t("action.refresh")}
        </Button>
      }
    >
      <Table<SchedulerJob>
        columns={columns}
        dataSource={jobs ?? []}
        rowKey="job_id"
        loading={isLoading}
        pagination={false}
        size="middle"
        locale={{ emptyText: t("scheduler.empty") }}
      />
    </PageContainer>
  );
}
