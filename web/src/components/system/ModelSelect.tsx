import { useMemo } from "react";
import { AutoComplete, Input, theme } from "antd";
import type { DefaultOptionType } from "antd/es/select";
import {
  useModelProviders,
  useProviderModels,
} from "../../hooks/useModelProviders";
import type { CatalogModelEntry } from "../../api/types";
import { useT } from "../../i18n";

/** 目录模型下拉（带自由输入）——通用受控组件。
 *
 * - providerId 存在且该 provider has_catalog：AutoComplete，选项来自模型目录
 *   （id + context window / ¥miss/1K + 能力 emoji 徽标），支持子串过滤与手输任意值。
 * - providerId 为空或无目录：退化为普通 Input。
 */

interface ModelSelectProps {
  providerId?: string;
  value?: string;
  /** Form.Item 注入场景下由 Form 提供，故声明为可选 */
  onChange?: (v: string) => void;
  size?: "small" | "middle" | "large";
  placeholder?: string;
  style?: React.CSSProperties;
}

function formatContextWindow(n: number | null): string | null {
  if (!n) return null;
  if (n >= 1_000_000) {
    const m = n / 1_000_000;
    return `${Number.isInteger(m) ? m : m.toFixed(1)}M`;
  }
  if (n >= 1000) return `${Math.round(n / 1000)}K`;
  return String(n);
}

function capabilityBadges(m: CatalogModelEntry): string {
  let badges = "";
  if (m.tool_call) badges += "🔧";
  if (m.reasoning) badges += "✨";
  if (m.vision) badges += "👁";
  return badges;
}

export default function ModelSelect({
  providerId,
  value,
  onChange,
  size,
  placeholder,
  style,
}: ModelSelectProps) {
  const t = useT();
  const { token } = theme.useToken();
  const { data: providers } = useModelProviders();

  const provider = providerId
    ? providers?.find((p) => p.id === providerId)
    : undefined;
  const hasCatalog = !!provider?.has_catalog;

  const { data: models } = useProviderModels(
    hasCatalog ? providerId : undefined,
  );

  const options: DefaultOptionType[] = useMemo(
    () =>
      (models ?? []).map((m) => {
        const ctx = formatContextWindow(m.context_window);
        const price = m.pricing_cny_per_1k
          ? `¥${m.pricing_cny_per_1k.miss}/1K`
          : null;
        const meta = [ctx, price].filter(Boolean).join(" · ");
        const badges = capabilityBadges(m);
        return {
          value: m.id,
          searchText: `${m.id} ${m.name}`.toLowerCase(),
          label: (
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                gap: 8,
              }}
            >
              <span>
                {m.id}
                {badges && <span style={{ marginLeft: 6 }}>{badges}</span>}
              </span>
              {meta && (
                <span
                  style={{ fontSize: 11, color: token.colorTextTertiary }}
                >
                  {meta}
                </span>
              )}
            </div>
          ),
        };
      }),
    [models, token.colorTextTertiary],
  );

  const effectivePlaceholder =
    placeholder ?? t("system.providers.registry.modelPlaceholder");

  if (!hasCatalog) {
    return (
      <Input
        size={size}
        value={value}
        placeholder={effectivePlaceholder}
        style={style}
        onChange={(e) => onChange?.(e.target.value)}
      />
    );
  }

  return (
    <AutoComplete
      size={size}
      value={value}
      options={options}
      placeholder={effectivePlaceholder}
      style={{ width: "100%", ...style }}
      onChange={(v) => onChange?.(v)}
      filterOption={(input, option) =>
        String(option?.searchText ?? "").includes(input.toLowerCase())
      }
    />
  );
}
