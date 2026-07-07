import { useState } from "react";
import {
  Table,
  Tag,
  Button,
  Drawer,
  Input,
  Space,
  Spin,
  Modal,
  Form,
  Popconfirm,
  Typography,
  notification,
  theme,
} from "antd";
import { DeleteOutlined, PlusOutlined } from "@ant-design/icons";
import {
  useSkills,
  useSkillDetail,
  useUpdateSkill,
  useCreateSkill,
  useDeleteSkill,
} from "../../hooks/useSystem";
import type { SkillInfo } from "../../api/types";
import { useT } from "../../i18n";
import { monoStyle } from "./shared";

export default function SkillsTab() {
  const t = useT();
  const { token } = theme.useToken();
  const { data: skills, isLoading } = useSkills();
  // 技能库只展示原本加载进来的技能；agent 生成的归"习得技能" tab
  const loadedSkills = (skills ?? []).filter((s) => s.created_by !== "agent");
  const [selectedSkill, setSelectedSkill] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [createForm] = Form.useForm();

  const { data: detail, isLoading: detailLoading } =
    useSkillDetail(selectedSkill);
  const [editContent, setEditContent] = useState<string>("");
  const [dirty, setDirty] = useState(false);

  const updateMutation = useUpdateSkill();
  const createMutation = useCreateSkill();
  const deleteMutation = useDeleteSkill();

  const handleOpenDetail = (name: string) => {
    setSelectedSkill(name);
    setDirty(false);
  };

  const handleContentChange = (val: string) => {
    setEditContent(val);
    setDirty(true);
  };

  const handleSave = () => {
    if (!selectedSkill) return;
    updateMutation.mutate(
      { name: selectedSkill, content: editContent },
      {
        onSuccess: () => {
          notification.success({ message: t("system.toast.skillSaved") });
          setDirty(false);
        },
      },
    );
  };

  const handleCreate = () => {
    createForm.validateFields().then((values) => {
      createMutation.mutate(
        { name: values.name, content: values.content || "" },
        {
          onSuccess: () => {
            notification.success({ message: t("system.toast.skillCreated", { name: values.name }) });
            setCreateOpen(false);
            createForm.resetFields();
          },
        },
      );
    });
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
        <a onClick={() => handleOpenDetail(name)}>{name}</a>
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
      render: (source: string) => {
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
        record.source === "workspace" || record.source === "user" ? (
          <Popconfirm
            title={t("system.skills.confirmDelete")}
            onConfirm={() =>
              deleteMutation.mutate(record.name, {
                onSuccess: () =>
                  notification.success({
                    message: t("system.toast.skillDeleted", { name: record.name }),
                  }),
              })
            }
          >
            <Button
              size="small"
              danger
              icon={<DeleteOutlined />}
              type="text"
            />
          </Popconfirm>
        ) : null,
    },
  ];

  return (
    <>
      <div
        style={{
          marginBottom: 16,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <Typography.Text style={{ color: token.colorTextSecondary }}>
          {t("system.skills.charsLoaded", { n: totalChars.toLocaleString() })}
        </Typography.Text>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => setCreateOpen(true)}
        >
          {t("system.skills.newSkill")}
        </Button>
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
        extra={
          <Button
            type="primary"
            disabled={!dirty}
            loading={updateMutation.isPending}
            onClick={handleSave}
          >
            {t("button.save")}
          </Button>
        }
      >
        {detailLoading ? (
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
              value={dirty ? editContent : detail.content}
              onChange={(e) => handleContentChange(e.target.value)}
              onFocus={() => {
                if (!dirty) setEditContent(detail.content);
              }}
              autoSize={{ minRows: 20, maxRows: 40 }}
              style={monoStyle}
            />
          </div>
        ) : (
          <Typography.Text type="secondary">{t("system.skills.notFound")}</Typography.Text>
        )}
      </Drawer>

      {/* Create Skill Modal */}
      <Modal
        title={t("system.skills.createTitle")}
        open={createOpen}
        onOk={handleCreate}
        onCancel={() => {
          setCreateOpen(false);
          createForm.resetFields();
        }}
        confirmLoading={createMutation.isPending}
        okText={t("action.create")}
        cancelText={t("common.cancel")}
      >
        <Form form={createForm} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item
            name="name"
            label={t("system.skills.form.name")}
            rules={[
              { required: true, message: t("system.skills.form.nameRequired") },
              {
                pattern: /^[a-zA-Z0-9_-]+$/,
                message: t("system.skills.form.namePattern"),
              },
            ]}
          >
            <Input placeholder={t("system.skills.form.namePlaceholder")} />
          </Form.Item>
          <Form.Item name="content" label={t("system.skills.form.content")}>
            <Input.TextArea
              rows={12}
              placeholder={t("system.skills.form.contentPlaceholder")}
              style={monoStyle}
            />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
