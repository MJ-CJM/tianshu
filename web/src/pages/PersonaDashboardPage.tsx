import { useState, useMemo } from "react";
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
  Tabs,
  Table,
  Card,
  Tooltip,
} from "antd";
import {
  CheckCircleOutlined,
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  NodeIndexOutlined,
  ApartmentOutlined,
  WarningOutlined,
  EyeOutlined,
} from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import PageContainer from "../components/common/PageContainer";
import GlowCard from "../components/common/GlowCard";
import {
  usePersonas,
  usePersonaMetrics,
  useCreatePersona,
  useUpdatePersona,
  useDeletePersona,
} from "../hooks/usePersonas";
import {
  useDepartments,
  useCreateDepartment,
  useUpdateDepartment,
  useDeleteDepartment,
} from "../hooks/useDepartments";
import { useTools, useSkills } from "../hooks/useSystem";
import { useRoutingRules } from "../hooks/useOps";
import { useConfigs } from "../hooks/useConfig";
import type {
  PersonaInfo,
  PersonaCreateRequest,
  PersonaUpdateRequest,
  DepartmentInfo,
  DepartmentCreateRequest,
  DepartmentUpdateRequest,
} from "../api/types";

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
  const navigate = useNavigate();
  const { data: metrics, isLoading } = usePersonaMetrics(expanded ? persona.id : null);

  return (
    <GlowCard
      hoverable
      style={{ cursor: "pointer", height: "100%" }}
      title={
        <Space>
          <span>{persona.name}</span>
          <Tag color="blue">{persona.department_name ?? persona.department}</Tag>
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
            icon={<EyeOutlined />}
            onClick={() => navigate(`/personas/${persona.id}`)}
          />
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
      {/* Tool tier — the primary capability indicator */}
      <div style={{ marginBottom: 8 }}>
        <Typography.Text style={{ fontSize: 12, color: token.colorTextSecondary }}>
          工具权限
        </Typography.Text>
        <div style={{ marginTop: 4 }}>
          <Tag
            color={persona.tool_tier_max >= 2 ? "green" : persona.tool_tier_max >= 1 ? "blue" : "default"}
            style={{ fontSize: 11 }}
          >
            Tier {persona.tool_tier_max}
          </Tag>
          <Typography.Text type="secondary" style={{ fontSize: 11, marginLeft: 4 }}>
            {persona.tool_tier_max === 0
              ? "仅监控/审核，不操作工具"
              : persona.tool_tier_max === 1
                ? "基础工具（只读）"
                : `可使用 tier ≤ ${persona.tool_tier_max} 的所有工具`}
          </Typography.Text>
        </div>
      </div>

      {/* Whitelisted tools (if any) */}
      {persona.tools_allowed.length > 0 && (
        <div style={{ marginBottom: 8 }}>
          <Typography.Text style={{ fontSize: 12, color: token.colorTextSecondary }}>
            指定工具
          </Typography.Text>
          <div style={{ marginTop: 4 }}>
            {persona.tools_allowed.map((tool) => (
              <Tag key={tool} style={{ marginBottom: 4, fontSize: 11 }}>
                {tool}
              </Tag>
            ))}
          </div>
        </div>
      )}

      {/* Denied tools */}
      {persona.tools_denied.length > 0 && (
        <div style={{ marginBottom: 8 }}>
          <Typography.Text style={{ fontSize: 12, color: token.colorTextSecondary }}>
            禁用工具
          </Typography.Text>
          <div style={{ marginTop: 4 }}>
            {persona.tools_denied.map((tool) => (
              <Tag key={tool} color="red" style={{ marginBottom: 4, fontSize: 11 }}>
                {tool}
              </Tag>
            ))}
          </div>
        </div>
      )}

      {/* Skills */}
      <div style={{ marginBottom: 8 }}>
        <Typography.Text style={{ fontSize: 12, color: token.colorTextSecondary }}>
          技能注入
        </Typography.Text>
        <div style={{ marginTop: 4 }}>
          {persona.skills_allowed.length > 0 ? (
            persona.skills_allowed.map((skill) => (
              <Tag key={skill} color="purple" style={{ marginBottom: 4, fontSize: 11 }}>
                {skill}
              </Tag>
            ))
          ) : (
            <Typography.Text type="secondary" style={{ fontSize: 11 }}>
              全部技能（无过滤）
            </Typography.Text>
          )}
        </div>
      </div>

      {/* Delegation */}
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

      {/* LLM Config */}
      {persona.llm_config_name && (
        <div style={{ marginBottom: 8 }}>
          <Typography.Text style={{ fontSize: 12, color: token.colorTextSecondary }}>
            LLM 配置
          </Typography.Text>
          <div style={{ marginTop: 4 }}>
            <Tag color="orange" style={{ fontSize: 11 }}>
              {persona.llm_config_name}
            </Tag>
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
  departments,
  onClose,
  onSubmit,
  loading,
}: {
  open: boolean;
  editingPersona: PersonaInfo | null;
  personas: PersonaInfo[];
  departments: DepartmentInfo[];
  onClose: () => void;
  onSubmit: (values: PersonaCreateRequest | PersonaUpdateRequest) => void;
  loading: boolean;
}) {
  const [form] = Form.useForm();
  const isEdit = !!editingPersona;
  const { data: tools } = useTools();
  const { data: skills } = useSkills();
  const { data: configsData } = useConfigs();

  const llmConfigOptions = [
    { value: "", label: "使用全局配置（默认）" },
    ...(configsData?.configs ?? []).map((c) => ({
      value: c.name,
      label: `${c.name} (${c.model})`,
    })),
  ];
  const toolOptions = (tools ?? []).map((t) => ({
    value: t.name,
    label: `${t.name} (tier ${t.tier})`,
  }));
  const skillOptions = (skills ?? []).map((s) => ({
    value: s.name,
    label: `${s.name}${s.description ? ` — ${s.description}` : ""}`,
  }));
  const deptOptions = departments.map((d) => ({
    value: d.id,
    label: `${d.name} (${d.id})`,
  }));

  const handleOpen = () => {
    if (editingPersona) {
      form.setFieldsValue({
        ...editingPersona,
        tools_allowed: editingPersona.tools_allowed,
        tools_denied: editingPersona.tools_denied,
        skills_allowed: editingPersona.skills_allowed,
        delegates_to: editingPersona.delegates_to,
        llm_config_name: editingPersona.llm_config_name ?? "",
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
          rules={[{ required: true, message: "请选择部门" }]}
        >
          <Select
            placeholder="选择部门"
            options={deptOptions}
            showSearch
            optionFilterProp="label"
          />
        </Form.Item>
        <Form.Item name="llm_config_name" label="LLM 配置">
          <Select
            placeholder="使用全局配置（默认）"
            options={llmConfigOptions}
            allowClear
          />
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

function DepartmentFormModal({
  open,
  editingDept,
  onClose,
  onSubmit,
  loading,
}: {
  open: boolean;
  editingDept: DepartmentInfo | null;
  onClose: () => void;
  onSubmit: (values: DepartmentCreateRequest | DepartmentUpdateRequest) => void;
  loading: boolean;
}) {
  const [form] = Form.useForm();
  const isEdit = !!editingDept;

  const handleOpen = () => {
    if (editingDept) {
      form.setFieldsValue({
        id: editingDept.id,
        name: editingDept.name,
        description: editingDept.description,
      });
    } else {
      form.resetFields();
    }
  };

  return (
    <Modal
      title={isEdit ? "编辑部门" : "添加部门"}
      open={open}
      onCancel={onClose}
      afterOpenChange={(visible) => visible && handleOpen()}
      onOk={() => form.submit()}
      confirmLoading={loading}
      destroyOnClose
      width={480}
    >
      <Form form={form} layout="vertical" onFinish={onSubmit}>
        {!isEdit && (
          <Form.Item
            name="id"
            label="部门 ID"
            rules={[
              { required: true, message: "请输入部门 ID" },
              { pattern: /^[a-z][a-z0-9_]*$/, message: "仅允许小写字母、数字和下划线" },
            ]}
          >
            <Input placeholder="如 bingbu, neige" />
          </Form.Item>
        )}
        <Form.Item
          name="name"
          label="部门名称"
          rules={[{ required: true, message: "请输入部门名称" }]}
        >
          <Input placeholder="如 兵部 (Ministry of War)" />
        </Form.Item>
        <Form.Item name="description" label="描述">
          <Input.TextArea rows={3} placeholder="部门职责描述（可选）" />
        </Form.Item>
      </Form>
    </Modal>
  );
}

function DepartmentTab({
  departments,
  personas,
}: {
  departments: DepartmentInfo[];
  personas: PersonaInfo[];
}) {
  const [modalOpen, setModalOpen] = useState(false);
  const [editingDept, setEditingDept] = useState<DepartmentInfo | null>(null);

  const createMutation = useCreateDepartment();
  const updateMutation = useUpdateDepartment();
  const deleteMutation = useDeleteDepartment();

  // Count personas per department
  const personaCountMap = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const p of personas) {
      counts[p.department] = (counts[p.department] ?? 0) + 1;
    }
    return counts;
  }, [personas]);

  const handleSubmit = (values: DepartmentCreateRequest | DepartmentUpdateRequest) => {
    if (editingDept) {
      updateMutation.mutate(
        { id: editingDept.id, body: values },
        {
          onSuccess: () => {
            notification.success({ message: "部门已更新" });
            setModalOpen(false);
          },
        },
      );
    } else {
      createMutation.mutate(values as DepartmentCreateRequest, {
        onSuccess: () => {
          notification.success({ message: "部门已创建" });
          setModalOpen(false);
        },
      });
    }
  };

  const handleDelete = (id: string) => {
    deleteMutation.mutate(id, {
      onSuccess: () => notification.success({ message: "部门已删除" }),
      onError: (err: Error) => {
        const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
        notification.error({ message: msg ?? "删除失败，该部门下仍有官员" });
      },
    });
  };

  const columns = [
    { title: "ID", dataIndex: "id", key: "id", width: 120 },
    { title: "名称", dataIndex: "name", key: "name", width: 200 },
    { title: "描述", dataIndex: "description", key: "description", ellipsis: true },
    {
      title: "官员数量",
      key: "persona_count",
      width: 100,
      render: (_: unknown, record: DepartmentInfo) => (
        <Tag color={personaCountMap[record.id] ? "blue" : "default"}>
          {personaCountMap[record.id] ?? 0}
        </Tag>
      ),
    },
    {
      title: "操作",
      key: "actions",
      width: 120,
      render: (_: unknown, record: DepartmentInfo) => (
        <Space size="small">
          <Button
            type="text"
            size="small"
            icon={<EditOutlined />}
            onClick={() => {
              setEditingDept(record);
              setModalOpen(true);
            }}
          />
          <Popconfirm
            title="确定删除该部门？"
            description="仅当部门下无官员时可删除"
            onConfirm={() => handleDelete(record.id)}
            okText="确认"
            cancelText="取消"
          >
            <Button
              type="text"
              size="small"
              danger
              icon={<DeleteOutlined />}
              disabled={(personaCountMap[record.id] ?? 0) > 0}
            />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <>
      <div style={{ marginBottom: 16 }}>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => {
            setEditingDept(null);
            setModalOpen(true);
          }}
        >
          添加部门
        </Button>
      </div>
      <Table
        columns={columns}
        dataSource={departments.map((d) => ({ key: d.id, ...d }))}
        size="small"
        pagination={false}
        locale={{ emptyText: "暂无部门" }}
      />
      <DepartmentFormModal
        open={modalOpen}
        editingDept={editingDept}
        onClose={() => setModalOpen(false)}
        onSubmit={handleSubmit}
        loading={createMutation.isPending || updateMutation.isPending}
      />
    </>
  );
}

export default function PersonaDashboardPage() {
  const { data: personas, isLoading } = usePersonas();
  const { data: departments } = useDepartments();
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingPersona, setEditingPersona] = useState<PersonaInfo | null>(null);
  const [activeTab, setActiveTab] = useState("personas");
  const [deptFilter, setDeptFilter] = useState<string | null>(null);
  const { data: routingRules } = useRoutingRules();

  const createMutation = useCreatePersona();
  const updateMutation = useUpdatePersona();
  const deleteMutation = useDeletePersona();

  const filteredPersonas = useMemo(() => {
    if (!personas) return [];
    if (!deptFilter) return personas;
    return personas.filter((p) => p.department === deptFilter);
  }, [personas, deptFilter]);

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

  const handleSubmit = (raw: PersonaCreateRequest | PersonaUpdateRequest) => {
    // Normalize empty string to null for llm_config_name
    const values = { ...raw, llm_config_name: raw.llm_config_name || null };
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
          departments={departments ?? []}
          onClose={() => setModalOpen(false)}
          onSubmit={handleSubmit}
          loading={createMutation.isPending}
        />
      </PageContainer>
    );
  }

  const deptFilterOptions = [
    { value: "", label: "全部部门" },
    ...(departments ?? []).map((d) => ({ value: d.id, label: d.name })),
  ];

  return (
    <PageContainer
      title="百官阁"
      extra={
        <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>
          添加官员
        </Button>
      }
    >
      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
          {
            key: "personas",
            label: "百官列表",
            children: (
              <>
                <div style={{ marginBottom: 16 }}>
                  <Select
                    value={deptFilter ?? ""}
                    onChange={(v) => setDeptFilter(v || null)}
                    options={deptFilterOptions}
                    style={{ width: 200 }}
                    placeholder="按部门筛选"
                  />
                </div>
                <Row gutter={[16, 16]}>
                  {filteredPersonas.map((persona) => (
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
                  {filteredPersonas.length === 0 && (
                    <Col span={24}>
                      <Empty description="该部门下无官员" />
                    </Col>
                  )}
                </Row>
              </>
            ),
          },
          {
            key: "departments",
            label: (
              <Space>
                <ApartmentOutlined />
                部门管理
              </Space>
            ),
            children: (
              <DepartmentTab
                departments={departments ?? []}
                personas={personas}
              />
            ),
          },
          {
            key: "routing",
            label: (
              <Space>
                <NodeIndexOutlined />
                路由规则
              </Space>
            ),
            children: (
              <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                <Card title="默认任务映射" size="small">
                  <Table
                    columns={[
                      { title: "任务类型", dataIndex: "task_type", key: "task_type", width: 120 },
                      {
                        title: "状态",
                        key: "status",
                        width: 100,
                        render: (_: unknown, r: { is_fallback?: boolean; preferred_department?: string }) =>
                          r.is_fallback ? (
                            <Tooltip title={`缺少 ${r.preferred_department} 部门的官员，已回退`}>
                              <Tag icon={<WarningOutlined />} color="warning">回退</Tag>
                            </Tooltip>
                          ) : (
                            <Tag color="success">正常</Tag>
                          ),
                      },
                      { title: "官员 ID", dataIndex: "persona_id", key: "persona_id", width: 120,
                        render: (v: string) => <Tag color="blue">{v}</Tag> },
                      { title: "官员名称", dataIndex: "name", key: "name", width: 120 },
                      { title: "部门", dataIndex: "department", key: "department", width: 120,
                        render: (v: string) => <Tag>{v}</Tag> },
                      {
                        title: "期望部门",
                        dataIndex: "preferred_department",
                        key: "preferred_department",
                        width: 120,
                        render: (v: string, r: { is_fallback?: boolean }) =>
                          r.is_fallback ? <Tag color="red">{v}</Tag> : <Tag>{v}</Tag>,
                      },
                    ]}
                    dataSource={routingRules?.default_map
                      ? Object.entries(routingRules.default_map).map(([taskType, rule]) => ({
                          key: taskType,
                          task_type: taskType,
                          ...rule,
                        }))
                      : []}
                    size="small"
                    pagination={false}
                    locale={{ emptyText: "暂无映射" }}
                  />
                </Card>

                <Card title="关键词匹配规则" size="small">
                  <Table
                    columns={[
                      { title: "官员 ID", dataIndex: "persona_id", key: "persona_id", width: 120,
                        render: (v: string) => <Tag color="blue">{v}</Tag> },
                      { title: "官员名称", dataIndex: "name", key: "name", width: 120 },
                      { title: "部门", dataIndex: "department", key: "department", width: 120,
                        render: (v: string) => <Tag>{v}</Tag> },
                      { title: "关键词", dataIndex: "keywords", key: "keywords",
                        render: (v: string[]) => v.map((kw) => <Tag key={kw} color="green">{kw}</Tag>) },
                    ]}
                    dataSource={routingRules?.keyword_map
                      ? Object.entries(routingRules.keyword_map).map(([personaId, rule]) => ({
                          key: personaId,
                          persona_id: personaId,
                          ...(rule as { name: string; department: string; keywords: string[] }),
                        }))
                      : []}
                    size="small"
                    pagination={false}
                    locale={{ emptyText: "暂无规则" }}
                  />
                </Card>

                {routingRules?.delegation_chains && routingRules.delegation_chains.length > 0 && (
                  <Card title="委派链路" size="small">
                    <Table
                      columns={[
                        { title: "委派方", dataIndex: "from_name", key: "from_name" },
                        { title: "委派方 ID", dataIndex: "from_id", key: "from_id", width: 120,
                          render: (v: string) => <Tag color="blue">{v}</Tag> },
                        { title: "可委派至", dataIndex: "delegates_to", key: "delegates_to",
                          render: (v: string[]) => v.map((d) => <Tag key={d} color="purple">{d}</Tag>) },
                      ]}
                      dataSource={routingRules.delegation_chains.map((d) => ({ key: d.from_id, ...d }))}
                      size="small"
                      pagination={false}
                    />
                  </Card>
                )}
              </Space>
            ),
          },
        ]}
      />

      <PersonaFormModal
        open={modalOpen}
        editingPersona={editingPersona}
        personas={personas}
        departments={departments ?? []}
        onClose={() => setModalOpen(false)}
        onSubmit={handleSubmit}
        loading={createMutation.isPending || updateMutation.isPending}
      />
    </PageContainer>
  );
}
