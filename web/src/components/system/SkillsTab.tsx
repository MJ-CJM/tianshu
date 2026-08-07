import { useState } from "react";
import {
  Alert,
  Button,
  Table,
  Tag,
  Drawer,
  Input,
  Space,
  Spin,
  Typography,
  Tooltip,
  notification,
  theme,
} from "antd";
import { PushpinFilled, PushpinOutlined } from "@ant-design/icons";
import { useSkills, useSkillDetail } from "../../hooks/useSystem";
import { pinSkill } from "../../api/system";
import type { SkillInfo } from "../../api/types";
import { useT } from "../../i18n";
import { monoStyle } from "./shared";
import PageQueryError from "../states/PageQueryError";

export default function SkillsTab() {
  const t = useT();
  const { token } = theme.useToken();
  const skillsQuery = useSkills();
  const { data: skills, isLoading } = skillsQuery;
  const loadedSkills = skills ?? [];
  const [selectedSkill, setSelectedSkill] = useState<string | null>(null);

  const detailQuery = useSkillDetail(selectedSkill);
  const { data: detail, isLoading: detailLoading } = detailQuery;

  if (skillsQuery.error) {
    return (
      <PageQueryError
        error={skillsQuery.error}
        onRetry={() => void skillsQuery.refetch()}
      />
    );
  }

  const handleOpenDetail = (name: string) => {
    setSelectedSkill(name);
  };

  const handlePin = async (name: string, currentPinned: boolean) => {
    await pinSkill(name, !currentPinned);
    notification.success({
      message: !currentPinned
        ? t("skillsPage.toast.pinned", { name })
        : t("skillsPage.toast.unpinned", { name }),
    });
    await skillsQuery.refetch();
  };

  // Compute char budget stats
  const totalChars = loadedSkills.reduce(
    (acc, s) => acc + s.content_length,
    0,
  );

  const columns = [
    {
      title: t("system.skills.table.name"),
      dataIndex: "name",
      key: "name",
      render: (name: string) => (
        <Button
          type="link"
          size="small"
          style={{ padding: 0, height: "auto" }}
          onClick={(event) => {
            event.stopPropagation();
            handleOpenDetail(name);
          }}
        >
          {name}
        </Button>
      ),
    },
    {
      title: t("system.skills.table.description"),
      dataIndex: "description",
      key: "description",
      ellipsis: true,
    },
    {
      title: t("system.skills.table.source"),
      dataIndex: "source",
      key: "source",
      width: 100,
      render: (source: string, record: SkillInfo) => {
        if (record.created_by === "agent") {
          return <Tag color="volcano">{t("skillsPage.badge.autoCurated")}</Tag>;
        }
        const colorMap: Record<string, string> = {
          builtin: "blue",
          user: "cyan",
          workspace: "green",
          injected: "purple",
        };
        return <Tag color={colorMap[source] ?? "default"}>{source}</Tag>;
      },
    },
    {
      title: t("system.skills.table.toolTier"),
      dataIndex: "tool_tier",
      key: "tool_tier",
      width: 100,
      render: (v: string | null) => v ?? "-",
    },
    {
      title: t("system.skills.table.chars"),
      dataIndex: "content_length",
      key: "content_length",
      width: 90,
      render: (v: number) => v.toLocaleString(),
    },
    {
      title: t("system.skills.table.actions"),
      key: "actions",
      width: 80,
      render: (_: unknown, record: SkillInfo) =>
        record.created_by === "agent" ? (
          <Tooltip
            title={
              record.pinned ? t("skillsPage.action.unpin") : t("skillsPage.action.pin")
            }
          >
            <Button
              size="small"
              type="text"
              aria-label={
                record.pinned ? t("skillsPage.action.unpin") : t("skillsPage.action.pin")
              }
              icon={record.pinned ? <PushpinFilled /> : <PushpinOutlined />}
              onClick={(event) => {
                event.stopPropagation();
                void handlePin(record.name, record.pinned ?? false);
              }}
            />
          </Tooltip>
        ) : null,
    },
  ];

  return (
    <>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message={t("system.skills.catalogReadOnly")}
      />
      <div
        style={{
          marginBottom: 16,
        }}
      >
        <Typography.Text style={{ color: token.colorTextSecondary }}>
          {t("system.skills.charsLoaded", { n: totalChars.toLocaleString() })}
        </Typography.Text>
      </div>

      <Table
        dataSource={loadedSkills}
        columns={columns}
        rowKey="name"
        loading={isLoading}
        size="small"
        pagination={false}
        onRow={(record) => ({
          style: { cursor: "pointer" },
          onClick: () => handleOpenDetail(record.name),
        })}
      />

      {/* Skill Detail Drawer */}
      <Drawer
        title={selectedSkill ? t("system.skills.detailWithName", { name: selectedSkill }) : t("system.skills.detail")}
        open={!!selectedSkill}
        onClose={() => setSelectedSkill(null)}
        width={640}
      >
        {detailQuery.error ? (
          <PageQueryError
            error={detailQuery.error}
            onRetry={() => void detailQuery.refetch()}
          />
        ) : detailLoading ? (
          <Spin />
        ) : detail ? (
          <div>
            <div style={{ marginBottom: 12 }}>
              <Space>
                <Tag
                  color={
                    detail.source === "builtin"
                      ? "blue"
                      : detail.source === "workspace"
                        ? "green"
                        : "purple"
                  }
                >
                  {detail.source}
                </Tag>
                {detail.always && <Tag color="orange">always</Tag>}
                {detail.tool_tier && (
                  <Tag>tier: {detail.tool_tier}</Tag>
                )}
              </Space>
            </div>
            {detail.description && (
              <Typography.Paragraph
                type="secondary"
                style={{ marginBottom: 12 }}
              >
                {detail.description}
              </Typography.Paragraph>
            )}
            <Input.TextArea
              value={detail.content}
              readOnly
              autoSize={{ minRows: 20, maxRows: 40 }}
              style={monoStyle}
            />
          </div>
        ) : (
          <Typography.Text type="secondary">{t("system.skills.notFound")}</Typography.Text>
        )}
      </Drawer>
    </>
  );
}
