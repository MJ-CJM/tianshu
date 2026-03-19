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

export default function SchedulerPage() {
  const navigate = useNavigate();
  const { data: jobs, isLoading, refetch } = useSchedulerJobs();
  const cancelMutation = useCancelJob();

  const handleCancel = async (jobId: string) => {
    try {
      await cancelMutation.mutateAsync(jobId);
      message.success("排期已取消");
    } catch {
      message.error("取消失败");
    }
  };

  const columns: ColumnsType<SchedulerJob> = [
    {
      title: "调度编号",
      dataIndex: "job_id",
      width: 140,
      render: (id: string) => (
        <MonoText style={{ fontSize: 12 }}>{truncateId(id)}</MonoText>
      ),
    },
    {
      title: "敕令编号",
      dataIndex: "edict_id",
      width: 140,
      render: (id: string) => (
        <a onClick={() => navigate(`/edicts/${id}`)}>
          <MonoText style={{ fontSize: 12 }}>{truncateId(id)}</MonoText>
        </a>
      ),
    },
    {
      title: "调度类型",
      dataIndex: "schedule_type",
      width: 100,
      render: (type: string) => (
        <Tag>{SCHEDULE_TYPE_LABELS[type] ?? type}</Tag>
      ),
    },
    {
      title: "下次执行",
      dataIndex: "next_run",
      width: 180,
      render: (v: string | null) =>
        v ? formatTime(v) : <span style={{ color: "#8c8c8c" }}>—</span>,
    },
    {
      title: "操作",
      width: 100,
      render: (_, record) => (
        <Popconfirm
          title="确认取消此排期？"
          onConfirm={() => handleCancel(record.job_id)}
          okText="确认"
          cancelText="取消"
        >
          <Button type="link" danger size="small">
            取消
          </Button>
        </Popconfirm>
      ),
    },
  ];

  return (
    <PageContainer
      title="文书房"
      extra={
        <Button icon={<ReloadOutlined />} onClick={() => refetch()}>
          刷新
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
        locale={{ emptyText: "暂无排期任务" }}
      />
    </PageContainer>
  );
}
