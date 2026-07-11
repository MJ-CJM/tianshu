import type {
  ApiResponse,
  DefaultPricingTable,
  EffectivePricing,
  PluginInfo,
  ProviderInfo,
  ProviderPricingUpdate,
} from "./types";
import { authFetch } from "./authFetch";

const API_BASE = import.meta.env.VITE_API_BASE || "";

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await authFetch(`${API_BASE}${url}`, init);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function getProviders(): Promise<ProviderInfo[]> {
  const resp = await fetchJson<ApiResponse<ProviderInfo[]>>("/api/providers");
  return resp.data ?? [];
}

export async function createProvider(
  data: Partial<ProviderInfo>,
): Promise<ProviderInfo> {
  const resp = await fetchJson<ApiResponse<ProviderInfo>>("/api/providers", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return resp.data!;
}

export async function updateProvider(
  name: string,
  data: Partial<ProviderInfo>,
): Promise<ProviderInfo> {
  const resp = await fetchJson<ApiResponse<ProviderInfo>>(
    `/api/providers/${name}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    },
  );
  return resp.data!;
}

export async function deleteProvider(name: string): Promise<void> {
  await fetchJson(`/api/providers/${name}`, { method: "DELETE" });
}

// --- Provider pricing (3 维：input miss / input hit / output) ---

export async function getEffectivePricing(name: string): Promise<EffectivePricing> {
  const resp = await fetchJson<ApiResponse<EffectivePricing>>(
    `/api/providers/${encodeURIComponent(name)}/pricing/effective`,
  );
  return resp.data!;
}

export async function updateProviderPricing(
  name: string,
  body: ProviderPricingUpdate,
): Promise<EffectivePricing> {
  const resp = await fetchJson<ApiResponse<EffectivePricing>>(
    `/api/providers/${encodeURIComponent(name)}/pricing`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  return resp.data!;
}

export async function resetProviderPricing(name: string): Promise<EffectivePricing> {
  const resp = await fetchJson<ApiResponse<EffectivePricing>>(
    `/api/providers/${encodeURIComponent(name)}/pricing`,
    { method: "DELETE" },
  );
  return resp.data!;
}

export async function getDefaultPricingTable(): Promise<DefaultPricingTable> {
  const resp = await fetchJson<ApiResponse<DefaultPricingTable>>(
    "/api/providers/pricing/defaults",
  );
  return resp.data!;
}

export async function getPlugins(): Promise<PluginInfo[]> {
  const resp = await fetchJson<ApiResponse<PluginInfo[]>>("/api/plugins");
  return resp.data ?? [];
}

export async function installPlugin(
  data: Record<string, unknown>,
): Promise<void> {
  await fetchJson("/api/plugins/install", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}
