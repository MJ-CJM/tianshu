import { useState } from "react";
import {
  Alert,
  Button,
  Collapse,
  DatePicker,
  Dropdown,
  Form,
  Input,
  InputNumber,
  Modal,
  Radio,
  Select,
  Space,
  Table,
  Tag,
  TimePicker,
  message,
} from "antd";
import {
  EditOutlined,
  HistoryOutlined,
  MoreOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import dayjs, { type Dayjs } from "dayjs";
import { Link, useNavigate } from "react-router-dom";
import { listSchedulerJobRuns } from "../api/scheduler";
import type { EdictSchedule, SchedulerJob, SchedulerRun } from "../api/types";
import {
  useCancelJob,
  usePauseJob,
  useResumeJob,
  useRunJobNow,
  useSchedulerJobs,
  useUpdateJob,
} from "../hooks/useScheduler";
import PageContainer from "../components/common/PageContainer";
import MonoText from "../components/common/MonoText";
import { formatTime, truncateId } from "../utils/format";
import { useT, type TFunction } from "../i18n";

type EditValues = {
  mode: "once" | "daily" | "weekly" | "interval" | "custom";
  at?: Dayjs;
  time?: Dayjs;
  weekday?: number;
  interval_seconds?: number;
  cron?: string;
};

function scheduleLabel(job: SchedulerJob, t: TFunction): string {
  if (job.schedule_type === "cron" && job.cron_expr) {
    const parts = job.cron_expr.trim().split(/\s+/);
    if (
      parts.length === 5 &&
      /^\d+$/.test(parts[0]!) &&
      /^\d+$/.test(parts[1]!)
    ) {
      const time = `${parts[1]!.padStart(2, "0")}:${parts[0]!.padStart(2, "0")}`;
      if (parts[2] === "*" && parts[3] === "*" && parts[4] === "*") {
        return `${t("scheduler.schedule.daily", { time })} · ${job.timezone}`;
      }
      if (parts[2] === "*" && parts[3] === "*" && /^[0-7]$/.test(parts[4]!)) {
        const weekday = parts[4] === "7" ? "0" : parts[4]!;
        return `${t("scheduler.schedule.weekly", {
          weekday: t(`form.edict.option.weekday${weekday}`),
          time,
        })} · ${job.timezone}`;
      }
    }
    return `${job.cron_expr} · ${job.timezone}`;
  }
  if (job.schedule_type === "interval") {
    const seconds = job.interval_seconds ?? 0;
    if (seconds > 0 && seconds % 3600 === 0) {
      return `${t("scheduler.schedule.everyHours", { n: seconds / 3600 })} · ${job.timezone}`;
    }
    if (seconds > 0 && seconds % 60 === 0) {
      return `${t("scheduler.schedule.everyMinutes", { n: seconds / 60 })} · ${job.timezone}`;
    }
    return `${t("scheduler.schedule.everySeconds", { n: seconds || "—" })} · ${job.timezone}`;
  }
  return `${t("scheduler.schedule.once")} · ${job.timezone}`;
}

function editValues(job: SchedulerJob): EditValues {
  if (job.schedule_type === "once") {
    return { mode: "once", at: job.next_run ? dayjs(job.next_run) : undefined };
  }
  if (job.schedule_type === "interval") {
    return { mode: "interval", interval_seconds: job.interval_seconds ?? 3600 };
  }
  const parts = (job.cron_expr ?? "").trim().split(/\s+/);
  if (
    parts.length === 5 &&
    /^\d+$/.test(parts[0]!) &&
    /^\d+$/.test(parts[1]!)
  ) {
    const time = dayjs()
      .hour(Number(parts[1]))
      .minute(Number(parts[0]))
      .second(0);
    if (parts[2] === "*" && parts[3] === "*" && parts[4] === "*") {
      return { mode: "daily", time };
    }
    if (parts[2] === "*" && parts[3] === "*" && /^[0-7]$/.test(parts[4]!)) {
      return {
        mode: "weekly",
        time,
        weekday: parts[4] === "7" ? 0 : Number(parts[4]),
      };
    }
  }
  return { mode: "custom", cron: job.cron_expr ?? "" };
}

export default function SchedulerPage() {
  const t = useT();
  const navigate = useNavigate();
  const [editForm] = Form.useForm<EditValues>();
  const editMode = Form.useWatch("mode", editForm);
  const [editing, setEditing] = useState<SchedulerJob | null>(null);
  const [advancedScheduleOpen, setAdvancedScheduleOpen] = useState(false);
  const [historyJob, setHistoryJob] = useState<SchedulerJob | null>(null);
  const [history, setHistory] = useState<SchedulerRun[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<Error | null>(null);
  const { data: jobs, isLoading, isError, error, refetch } = useSchedulerJobs();
  const cancelMutation = useCancelJob();
  const pauseMutation = usePauseJob();
  const resumeMutation = useResumeJob();
  const runMutation = useRunJobNow();
  const updateMutation = useUpdateJob();

  const notify = async (
    action: () => Promise<unknown>,
    success: string,
    failure: string,
  ) => {
    try {
      await action();
      message.success(success);
      return true;
    } catch {
      message.error(failure);
      return false;
    }
  };

  const confirmCancel = (job: SchedulerJob) => {
    Modal.confirm({
      title: t("scheduler.popconfirm.cancel"),
      content: job.title || truncateId(job.edict_id),
      okText: t("common.confirm"),
      cancelText: t("common.cancel"),
      okButtonProps: { danger: true },
      onOk: () =>
        notify(
          () => cancelMutation.mutateAsync(job.job_id),
          t("scheduler.toast.cancelled"),
          t("scheduler.toast.cancelFailed"),
        ),
    });
  };

  const loadHistory = async (job: SchedulerJob) => {
    setHistory([]);
    setHistoryError(null);
    setHistoryLoading(true);
    try {
      const result = await listSchedulerJobRuns(job.job_id);
      setHistory(result.data ?? []);
    } catch (cause) {
      setHistoryError(
        cause instanceof Error
          ? cause
          : new Error(t("scheduler.toast.historyFailed")),
      );
      message.error(t("scheduler.toast.historyFailed"));
    } finally {
      setHistoryLoading(false);
    }
  };

  const openHistory = (job: SchedulerJob) => {
    setHistoryJob(job);
    void loadHistory(job);
  };

  const openEdit = (job: SchedulerJob) => {
    const values = editValues(job);
    setEditing(job);
    setAdvancedScheduleOpen(
      values.mode === "interval" || values.mode === "custom",
    );
    editForm.setFieldsValue(values);
  };

  const saveEdit = async () => {
    if (!editing) return;
    const values = await editForm.validateFields();
    const timezone =
      editing.timezone ||
      Intl.DateTimeFormat().resolvedOptions().timeZone ||
      "UTC";
    let schedule: EdictSchedule;
    if (values.mode === "once") {
      schedule = {
        type: "once",
        at: values.at!.toISOString(),
        cron: null,
        interval_seconds: null,
        timezone,
        concurrency_policy: "skip",
        misfire_policy: "coalesce",
      };
    } else if (values.mode === "interval") {
      schedule = {
        type: "interval",
        at: null,
        cron: null,
        interval_seconds: values.interval_seconds!,
        timezone,
        concurrency_policy: "skip",
        misfire_policy: "coalesce",
      };
    } else {
      const time = values.time ?? dayjs().hour(9).minute(0);
      const cron =
        values.mode === "custom"
          ? values.cron!.trim()
          : values.mode === "weekly"
            ? `${time.minute()} ${time.hour()} * * ${values.weekday ?? 1}`
            : `${time.minute()} ${time.hour()} * * *`;
      schedule = {
        type: "cron",
        at: null,
        cron,
        interval_seconds: null,
        timezone,
        concurrency_policy: "skip",
        misfire_policy: "coalesce",
      };
    }
    const saved = await notify(
      () => updateMutation.mutateAsync({ jobId: editing.job_id, schedule }),
      t("scheduler.toast.updated"),
      t("scheduler.toast.updateFailed"),
    );
    if (saved) setEditing(null);
  };

  const columns: ColumnsType<SchedulerJob> = [
    {
      title: t("scheduler.table.task"),
      dataIndex: "title",
      render: (_title: string, record) => (
        <Link to={`/edicts/${record.edict_id}`}>
          {record.title || <MonoText>{truncateId(record.edict_id)}</MonoText>}
        </Link>
      ),
    },
    {
      title: t("scheduler.table.schedule"),
      width: 230,
      render: (_, record) => scheduleLabel(record, t),
    },
    {
      title: t("scheduler.table.status"),
      dataIndex: "status",
      width: 100,
      render: (status: SchedulerJob["status"]) => (
        <Tag
          color={
            status === "active"
              ? "green"
              : status === "paused"
                ? "gold"
                : status === "completed"
                  ? "blue"
                  : "red"
          }
        >
          {t(`scheduler.status.${status}`)}
        </Tag>
      ),
    },
    {
      title: t("scheduler.table.nextRun"),
      dataIndex: "next_run",
      width: 180,
      render: (value: string | null) => (value ? formatTime(value) : "—"),
    },
    {
      title: t("scheduler.table.lastRun"),
      dataIndex: "last_run",
      width: 170,
      render: (run: SchedulerRun | null) =>
        run ? (
          <Space size={4}>
            <Tag>
              {t(`scheduler.runStatus.${run.execution_status ?? run.status}`)}
            </Tag>
            <span>{formatTime(run.started_at)}</span>
          </Space>
        ) : (
          "—"
        ),
    },
    {
      title: t("scheduler.table.actions"),
      width: 170,
      render: (_, record) => (
        <Space size="small">
          {record.status === "active" ? (
            <Button
              size="small"
              icon={<PauseCircleOutlined />}
              onClick={() =>
                notify(
                  () => pauseMutation.mutateAsync(record.job_id),
                  t("scheduler.toast.paused"),
                  t("scheduler.toast.pauseFailed"),
                )
              }
            >
              {t("scheduler.action.pause")}
            </Button>
          ) : record.status === "paused" || record.status === "failed" ? (
            <Button
              size="small"
              type="primary"
              icon={<PlayCircleOutlined />}
              onClick={() =>
                notify(
                  () => resumeMutation.mutateAsync(record.job_id),
                  t("scheduler.toast.resumed"),
                  t("scheduler.toast.resumeFailed"),
                )
              }
            >
              {t("scheduler.action.resume")}
            </Button>
          ) : null}
          <Dropdown
            trigger={["click"]}
            menu={{
              items: [
                {
                  key: "run",
                  icon: <ThunderboltOutlined />,
                  label: t("scheduler.action.runNow"),
                  disabled:
                    record.status === "failed" || record.status === "completed",
                  onClick: () =>
                    notify(
                      () => runMutation.mutateAsync(record.job_id),
                      t("scheduler.toast.queued"),
                      t("scheduler.toast.runFailed"),
                    ),
                },
                {
                  key: "edit",
                  icon: <EditOutlined />,
                  label: t("scheduler.action.edit"),
                  disabled:
                    record.status === "failed" || record.status === "completed",
                  onClick: () => openEdit(record),
                },
                {
                  key: "history",
                  icon: <HistoryOutlined />,
                  label: t("scheduler.action.history"),
                  onClick: () => openHistory(record),
                },
                { type: "divider" },
                {
                  key: "cancel",
                  danger: true,
                  disabled: record.status === "completed",
                  label: t("action.cancel"),
                  onClick: () => confirmCancel(record),
                },
              ],
            }}
          >
            <Button
              size="small"
              icon={<MoreOutlined />}
              aria-label={t("scheduler.action.more")}
            />
          </Dropdown>
        </Space>
      ),
    },
  ];

  return (
    <PageContainer
      title={t("scheduler.title")}
      extra={
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => refetch()}>
            {t("action.refresh")}
          </Button>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => navigate("/edicts/create?schedule=once")}
          >
            {t("scheduler.create")}
          </Button>
        </Space>
      }
    >
      {isError && (
        <Alert
          type="error"
          showIcon
          message={t("scheduler.loadFailed")}
          description={error instanceof Error ? error.message : undefined}
          action={
            <Button onClick={() => refetch()}>{t("action.retry")}</Button>
          }
          style={{ marginBottom: 16 }}
        />
      )}
      {!isError && (
        <Table<SchedulerJob>
          columns={columns}
          dataSource={jobs ?? []}
          rowKey="job_id"
          loading={isLoading}
          pagination={false}
          size="middle"
          locale={{ emptyText: t("scheduler.empty") }}
          scroll={{ x: 1050 }}
        />
      )}

      <Modal
        open={editing !== null}
        title={t("scheduler.editTitle")}
        onCancel={() => setEditing(null)}
        onOk={() => void saveEdit()}
        confirmLoading={updateMutation.isPending}
        destroyOnHidden
      >
        <Form form={editForm} layout="vertical" preserve={false}>
          <Form.Item name="mode" label={t("scheduler.edit.mode")}>
            <Radio.Group
              optionType="button"
              buttonStyle="solid"
              options={[
                { value: "once", label: t("form.edict.option.scheduleOnce") },
                { value: "daily", label: t("form.edict.option.repeatDaily") },
                { value: "weekly", label: t("form.edict.option.repeatWeekly") },
              ]}
            />
          </Form.Item>
          {editMode === "once" && (
            <Form.Item
              name="at"
              label={t("form.edict.field.scheduleAt")}
              rules={[
                { required: true },
                {
                  validator: (_, value: Dayjs | undefined) =>
                    !value || value.isAfter(dayjs())
                      ? Promise.resolve()
                      : Promise.reject(
                          new Error(
                            t("form.edict.validation.scheduleAtFuture"),
                          ),
                        ),
                },
              ]}
            >
              <DatePicker
                showTime
                format="YYYY-MM-DD HH:mm"
                style={{ width: "100%" }}
                disabledDate={(date) => date.endOf("day").isBefore(dayjs())}
              />
            </Form.Item>
          )}
          {(editMode === "daily" || editMode === "weekly") && (
            <Form.Item
              name="time"
              label={t("form.edict.field.repeatTime")}
              rules={[{ required: true }]}
            >
              <TimePicker format="HH:mm" style={{ width: "100%" }} />
            </Form.Item>
          )}
          {editMode === "weekly" && (
            <Form.Item
              name="weekday"
              label={t("form.edict.field.repeatWeekday")}
            >
              <Select
                options={[1, 2, 3, 4, 5, 6, 0].map((value) => ({
                  value,
                  label: t(`form.edict.option.weekday${value}`),
                }))}
              />
            </Form.Item>
          )}
          <Collapse
            activeKey={advancedScheduleOpen ? ["advanced"] : []}
            onChange={(keys) =>
              setAdvancedScheduleOpen(
                (Array.isArray(keys) ? keys : [keys]).includes("advanced"),
              )
            }
            items={[
              {
                key: "advanced",
                label: t("scheduler.edit.advanced"),
                children: (
                  <>
                    <Radio.Group
                      value={editMode}
                      onChange={(event) =>
                        editForm.setFieldValue(
                          "mode",
                          event.target.value as EditValues["mode"],
                        )
                      }
                      optionType="button"
                      options={[
                        {
                          value: "interval",
                          label: t("scheduler.edit.interval"),
                        },
                        {
                          value: "custom",
                          label: t("form.edict.option.repeatCustom"),
                        },
                      ]}
                      style={{ marginBottom: 16 }}
                    />
                    {editMode === "interval" && (
                      <Form.Item
                        name="interval_seconds"
                        label={t("scheduler.edit.intervalSeconds")}
                        rules={[{ required: true }]}
                      >
                        <InputNumber min={60} style={{ width: "100%" }} />
                      </Form.Item>
                    )}
                    {editMode === "custom" && (
                      <Form.Item
                        name="cron"
                        label={t("form.edict.field.customCron")}
                        rules={[{ required: true }]}
                      >
                        <Input
                          placeholder={t("form.edict.placeholder.cronExpr")}
                        />
                      </Form.Item>
                    )}
                  </>
                ),
              },
            ]}
          />
        </Form>
      </Modal>

      <Modal
        open={historyJob !== null}
        title={t("scheduler.historyTitle")}
        footer={null}
        onCancel={() => setHistoryJob(null)}
        width={760}
      >
        {historyError ? (
          <Alert
            type="error"
            showIcon
            message={t("scheduler.toast.historyFailed")}
            description={historyError.message}
            action={
              <Button
                onClick={() => historyJob && void loadHistory(historyJob)}
              >
                {t("action.retry")}
              </Button>
            }
          />
        ) : (
          <Table<SchedulerRun>
            rowKey="id"
            loading={historyLoading}
            pagination={false}
            dataSource={history}
            columns={[
              {
                title: t("scheduler.table.startedAt"),
                dataIndex: "started_at",
                render: (value: string) => formatTime(value),
              },
              {
                title: t("scheduler.table.status"),
                dataIndex: "status",
                render: (value: string, run) => (
                  <Tag>
                    {t(`scheduler.runStatus.${run.execution_status ?? value}`)}
                  </Tag>
                ),
              },
              {
                title: t("scheduler.table.error"),
                dataIndex: "error",
                render: (value) => value || "—",
              },
            ]}
          />
        )}
      </Modal>
    </PageContainer>
  );
}
