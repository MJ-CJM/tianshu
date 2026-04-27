/** 提供方计价配置卡片 —— 列出每个 provider 的 3 维生效价（input miss/hit/output）。
 *
 * 行为：
 * - 列：name, model, miss¥/1K, hit¥/1K, out¥/1K, 来源, 操作
 * - 来源 badge：custom（自定义）/ default（默认价表）/ mixed（部分自定义）
 * - 编辑 Modal：3 个 InputNumber + "重置默认"按钮
 */

import { useEffect, useState } from "react";
import {
  Button,
  Card,
  InputNumber,
  Modal,
  Popconfirm,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import { EditOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  getDefaultPricingTable,
  getEffectivePricing,
  getProviders,
  resetProviderPricing,
  updateProviderPricing,
} from "../../api/providers";
import type {
  DefaultPricingTable,
  EffectivePricing,
  ProviderInfo,
  ProviderPricingUpdate,
} from "../../api/types";

interface RowData extends ProviderInfo {
  effective: EffectivePricing | null;
}

const SOURCE_LABEL: Record<string, { color: string; text: string }> = {
  custom: { color: "purple", text: "自定义" },
  mixed: { color: "blue", text: "部分自定义" },
  default: { color: "default", text: "默认价表" },
};

function formatPrice(p: number | null): string {
  if (p === null || p === undefined) return "—";
  return p.toFixed(5).replace(/0+$/, "").replace(/\.$/, "");
}

export default function ProviderPricingCard() {
  const [rows, setRows] = useState<RowData[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<RowData | null>(null);
  const [form, setForm] = useState<ProviderPricingUpdate>({});
  const [saving, setSaving] = useState(false);
  const [defaultTableOpen, setDefaultTableOpen] = useState(false);
  const [defaultTable, setDefaultTable] = useState<DefaultPricingTable | null>(null);

  const refresh = async () => {
    setLoading(true);
    try {
      const providers = await getProviders();
      const enriched = await Promise.all(
        providers.map(async (p) => {
          try {
            const eff = await getEffectivePricing(p.name);
            return { ...p, effective: eff };
          } catch {
            return { ...p, effective: null };
          }
        }),
      );
      setRows(enriched);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  const openEdit = (row: RowData) => {
    setEditing(row);
    setForm({
      cost_per_1k_prompt: row.cost_per_1k_prompt,
      cost_per_1k_cache_read: row.cost_per_1k_cache_read,
      cost_per_1k_completion: row.cost_per_1k_completion,
    });
  };

  const handleSave = async () => {
    if (!editing) return;
    setSaving(true);
    try {
      await updateProviderPricing(editing.name, form);
      message.success("已保存");
      setEditing(null);
      await refresh();
    } catch (e) {
      message.error("保存失败：" + String(e));
    } finally {
      setSaving(false);
    }
  };

  const handleReset = async (name: string) => {
    try {
      await resetProviderPricing(name);
      message.success("已重置为默认价表");
      await refresh();
    } catch (e) {
      message.error("重置失败：" + String(e));
    }
  };

  const columns = [
    { title: "名称", dataIndex: "name", key: "name" },
    { title: "模型", dataIndex: "model", key: "model" },
    {
      title: "Input miss ¥/1K",
      key: "miss",
      render: (_: unknown, r: RowData) => formatPrice(r.effective?.miss ?? null),
    },
    {
      title: "Input hit ¥/1K",
      key: "hit",
      render: (_: unknown, r: RowData) => formatPrice(r.effective?.hit ?? null),
    },
    {
      title: "Output ¥/1K",
      key: "out",
      render: (_: unknown, r: RowData) => formatPrice(r.effective?.out ?? null),
    },
    {
      title: "来源",
      key: "source",
      render: (_: unknown, r: RowData) => {
        const src = r.effective?.source ?? "default";
        const meta = SOURCE_LABEL[src] ?? SOURCE_LABEL.default!;
        return <Tag color={meta.color}>{meta.text}</Tag>;
      },
    },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, r: RowData) => (
        <Space size="small">
          <Button
            size="small"
            icon={<EditOutlined />}
            onClick={() => openEdit(r)}
          >
            编辑
          </Button>
          <Popconfirm
            title="重置为默认价表？"
            description="清空自定义 3 维价，回退到 _DEFAULT_PRICING。"
            onConfirm={() => handleReset(r.name)}
          >
            <Button size="small" icon={<ReloadOutlined />} danger>
              重置
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const openDefaultTable = async () => {
    if (!defaultTable) {
      try {
        const t = await getDefaultPricingTable();
        setDefaultTable(t);
      } catch (e) {
        message.error("加载默认价表失败：" + String(e));
        return;
      }
    }
    setDefaultTableOpen(true);
  };

  return (
    <Card
      size="small"
      title="提供方计价"
      extra={
        <Space size="small">
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            每千 token CNY；空白字段落默认价表
          </Typography.Text>
          <Button size="small" onClick={openDefaultTable}>查看默认价表</Button>
        </Space>
      }
      style={{ marginTop: 16 }}
    >
      {loading ? (
        <Spin />
      ) : (
        <Table
          dataSource={rows}
          columns={columns}
          rowKey="name"
          pagination={false}
          size="small"
        />
      )}

      <Modal
        title={`编辑「${editing?.name}」三维价`}
        open={!!editing}
        onCancel={() => setEditing(null)}
        onOk={handleSave}
        confirmLoading={saving}
        okText="保存"
        cancelText="取消"
      >
        <Space direction="vertical" style={{ width: "100%" }} size="middle">
          <div>
            <Typography.Text>Input ¥/1K（缓存未中）</Typography.Text>
            <InputNumber
              min={0}
              step={0.0001}
              style={{ width: "100%", marginTop: 4 }}
              value={form.cost_per_1k_prompt ?? undefined}
              onChange={(v) =>
                setForm((s) => ({ ...s, cost_per_1k_prompt: v ?? null }))
              }
              placeholder="留空 = 用默认价表"
            />
          </div>
          <div>
            <Typography.Text>
              Input ¥/1K（缓存命中折扣价）
            </Typography.Text>
            <InputNumber
              min={0}
              step={0.00001}
              style={{ width: "100%", marginTop: 4 }}
              value={form.cost_per_1k_cache_read ?? undefined}
              onChange={(v) =>
                setForm((s) => ({ ...s, cost_per_1k_cache_read: v ?? null }))
              }
              placeholder="留空 = 与未中价相同（无折扣）"
            />
          </div>
          <div>
            <Typography.Text>Output ¥/1K</Typography.Text>
            <InputNumber
              min={0}
              step={0.001}
              style={{ width: "100%", marginTop: 4 }}
              value={form.cost_per_1k_completion ?? undefined}
              onChange={(v) =>
                setForm((s) => ({ ...s, cost_per_1k_completion: v ?? null }))
              }
              placeholder="留空 = 用默认价表"
            />
          </div>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            提示：留空字段会落回 _DEFAULT_PRICING。修改立即生效，下次 LLM 调用按新价计费。
          </Typography.Text>
        </Space>
      </Modal>

      <Modal
        title="默认价表 (_DEFAULT_PRICING)"
        open={defaultTableOpen}
        onCancel={() => setDefaultTableOpen(false)}
        footer={null}
        width={720}
      >
        <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
          provider 自定义价格未填字段时，按 model 名匹配此表。匹配不到则用兜底价。
        </Typography.Paragraph>
        <Table
          dataSource={defaultTable?.entries ?? []}
          rowKey="model"
          size="small"
          pagination={false}
          columns={[
            { title: "Model", dataIndex: "model", key: "model" },
            {
              title: "Input miss ¥/1K",
              dataIndex: "miss",
              key: "miss",
              render: (v: number) => formatPrice(v),
              align: "right" as const,
            },
            {
              title: "Input hit ¥/1K",
              dataIndex: "hit",
              key: "hit",
              render: (v: number) => formatPrice(v),
              align: "right" as const,
            },
            {
              title: "Output ¥/1K",
              dataIndex: "out",
              key: "out",
              render: (v: number) => formatPrice(v),
              align: "right" as const,
            },
          ]}
        />
        {defaultTable?.fallback && (
          <Typography.Paragraph
            type="secondary"
            style={{ fontSize: 12, marginTop: 12 }}
          >
            兜底价（未命中表时）：
            miss <code>{formatPrice(defaultTable.fallback.miss)}</code>，
            hit <code>{formatPrice(defaultTable.fallback.hit)}</code>，
            out <code>{formatPrice(defaultTable.fallback.out)}</code>
          </Typography.Paragraph>
        )}
      </Modal>
    </Card>
  );
}
