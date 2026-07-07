import { useState, useCallback } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  Table,
  Tag,
  Button,
  Space,
  Popconfirm,
  Typography,
  notification,
  Tooltip,
} from "antd";
import {
  PushpinOutlined,
  PushpinFilled,
  EditOutlined,
  RollbackOutlined,
} from "@ant-design/icons";
import SkillEditDialog from "../skill/SkillEditDialog";
import { useSkills } from "../../hooks/useSystem";
import { archiveSkill, pinSkill } from "../../api/system";
import type { SkillInfo } from "../../api/types";
import { useT } from "../../i18n";

export default function SecretSkillsTab() {
  const t = useT();
  const qc = useQueryClient();
  const { data: skills, isLoading } = useSkills();
  const [editName, setEditName] = useState<string | null>(null);

  // 只放 tianshu(agent) 运行时生成/学习的技能
  const agentSkills = (skills ?? []).filter((s) => s.created_by === "agent");

  // Sort: by created_at desc (missing created_at goes last)
  const sorted = [...agentSkills].sort((a, b) => {
    if (a.created_at && b.created_at) {
      return b.created_at.localeCompare(a.created_at);
    }
    if (a.created_at) return -1;
    if (b.created_at) return 1;
    return 0;
  });

  const refresh = useCallback(() => {
    qc.invalidateQueries({ queryKey: ["skills"] });
  }, [qc]);

  const handleArchive = async (name: string) => {
    await archiveSkill(name);
    notification.success({ message: t("skillsPage.toast.archived", { name }) });
    refresh();
  };

  const handlePin = async (name: string, currentPinned: boolean) => {
    await pinSkill(name, !currentPinned);
    notification.success({
      message: !currentPinned
        ? t("skillsPage.toast.pinned", { name })
        : t("skillsPage.toast.unpinned", { name }),
    });
    refresh();
  };

  const columns = [
    {
      title: t("skillsPage.table.name"),
      dataIndex: "name",
      key: "name",
      render: (name: string) => <Typography.Text strong>{name}</Typography.Text>,
    },
    {
      title: t("skillsPage.table.description"),
      dataIndex: "description",
      key: "description",
      ellipsis: true,
    },
    {
      title: t("skillsPage.table.source"),
      key: "source",
      width: 110,
      render: (_: unknown, record: SkillInfo) => {
        if (record.created_by === "agent") {
          return (
            <Tag color="volcano">{t("skillsPage.badge.autoCurated")}</Tag>
          );
        }
        const colorMap: Record<string, string> = {
          builtin: "blue",
          user: "cyan",
          workspace: "green",
          injected: "purple",
        };
        return (
          <Tag color={colorMap[record.source] ?? "default"}>{record.source}</Tag>
        );
      },
    },
    {
      title: t("skillsPage.table.state"),
      dataIndex: "state",
      key: "state",
      width: 90,
      render: (state: string | undefined) => {
        if (!state) return "—";
        const color = state === "active" ? "green" : state === "archived" ? "default" : "orange";
        return <Tag color={color}>{state}</Tag>;
      },
    },
    {
      title: t("skillsPage.table.usage"),
      key: "usage",
      width: 120,
      render: (_: unknown, record: SkillInfo) => {
        if (record.usage_count == null) return "—";
        const rate =
          record.success_rate != null
            ? `${(record.success_rate * 100).toFixed(0)}%`
            : "—";
        return (
          <Typography.Text style={{ fontSize: 12 }}>
            {record.usage_count} / {rate}
          </Typography.Text>
        );
      },
    },
    {
      title: t("skillsPage.table.flags"),
      key: "flags",
      width: 120,
      render: (_: unknown, record: SkillInfo) => (
        <Space size={4}>
          {record.human_curated && (
            <Tooltip title={t("skillsPage.badge.humanCurated")}>
              <Tag color="gold">{t("skillsPage.badge.humanCuratedShort")}</Tag>
            </Tooltip>
          )}
          {record.pinned && (
            <Tooltip title={t("skillsPage.badge.pinned")}>
              <Tag color="geekblue">{t("skillsPage.badge.pinnedShort")}</Tag>
            </Tooltip>
          )}
        </Space>
      ),
    },
    {
      title: t("skillsPage.table.createdAt"),
      dataIndex: "created_at",
      key: "created_at",
      width: 150,
      render: (v: string | undefined) =>
        v ? new Date(v).toLocaleString("zh-CN") : "—",
    },
    {
      title: t("skillsPage.table.actions"),
      key: "actions",
      width: 140,
      render: (_: unknown, record: SkillInfo) => (
        <Space size={4}>
          <Tooltip
            title={
              record.pinned ? t("skillsPage.action.unpin") : t("skillsPage.action.pin")
            }
          >
            <Button
              size="small"
              type="text"
              icon={record.pinned ? <PushpinFilled /> : <PushpinOutlined />}
              onClick={() => handlePin(record.name, record.pinned ?? false)}
            />
          </Tooltip>
          <Tooltip title={t("skillsPage.action.edit")}>
            <Button
              size="small"
              type="text"
              icon={<EditOutlined />}
              onClick={() => setEditName(record.name)}
            />
          </Tooltip>
          <Popconfirm
            title={t("skillsPage.confirmArchive")}
            onConfirm={() => handleArchive(record.name)}
            okText={t("skillsPage.action.archive")}
            cancelText={t("common.cancel")}
          >
            <Tooltip title={t("skillsPage.action.archive")}>
              <Button
                size="small"
                type="text"
                danger
                icon={<RollbackOutlined />}
              />
            </Tooltip>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <>
      <Table
        dataSource={sorted}
        columns={columns}
        rowKey="name"
        loading={isLoading}
        size="small"
        pagination={false}
      />
      <SkillEditDialog
        name={editName}
        onClose={() => setEditName(null)}
        onSaved={refresh}
      />
    </>
  );
}
