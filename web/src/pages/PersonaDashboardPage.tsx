import { useState, useMemo, useEffect } from "react";
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
  Radio,
  Collapse,
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
import {
  usePersonaTemplates,
  usePersonaTemplate,
} from "../hooks/usePersonaTemplates";
import type { TemplateLang } from "../api/personaTemplates";
import { useRoutingRules } from "../hooks/useOps";
import { useConfigs } from "../hooks/useConfig";
import { isApiProblem } from "../api/client";
import type {
  PersonaInfo,
  PersonaCreateRequest,
  PersonaUpdateRequest,
  DepartmentInfo,
  DepartmentCreateRequest,
  DepartmentUpdateRequest,
} from "../api/types";
import { useT } from "../i18n";

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
  const t = useT();
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
          {persona.title && (
            <Typography.Text
              type="secondary"
              style={{ fontSize: 12, fontWeight: "normal" }}
            >
              · {persona.title}
            </Typography.Text>
          )}
          <Tag color="blue">{persona.department_name ?? persona.department}</Tag>
          {persona.can_delegate && (
            <Tag icon={<CheckCircleOutlined />} color="green">
              {t("persona.card.delegate")}
            </Tag>
          )}
          {persona.memory_global_read && (
            <Tag color="orange">
              {t("persona.card.globalRead")}
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
            title={t("persona.confirm.deletePersona")}
            description={t("persona.confirm.deletePersonaDesc")}
            onConfirm={onDelete}
            okText={t("common.confirm")}
            cancelText={t("common.cancel")}
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
          {t("persona.card.toolPermission")}
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
              ? t("persona.tier.tier0")
              : persona.tool_tier_max === 1
                ? t("persona.tier.tier1")
                : t("persona.tier.tierN", { n: persona.tool_tier_max })}
          </Typography.Text>
        </div>
      </div>

      {/* Whitelisted tools (if any) */}
      {persona.tools_allowed.length > 0 && (
        <div style={{ marginBottom: 8 }}>
          <Typography.Text style={{ fontSize: 12, color: token.colorTextSecondary }}>
            {t("persona.card.specifiedTools")}
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
            {t("persona.card.deniedTools")}
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
          {t("persona.card.skills")}
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
              {t("persona.card.allSkills")}
            </Typography.Text>
          )}
        </div>
      </div>

      {/* Delegation */}
      {persona.delegates_to.length > 0 && (
        <div style={{ marginBottom: 8 }}>
          <Typography.Text style={{ fontSize: 12, color: token.colorTextSecondary }}>
            {t("persona.card.delegatesTo")}
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
            {t("persona.card.llmConfig")}
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
                    title={t("persona.metric.total")}
                    value={metrics.total_executions}
                    valueStyle={{ fontSize: 18 }}
                  />
                </Col>
                <Col span={12}>
                  <Statistic
                    title={t("persona.metric.completed")}
                    value={metrics.completed}
                    valueStyle={{ fontSize: 18, color: token.colorSuccess }}
                  />
                </Col>
              </Row>
              <Row gutter={16}>
                <Col span={12}>
                  <Statistic
                    title={t("persona.metric.failed")}
                    value={metrics.failed}
                    valueStyle={{ fontSize: 18, color: token.colorError }}
                  />
                </Col>
                <Col span={12}>
                  <div>
                    <Typography.Text
                      style={{ fontSize: 12, color: token.colorTextSecondary }}
                    >
                      {t("persona.metric.successRate")}
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
                    title={t("persona.metric.totalTokens")}
                    value={metrics.total_tokens}
                    valueStyle={{ fontSize: 16 }}
                  />
                </Col>
                <Col span={12}>
                  <Statistic
                    title={t("persona.metric.avgTokens")}
                    value={metrics.avg_tokens_per_execution}
                    valueStyle={{ fontSize: 16 }}
                  />
                </Col>
              </Row>
              <Row gutter={16}>
                <Col span={12}>
                  <Statistic
                    title={t("persona.metric.totalCost")}
                    value={metrics.total_cost_cny}
                    prefix="¥"
                    precision={4}
                    valueStyle={{ fontSize: 16 }}
                  />
                </Col>
                <Col span={12}>
                  <Statistic
                    title={t("persona.metric.avgDuration")}
                    value={metrics.avg_duration_seconds}
                    suffix="s"
                    precision={1}
                    valueStyle={{ fontSize: 16 }}
                  />
                </Col>
              </Row>
            </Space>
          ) : (
            <Typography.Text type="secondary">{t("persona.metric.empty")}</Typography.Text>
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
  const t = useT();
  const [form] = Form.useForm();
  const isEdit = !!editingPersona;
  const { data: tools } = useTools();
  const { data: skills } = useSkills();
  const { data: configsData } = useConfigs();

  const llmConfigOptions = [
    { value: "", label: t("persona.form.persona.llmConfigGlobal") },
    ...(configsData?.configs ?? []).map((c) => ({
      value: c.name,
      label: `${c.name} (${c.model})`,
    })),
  ];
  const toolOptions = (tools ?? []).map((tool) => ({
    value: tool.name,
    label: `${tool.name} (tier ${tool.tier})`,
  }));
  const skillOptions = (skills ?? []).map((s) => ({
    value: s.name,
    label: `${s.name}${s.description ? ` — ${s.description}` : ""}`,
  }));
  const deptOptions = departments.map((d) => ({
    value: d.id,
    label: `${d.name} (${d.id})`,
  }));

  // 角色模板（仅创建态）：选语言 → 列模板 → 选中后预填 name 并预览 SOUL/ROLE
  const [templateLang, setTemplateLang] = useState<TemplateLang>("zh");
  const [selectedTemplateId, setSelectedTemplateId] = useState<string | null>(null);
  const { data: templateCats } = usePersonaTemplates(templateLang);
  const { data: templateDetail } = usePersonaTemplate(
    templateLang,
    selectedTemplateId,
  );
  const templateOptions = (templateCats ?? []).map((cat) => ({
    label: cat.category,
    options: cat.templates.map((tpl) => ({
      value: tpl.id,
      label: `${tpl.emoji ? `${tpl.emoji} ` : ""}${tpl.name}`,
    })),
  }));

  useEffect(() => {
    if (templateDetail) {
      form.setFieldsValue({ name: templateDetail.name });
    }
  }, [templateDetail, form]);

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
      setTemplateLang("zh");
      setSelectedTemplateId(null);
    }
  };

  const handleFinish = (values: PersonaCreateRequest | PersonaUpdateRequest) => {
    if (!isEdit && selectedTemplateId) {
      onSubmit({
        ...(values as PersonaCreateRequest),
        template_id: selectedTemplateId,
        template_lang: templateLang,
      });
    } else {
      onSubmit(values);
    }
  };

  return (
    <Modal
      title={isEdit ? t("persona.form.persona.editTitle") : t("persona.form.persona.addTitle")}
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
        onFinish={handleFinish}
        initialValues={{
          tool_tier_max: 0,
          can_delegate: false,
          memory_global_read: false,
          tools_allowed: [],
          tools_denied: [],
          skills_allowed: [],
          delegates_to: [],
        }}
      >
        {!isEdit && (
          <>
            <Form.Item label={t("persona.form.persona.field.templateLang")}>
              <Radio.Group
                value={templateLang}
                onChange={(e) => {
                  setTemplateLang(e.target.value);
                  setSelectedTemplateId(null);
                }}
                optionType="button"
                options={[
                  { value: "zh", label: "中文" },
                  { value: "en", label: "English" },
                ]}
              />
            </Form.Item>
            <Form.Item
              label={t("persona.form.persona.field.template")}
              tooltip={t("persona.form.persona.tooltip.template")}
            >
              <Select
                allowClear
                showSearch
                optionFilterProp="label"
                placeholder={t("persona.form.persona.placeholder.template")}
                value={selectedTemplateId ?? undefined}
                onChange={(v) => setSelectedTemplateId(v ?? null)}
                options={templateOptions}
              />
            </Form.Item>
            {templateDetail && (
              <Collapse
                size="small"
                style={{ marginBottom: 16 }}
                items={[
                  {
                    key: "soul",
                    label: t("persona.form.persona.soulPreview"),
                    children: (
                      <Typography.Paragraph
                        style={{ whiteSpace: "pre-wrap", maxHeight: 220, overflow: "auto", margin: 0 }}
                      >
                        {templateDetail.soul_preview}
                      </Typography.Paragraph>
                    ),
                  },
                  {
                    key: "role",
                    label: t("persona.form.persona.rolePreview"),
                    children: (
                      <Typography.Paragraph
                        style={{ whiteSpace: "pre-wrap", maxHeight: 220, overflow: "auto", margin: 0 }}
                      >
                        {templateDetail.role_preview}
                      </Typography.Paragraph>
                    ),
                  },
                ]}
              />
            )}
          </>
        )}
        {!isEdit && (
          <Form.Item
            name="id"
            label={t("persona.form.persona.field.id")}
            rules={[
              { required: true, message: t("persona.form.persona.validation.idRequired") },
              { pattern: /^[a-z][a-z0-9_]*$/, message: t("persona.form.persona.validation.idPattern") },
            ]}
          >
            <Input placeholder={t("persona.form.persona.placeholder.id")} />
          </Form.Item>
        )}
        <Form.Item
          name="name"
          label={t("persona.form.persona.field.name")}
          rules={[{ required: true, message: t("persona.form.persona.validation.nameRequired") }]}
        >
          <Input placeholder={t("persona.form.persona.placeholder.name")} />
        </Form.Item>
        <Form.Item
          name="title"
          label={t("persona.form.persona.field.title")}
          rules={[{ max: 32, message: t("persona.form.persona.validation.titleMax") }]}
          tooltip={t("persona.form.persona.tooltip.title")}
        >
          <Input placeholder={t("persona.form.persona.placeholder.title")} maxLength={32} />
        </Form.Item>
        <Form.Item
          name="department"
          label={t("persona.form.persona.field.department")}
          rules={[{ required: true, message: t("persona.form.persona.validation.departmentRequired") }]}
        >
          <Select
            placeholder={t("persona.form.persona.placeholder.department")}
            options={deptOptions}
            showSearch
            optionFilterProp="label"
          />
        </Form.Item>
        <Form.Item name="llm_config_name" label={t("persona.form.persona.field.llmConfig")}>
          <Select
            placeholder={t("persona.form.persona.placeholder.llmConfig")}
            options={llmConfigOptions}
            allowClear
          />
        </Form.Item>
        <Form.Item name="tools_allowed" label={t("persona.form.persona.field.toolsAllowed")}>
          <Select
            mode="multiple"
            placeholder={t("persona.form.persona.placeholder.toolsAllowed")}
            options={toolOptions}
            showSearch
            optionFilterProp="label"
          />
        </Form.Item>
        <Form.Item name="tools_denied" label={t("persona.form.persona.field.toolsDenied")}>
          <Select
            mode="multiple"
            placeholder={t("persona.form.persona.placeholder.toolsDenied")}
            options={toolOptions}
            showSearch
            optionFilterProp="label"
          />
        </Form.Item>
        <Form.Item name="skills_allowed" label={t("persona.form.persona.field.skills")}>
          <Select
            mode="multiple"
            placeholder={t("persona.form.persona.placeholder.skills")}
            options={skillOptions}
            showSearch
            optionFilterProp="label"
          />
        </Form.Item>
        <Form.Item name="tool_tier_max" label={t("persona.form.persona.field.tierMax")}>
          <InputNumber min={0} max={10} style={{ width: "100%" }} />
        </Form.Item>
        <Form.Item name="can_delegate" label={t("persona.form.persona.field.canDelegate")} valuePropName="checked">
          <Switch />
        </Form.Item>
        <Form.Item name="memory_global_read" label={t("persona.form.persona.field.memoryGlobalRead")} valuePropName="checked" extra={t("persona.form.persona.fieldHint.memoryGlobalRead")}>
          <Switch />
        </Form.Item>
        <Form.Item name="delegates_to" label={t("persona.form.persona.field.delegatesTo")}>
          <Select
            mode="multiple"
            placeholder={t("persona.form.persona.placeholder.delegatesTo")}
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
  const t = useT();
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
      title={isEdit ? t("persona.form.department.editTitle") : t("persona.form.department.addTitle")}
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
            label={t("persona.form.department.field.id")}
            rules={[
              { required: true, message: t("persona.form.department.validation.idRequired") },
              { pattern: /^[a-z][a-z0-9_]*$/, message: t("persona.form.department.validation.idPattern") },
            ]}
          >
            <Input placeholder={t("persona.form.department.placeholder.id")} />
          </Form.Item>
        )}
        <Form.Item
          name="name"
          label={t("persona.form.department.field.name")}
          rules={[{ required: true, message: t("persona.form.department.validation.nameRequired") }]}
        >
          <Input placeholder={t("persona.form.department.placeholder.name")} />
        </Form.Item>
        <Form.Item name="description" label={t("persona.form.department.field.description")}>
          <Input.TextArea rows={3} placeholder={t("persona.form.department.placeholder.description")} />
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
  const t = useT();
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
            notification.success({ message: t("persona.toast.deptUpdated") });
            setModalOpen(false);
          },
        },
      );
    } else {
      createMutation.mutate(values as DepartmentCreateRequest, {
        onSuccess: () => {
          notification.success({ message: t("persona.toast.deptCreated") });
          setModalOpen(false);
        },
      });
    }
  };

  const handleDelete = (id: string) => {
    deleteMutation.mutate(id, {
      onSuccess: () => notification.success({ message: t("persona.toast.deptDeleted") }),
      onError: (err: unknown) => {
        notification.error({
          message: isApiProblem(err) ? err.message : t("persona.toast.deptDeleteFailed"),
        });
      },
    });
  };

  const columns = [
    { title: t("persona.department.table.id"), dataIndex: "id", key: "id", width: 120 },
    { title: t("persona.department.table.name"), dataIndex: "name", key: "name", width: 200 },
    { title: t("persona.department.table.description"), dataIndex: "description", key: "description", ellipsis: true },
    {
      title: t("persona.department.table.personaCount"),
      key: "persona_count",
      width: 100,
      render: (_: unknown, record: DepartmentInfo) => (
        <Tag color={personaCountMap[record.id] ? "blue" : "default"}>
          {personaCountMap[record.id] ?? 0}
        </Tag>
      ),
    },
    {
      title: t("persona.department.table.actions"),
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
            title={t("persona.confirm.deleteDept")}
            description={t("persona.confirm.deleteDeptDesc")}
            onConfirm={() => handleDelete(record.id)}
            okText={t("common.confirm")}
            cancelText={t("common.cancel")}
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
          {t("persona.action.addDept")}
        </Button>
      </div>
      <Table
        columns={columns}
        dataSource={departments.map((d) => ({ key: d.id, ...d }))}
        size="small"
        pagination={false}
        locale={{ emptyText: t("persona.empty.noDepartments") }}
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
  const t = useT();
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
      onSuccess: () => notification.success({ message: t("persona.toast.personaDeleted") }),
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
            notification.success({ message: t("persona.toast.personaUpdated") });
            setModalOpen(false);
          },
        },
      );
    } else {
      createMutation.mutate(values as PersonaCreateRequest, {
        onSuccess: () => {
          notification.success({ message: t("persona.toast.personaCreated") });
          setModalOpen(false);
        },
      });
    }
  };

  if (isLoading) {
    return (
      <PageContainer title={t("persona.title")}>
        <div style={{ textAlign: "center", padding: 48 }}>
          <Spin size="large" />
        </div>
      </PageContainer>
    );
  }

  if (!personas || personas.length === 0) {
    return (
      <PageContainer
        title={t("persona.title")}
        extra={
          <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>
            {t("persona.action.addPersona")}
          </Button>
        }
      >
        <Empty description={t("persona.empty.noPersonas")} />
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
    { value: "", label: t("persona.filter.deptAll") },
    ...(departments ?? []).map((d) => ({ value: d.id, label: d.name })),
  ];

  return (
    <PageContainer
      title={t("persona.title")}
      extra={
        <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>
          {t("persona.action.addPersona")}
        </Button>
      }
    >
      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
          {
            key: "personas",
            label: t("persona.tab.personas"),
            children: (
              <>
                <div style={{ marginBottom: 16 }}>
                  <Select
                    value={deptFilter ?? ""}
                    onChange={(v) => setDeptFilter(v || null)}
                    options={deptFilterOptions}
                    style={{ width: 200 }}
                    placeholder={t("persona.filter.byDept")}
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
                      <Empty description={t("persona.empty.noDeptPersonas")} />
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
                {t("persona.tab.departments")}
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
                {t("persona.tab.routing")}
              </Space>
            ),
            children: (
              <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                <Card title={t("persona.routing.defaultMapTitle")} size="small">
                  <Table
                    columns={[
                      { title: t("persona.routing.table.taskType"), dataIndex: "task_type", key: "task_type", width: 120 },
                      {
                        title: t("persona.routing.table.status"),
                        key: "status",
                        width: 100,
                        render: (_: unknown, r: { is_fallback?: boolean; preferred_department?: string }) =>
                          r.is_fallback ? (
                            <Tooltip title={t("persona.routing.fallbackTooltip", { dept: r.preferred_department ?? "" })}>
                              <Tag icon={<WarningOutlined />} color="warning">{t("persona.routing.fallback")}</Tag>
                            </Tooltip>
                          ) : (
                            <Tag color="success">{t("persona.routing.normal")}</Tag>
                          ),
                      },
                      { title: t("persona.routing.table.personaId"), dataIndex: "persona_id", key: "persona_id", width: 120,
                        render: (v: string) => <Tag color="blue">{v}</Tag> },
                      { title: t("persona.routing.table.personaName"), dataIndex: "name", key: "name", width: 120 },
                      { title: t("persona.routing.table.department"), dataIndex: "department", key: "department", width: 120,
                        render: (v: string) => <Tag>{v}</Tag> },
                      {
                        title: t("persona.routing.table.preferredDept"),
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
                    locale={{ emptyText: t("persona.empty.noMapping") }}
                  />
                </Card>

                <Card title={t("persona.routing.keywordMapTitle")} size="small">
                  <Table
                    columns={[
                      { title: t("persona.routing.table.personaId"), dataIndex: "persona_id", key: "persona_id", width: 120,
                        render: (v: string) => <Tag color="blue">{v}</Tag> },
                      { title: t("persona.routing.table.personaName"), dataIndex: "name", key: "name", width: 120 },
                      { title: t("persona.routing.table.department"), dataIndex: "department", key: "department", width: 120,
                        render: (v: string) => <Tag>{v}</Tag> },
                      { title: t("persona.routing.table.keywords"), dataIndex: "keywords", key: "keywords",
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
                    locale={{ emptyText: t("persona.empty.noRules") }}
                  />
                </Card>

                {routingRules?.delegation_chains && routingRules.delegation_chains.length > 0 && (
                  <Card title={t("persona.routing.delegationChainTitle")} size="small">
                    <Table
                      columns={[
                        { title: t("persona.routing.table.fromName"), dataIndex: "from_name", key: "from_name" },
                        { title: t("persona.routing.table.fromId"), dataIndex: "from_id", key: "from_id", width: 120,
                          render: (v: string) => <Tag color="blue">{v}</Tag> },
                        { title: t("persona.routing.table.delegatesTo"), dataIndex: "delegates_to", key: "delegates_to",
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
