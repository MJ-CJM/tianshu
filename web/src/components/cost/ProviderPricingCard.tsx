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
import { useT } from "../../i18n";
import PageQueryError from "../states/PageQueryError";

interface RowData extends ProviderInfo {
  effective: EffectivePricing | null;
  pricingUnavailable: boolean;
}

const SOURCE_COLOR: Record<string, string> = {
  custom: "purple",
  mixed: "blue",
  default: "default",
};

function formatPrice(p: number | null): string {
  if (p === null || p === undefined) return "—";
  return p.toFixed(5).replace(/0+$/, "").replace(/\.$/, "");
}

export default function ProviderPricingCard() {
  const t = useT();
  const [rows, setRows] = useState<RowData[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [editing, setEditing] = useState<RowData | null>(null);
  const [form, setForm] = useState<ProviderPricingUpdate>({});
  const [saving, setSaving] = useState(false);
  const [defaultTableOpen, setDefaultTableOpen] = useState(false);
  const [defaultTable, setDefaultTable] = useState<DefaultPricingTable | null>(
    null,
  );

  const refresh = async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const providers = await getProviders();
      const enriched = await Promise.all(
        providers.map(async (p) => {
          try {
            const eff = await getEffectivePricing(p.name);
            return { ...p, effective: eff, pricingUnavailable: false };
          } catch {
            return { ...p, effective: null, pricingUnavailable: true };
          }
        }),
      );
      setRows(enriched);
    } catch (error) {
      setLoadError(error);
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
      message.success(t("cost.pricing.saved"));
      setEditing(null);
      await refresh();
    } catch (e) {
      message.error(t("cost.pricing.saveFailed", { err: String(e) }));
    } finally {
      setSaving(false);
    }
  };

  const handleReset = async (name: string) => {
    try {
      await resetProviderPricing(name);
      message.success(t("cost.pricing.resetDone"));
      await refresh();
    } catch (e) {
      message.error(t("cost.pricing.resetFailed", { err: String(e) }));
    }
  };

  const columns = [
    { title: t("cost.pricing.name"), dataIndex: "name", key: "name" },
    { title: t("cost.pricing.model"), dataIndex: "model", key: "model" },
    {
      title: t("cost.pricing.miss"),
      key: "miss",
      render: (_: unknown, r: RowData) =>
        formatPrice(r.effective?.miss ?? null),
    },
    {
      title: t("cost.pricing.hit"),
      key: "hit",
      render: (_: unknown, r: RowData) => formatPrice(r.effective?.hit ?? null),
    },
    {
      title: t("cost.pricing.out"),
      key: "out",
      render: (_: unknown, r: RowData) => formatPrice(r.effective?.out ?? null),
    },
    {
      title: t("cost.pricing.source"),
      key: "source",
      render: (_: unknown, r: RowData) => {
        if (r.pricingUnavailable) {
          return <Tag color="red">{t("pageDataState.unavailableTitle")}</Tag>;
        }
        if (r.effective?.billing === "subscription") {
          return (
            <Tag color="purple">
              {t("cost.pricing.sourceLabel.subscription")}
            </Tag>
          );
        }
        const src = r.effective?.source ?? "default";
        const color = SOURCE_COLOR[src] ?? "default";
        return <Tag color={color}>{t(`cost.pricing.sourceLabel.${src}`)}</Tag>;
      },
    },
    {
      title: t("cost.pricing.actions"),
      key: "actions",
      render: (_: unknown, r: RowData) => (
        <Space size="small">
          <Button
            size="small"
            icon={<EditOutlined />}
            onClick={() => openEdit(r)}
          >
            {t("cost.pricing.edit")}
          </Button>
          <Popconfirm
            title={t("cost.pricing.resetConfirm")}
            description={t("cost.pricing.resetDesc")}
            onConfirm={() => handleReset(r.name)}
          >
            <Button size="small" icon={<ReloadOutlined />} danger>
              {t("cost.pricing.reset")}
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const openDefaultTable = async () => {
    if (!defaultTable) {
      try {
        const tbl = await getDefaultPricingTable();
        setDefaultTable(tbl);
      } catch (e) {
        message.error(t("cost.pricing.loadDefaultFailed", { err: String(e) }));
        return;
      }
    }
    setDefaultTableOpen(true);
  };

  return (
    <Card
      size="small"
      title={t("cost.pricing.title")}
      extra={
        <Space size="small">
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {t("cost.pricing.perKHint")}
          </Typography.Text>
          <Button size="small" onClick={openDefaultTable}>
            {t("cost.pricing.viewDefault")}
          </Button>
        </Space>
      }
      style={{ marginTop: 16 }}
    >
      {loadError ? (
        <PageQueryError error={loadError} onRetry={() => void refresh()} />
      ) : loading ? (
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
        title={t("cost.pricing.editTitle", { name: editing?.name ?? "" })}
        open={!!editing}
        onCancel={() => setEditing(null)}
        onOk={handleSave}
        confirmLoading={saving}
        okText={t("button.save")}
        cancelText={t("common.cancel")}
      >
        <Space direction="vertical" style={{ width: "100%" }} size="middle">
          <div>
            <Typography.Text>{t("cost.pricing.promptLabel")}</Typography.Text>
            <InputNumber
              min={0}
              step={0.0001}
              style={{ width: "100%", marginTop: 4 }}
              value={form.cost_per_1k_prompt ?? undefined}
              onChange={(v) =>
                setForm((s) => ({ ...s, cost_per_1k_prompt: v ?? null }))
              }
              placeholder={t("cost.pricing.promptPlaceholder")}
            />
          </div>
          <div>
            <Typography.Text>{t("cost.pricing.cacheLabel")}</Typography.Text>
            <InputNumber
              min={0}
              step={0.00001}
              style={{ width: "100%", marginTop: 4 }}
              value={form.cost_per_1k_cache_read ?? undefined}
              onChange={(v) =>
                setForm((s) => ({ ...s, cost_per_1k_cache_read: v ?? null }))
              }
              placeholder={t("cost.pricing.cachePlaceholder")}
            />
          </div>
          <div>
            <Typography.Text>{t("cost.pricing.outputLabel")}</Typography.Text>
            <InputNumber
              min={0}
              step={0.001}
              style={{ width: "100%", marginTop: 4 }}
              value={form.cost_per_1k_completion ?? undefined}
              onChange={(v) =>
                setForm((s) => ({ ...s, cost_per_1k_completion: v ?? null }))
              }
              placeholder={t("cost.pricing.promptPlaceholder")}
            />
          </div>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {t("cost.pricing.tip")}
          </Typography.Text>
        </Space>
      </Modal>

      <Modal
        title={t("cost.pricing.defaultTitle")}
        open={defaultTableOpen}
        onCancel={() => setDefaultTableOpen(false)}
        footer={null}
        width={480}
      >
        <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
          {t("cost.pricing.defaultIntro")}
        </Typography.Paragraph>
        {defaultTable && (
          <Typography.Paragraph style={{ fontSize: 13 }}>
            {t("cost.pricing.catalogSummary", {
              providerCount: defaultTable.provider_count,
              modelCount: defaultTable.model_count,
              generatedAt: defaultTable.generated_at,
              source: defaultTable.source,
            })}
          </Typography.Paragraph>
        )}
        {defaultTable?.fallback && (
          <Typography.Paragraph
            type="secondary"
            style={{ fontSize: 12, marginTop: 12 }}
          >
            {t("cost.pricing.fallback")}
            miss <code>{formatPrice(defaultTable.fallback.miss)}</code>, hit{" "}
            <code>{formatPrice(defaultTable.fallback.hit)}</code>, out{" "}
            <code>{formatPrice(defaultTable.fallback.out)}</code>
          </Typography.Paragraph>
        )}
      </Modal>
    </Card>
  );
}
