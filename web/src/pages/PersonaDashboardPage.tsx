import { useState } from "react";
import {
  Row,
  Col,
  Tag,
  Space,
  Typography,
  Statistic,
  Progress,
  Spin,
  Empty,
  Button,
  Modal,
  Form,
  Input,
  InputNumber,
  Switch,
  Select,
  Popconfirm,
  notification,
  theme,
} from "antd";
import {
  CheckCircleOutlined,
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
} from "@ant-design/icons";
import PageContainer from "../components/common/PageContainer";
import GlowCard from "../components/common/GlowCard";
import {
  usePersonas,
  usePersonaMetrics,
  useCreatePersona,
  useUpdatePersona,
  useDeletePersona,
} from "../hooks/usePersonas";
import { useTools, useSkills } from "../hooks/useSystem";
import type { PersonaInfo, PersonaCreateRequest, PersonaUpdateRequest } from "../api/types";

function PersonaCard({
  persona,
  expanded,
  onToggle,
  onEdit,
  onDelete,
}: {
  persona: PersonaInfo;
  expanded: boolean;
  onToggle: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const { token } = theme.useToken();
  const { data: metrics, isLoading } = usePersonaMetrics(expanded ? persona.id : null);

  return (
    <GlowCard
      hoverable
      style={{ cursor: "pointer", height: "100%" }}
      title={
        <Space>
          <span>{persona.name}</span>
          <Tag color="blue">{persona.department}</Tag>
          {persona.can_delegate && (
            <Tag icon={<CheckCircleOutlined />} color="green">
              可委派
            </Tag>
          )}
        </Space>
      }
      extra={
        <Space size="small" onClick={(e) => e.stopPropagation()}>
          <Button
            type="text"
            size="small"
            icon={<EditOutlined />}
            onClick={onEdit}
          />
          <Popconfirm
            title="确定删除该官员？"
            description="删除后不可恢复"
            onConfirm={onDelete}
            okText="确认"
            cancelText="取消"
          >
            <Button type="text" size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      }
      onClick={onToggle}
    >
      <div style={{ marginBottom: 8 }}>
        <Typography.Text style={{ fontSize: 12, color: token.colorTextSecondary }}>
          可用工具
        </Typography.Text>
        <div style={{ marginTop: 4 }}>
          {persona.tools_allowed.length > 0 ? (
            persona.tools_allowed.map((tool) => (
              <Tag key={tool} style={{ marginBottom: 4, fontSize: 11 }}>
                {tool}
              </Tag>
            ))
          ) : (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              无特定工具
            </Typography.Text>
          )}
        </div>
      </div>

      {persona.skills_allowed.length > 0 && (
        <div style={{ marginBottom: 8 }}>
          <Typography.Text style={{ fontSize: 12, color: token.colorTextSecondary }}>
            技能
          </Typography.Text>
          <div style={{ marginTop: 4 }}>
            {persona.skills_allowed.map((skill) => (
              <Tag key={skill} color="purple" style={{ marginBottom: 4, fontSize: 11 }}>
                {skill}
              </Tag>
            ))}
          </div>
        </div>
      )}

      {persona.delegates_to.length > 0 && (
        <div style={{ marginBottom: 8 }}>
          <Typography.Text style={{ fontSize: 12, color: token.colorTextSecondary }}>
            可委派至
          </Typography.Text>
          <div style={{ marginTop: 4 }}>
            {persona.delegates_to.map((d) => (
              <Tag key={d} color="cyan" style={{ marginBottom: 4, fontSize: 11 }}>
                {d}
              </Tag>
            ))}
          </div>
        </div>
      )}

      {expanded && (
        <div
          style={{
            marginTop: 12,
            paddingTop: 12,
            borderTop: `1px solid ${token.colorBorder}`,
          }}
          onClick={(e) => e.stopPropagation()}
        >
          {isLoading ? (
            <div style={{ textAlign: "center", padding: 16 }}>
              <Spin size="small" />
            </div>
          ) : metrics ? (
            <Space direction="vertical" size="small" style={{ width: "100%" }}>
              <Row gutter={16}>
                <Col span={12}>
                  <Statistic
                    title="总执行"
                    value={metrics.total_executions}
                    valueStyle={{ fontSize: 18 }}
                  />
                </Col>
                <Col span={12}>
                  <Statistic
                    title="完成"
                    value={metrics.completed}
                    valueStyle={{ fontSize: 18, color: token.colorSuccess }}
                  />
                </Col>
              </Row>
              <Row gutter={16}>
                <Col span={12}>
                  <Statistic
                    title="失败"
                    value={metrics.failed}
                    valueStyle={{ fontSize: 18, color: token.colorError }}
                  />
                </Col>
                <Col span={12}>
                  <div>
                    <Typography.Text
                      style={{ fontSize: 12, color: token.colorTextSecondary }}
                    >
                      成功率
                    </Typography.Text>
                    <Progress
                      percent={Number(metrics.success_rate.toFixed(1))}
                      size="small"
                      status={metrics.success_rate >= 80 ? "success" : "normal"}
                    />
                  </div>
                </Col>
              </Row>
              <Row gutter={16}>
                <Col span={12}>
                  <Statistic
                    title="总 Token"
                    value={metrics.total_tokens}
                    valueStyle={{ fontSize: 16 }}
                  />
                </Col>
                <Col span={12}>
                  <Statistic
                    title="均 Token"
                    value={metrics.avg_tokens_per_execution}
                    valueStyle={{ fontSize: 16 }}
                  />
                </Col>
              </Row>
              <Row gutter={16}>
                <Col span={12}>
                  <Statistic
                    title="总成本"
                    value={metrics.total_cost_cny}
                    prefix="¥"
                    precision={4}
                    valueStyle={{ fontSize: 16 }}
                  />
                </Col>
                <Col span={12}>
                  <Statistic
                    title="均耗时"
                    value={metrics.avg_duration_seconds}
                    suffix="s"
                    precision={1}
                    valueStyle={{ fontSize: 16 }}
                  />
                </Col>
              </Row>
            </Space>
          ) : (
            <Typography.Text type="secondary">暂无指标数据</Typography.Text>
          )}
        </div>
      )}
    </GlowCard>
  );
}

function PersonaFormModal({
  open,
  editingPersona,
  personas,
  onClose,
  onSubmit,
  loading,
}: {
  open: boolean;
  editingPersona: PersonaInfo | null;
  personas: PersonaInfo[];
  onClose: () => void;
  onSubmit: (values: PersonaCreateRequest | PersonaUpdateRequest) => void;
  loading: boolean;
}) {
  const [form] = Form.useForm();
  const isEdit = !!editingPersona;
  const { data: tools } = useTools();
  const { data: skills } = useSkills();

  const toolOptions = (tools ?? []).map((t) => ({
    value: t.name,
    label: `${t.name} (tier ${t.tier})`,
  }));
  const skillOptions = (skills ?? []).map((s) => ({
    value: s.name,
    label: `${s.name}${s.description ? ` — ${s.description}` : ""}`,
  }));

  const handleOpen = () => {
    if (editingPersona) {
      form.setFieldsValue({
        ...editingPersona,
        tools_allowed: editingPersona.tools_allowed,
        tools_denied: editingPersona.tools_denied,
        skills_allowed: editingPersona.skills_allowed,
        delegates_to: editingPersona.delegates_to,
      });
    } else {
      form.resetFields();
    }
  };

  return (
    <Modal
      title={isEdit ? "编辑官员" : "添加官员"}
      open={open}
      onCancel={onClose}
      afterOpenChange={(visible) => visible && handleOpen()}
      onOk={() => form.submit()}
      confirmLoading={loading}
      destroyOnClose
      width={560}
    >
      <Form
        form={form}
        layout="vertical"
        onFinish={onSubmit}
        initialValues={{
          tool_tier_max: 0,
          can_delegate: false,
          tools_allowed: [],
          tools_denied: [],
          skills_allowed: [],
          delegates_to: [],
        }}
      >
        {!isEdit && (
          <Form.Item
            name="id"
            label="ID"
            rules={[
              { required: true, message: "请输入 ID" },
              { pattern: /^[a-z][a-z0-9_]*$/, message: "仅允许小写字母、数字和下划线" },
            ]}
          >
            <Input placeholder="如 bingbu, neige" />
          </Form.Item>
        )}
        <Form.Item
          name="name"
          label="名称"
          rules={[{ required: true, message: "请输入名称" }]}
        >
          <Input placeholder="如 兵部尚书" />
        </Form.Item>
        <Form.Item
          name="department"
          label="部门"
          rules={[{ required: true, message: "请输入部门" }]}
        >
          <Input placeholder="如 兵部" />
        </Form.Item>
        <Form.Item name="tools_allowed" label="允许工具">
          <Select
            mode="multiple"
            placeholder="选择允许使用的工具"
            options={toolOptions}
            showSearch
            optionFilterProp="label"
          />
        </Form.Item>
        <Form.Item name="tools_denied" label="禁用工具">
          <Select
            mode="multiple"
            placeholder="选择禁用的工具"
            options={toolOptions}
            showSearch
            optionFilterProp="label"
          />
        </Form.Item>
        <Form.Item name="skills_allowed" label="技能">
          <Select
            mode="multiple"
            placeholder="选择技能（留空 = 全部注入）"
            options={skillOptions}
            showSearch
            optionFilterProp="label"
          />
        </Form.Item>
        <Form.Item name="tool_tier_max" label="最大工具等级">
          <InputNumber min={0} max={10} style={{ width: "100%" }} />
        </Form.Item>
        <Form.Item name="can_delegate" label="可委派" valuePropName="checked">
          <Switch />
        </Form.Item>
        <Form.Item name="delegates_to" label="可委派目标">
          <Select
            mode="multiple"
            placeholder="选择可委派的官员"
            options={personas
              .filter((p) => p.id !== editingPersona?.id)
              .map((p) => ({ value: p.id, label: `${p.name} (${p.id})` }))}
          />
        </Form.Item>
      </Form>
    </Modal>
  );
}

export default function PersonaDashboardPage() {
  const { data: personas, isLoading } = usePersonas();
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingPersona, setEditingPersona] = useState<PersonaInfo | null>(null);

  const createMutation = useCreatePersona();
  const updateMutation = useUpdatePersona();
  const deleteMutation = useDeletePersona();

  const handleEdit = (persona: PersonaInfo) => {
    setEditingPersona(persona);
    setModalOpen(true);
  };

  const handleCreate = () => {
    setEditingPersona(null);
    setModalOpen(true);
  };

  const handleDelete = (id: string) => {
    deleteMutation.mutate(id, {
      onSuccess: () => notification.success({ message: "官员已删除" }),
    });
  };

  const handleSubmit = (values: PersonaCreateRequest | PersonaUpdateRequest) => {
    if (editingPersona) {
      updateMutation.mutate(
        { id: editingPersona.id, body: values },
        {
          onSuccess: () => {
            notification.success({ message: "官员已更新" });
            setModalOpen(false);
          },
        },
      );
    } else {
      createMutation.mutate(values as PersonaCreateRequest, {
        onSuccess: () => {
          notification.success({ message: "官员已创建" });
          setModalOpen(false);
        },
      });
    }
  };

  if (isLoading) {
    return (
      <PageContainer title="百官阁">
        <div style={{ textAlign: "center", padding: 48 }}>
          <Spin size="large" />
        </div>
      </PageContainer>
    );
  }

  if (!personas || personas.length === 0) {
    return (
      <PageContainer
        title="百官阁"
        extra={
          <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>
            添加官员
          </Button>
        }
      >
        <Empty description="暂无百官配置" />
        <PersonaFormModal
          open={modalOpen}
          editingPersona={null}
          personas={[]}
          onClose={() => setModalOpen(false)}
          onSubmit={handleSubmit}
          loading={createMutation.isPending}
        />
      </PageContainer>
    );
  }

  return (
    <PageContainer
      title="百官阁"
      extra={
        <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>
          添加官员
        </Button>
      }
    >
      <Row gutter={[16, 16]}>
        {personas.map((persona) => (
          <Col key={persona.id} xs={24} sm={12} lg={8}>
            <PersonaCard
              persona={persona}
              expanded={expandedId === persona.id}
              onToggle={() =>
                setExpandedId((prev) => (prev === persona.id ? null : persona.id))
              }
              onEdit={() => handleEdit(persona)}
              onDelete={() => handleDelete(persona.id)}
            />
          </Col>
        ))}
      </Row>

      <PersonaFormModal
        open={modalOpen}
        editingPersona={editingPersona}
        personas={personas}
        onClose={() => setModalOpen(false)}
        onSubmit={handleSubmit}
        loading={createMutation.isPending || updateMutation.isPending}
      />
    </PageContainer>
  );
}
