import apiClient from "./client";

export interface FeishuChannelConfig {
  app_id: string;
  app_secret: string | null; // null=不修改；""=清空；非空=替换
  domain: string;
  connection_mode: string;
  allowed_users: string;
  home_channel: string;
  encrypt_key: string;
  verification_token: string;
  bot_open_id: string;
  bot_name: string;
  webhook_path: string;
  ws_reconnect_interval: number;
  text_batch_delay: number;
  dedup_cache_size: number;
  assistant_persona_id: string;
  intent_llm_enabled: boolean;
  enable_edict_submission: boolean;
}

export interface FeishuChannelView extends Omit<FeishuChannelConfig, "app_secret"> {
  app_secret: string; // 总是掩码 "***" 或 ""
  _source: "db" | "env";
  _has_secret: boolean;
  _updated_at?: string;
}

export interface FeishuStatus {
  running: boolean;
  mode: string | null;
  app_id?: string;
}

export async function getFeishuChannel(): Promise<FeishuChannelView> {
  const { data } = await apiClient.get("/tongzheng/channels/feishu");
  return data.data;
}

export async function putFeishuChannel(
  config: FeishuChannelConfig,
): Promise<{ reloaded: boolean; reason: string }> {
  const { data } = await apiClient.put("/tongzheng/channels/feishu", config);
  return data.data;
}

export async function getFeishuStatus(): Promise<FeishuStatus> {
  const { data } = await apiClient.get("/tongzheng/channels/feishu/status");
  return data.data;
}

// ===================== Telegram（与飞书并列）=====================

export interface TelegramChannelConfig {
  bot_token: string | null; // null=不修改；""=清空；非空=替换
  connection_mode: string; // polling | webhook
  allowed_users: string;
  home_channel: string;
  webhook_path: string;
  webhook_secret: string;
  poll_timeout: number;
  text_batch_delay: number;
  dedup_cache_size: number;
  assistant_persona_id: string;
  enable_edict_submission: boolean;
}

export interface TelegramChannelView
  extends Omit<TelegramChannelConfig, "bot_token"> {
  bot_token: string; // 总是掩码 "***" 或 ""
  _source: "db" | "env";
  _has_secret: boolean;
  _updated_at?: string;
}

export interface TelegramStatus {
  running: boolean;
  mode: string | null;
}

export async function getTelegramChannel(): Promise<TelegramChannelView> {
  const { data } = await apiClient.get("/tongzheng/channels/telegram");
  return data.data;
}

export async function putTelegramChannel(
  config: TelegramChannelConfig,
): Promise<{ reloaded: boolean; reason: string }> {
  const { data } = await apiClient.put("/tongzheng/channels/telegram", config);
  return data.data;
}

export async function getTelegramStatus(): Promise<TelegramStatus> {
  const { data } = await apiClient.get("/tongzheng/channels/telegram/status");
  return data.data;
}

export interface PersonaSummary {
  id: string;
  name: string;
  department: string;
}

export async function listPersonas(): Promise<PersonaSummary[]> {
  const { data } = await apiClient.get("/tongzheng/personas");
  return data.data?.personas ?? [];
}

// ===================== 多实例（instances）=====================

export interface InstanceView {
  instance_id: string;
  channel_type: "feishu" | "telegram";
  label: string;
  enabled: boolean;
  _has_secret: boolean;
  updated_at: string;
  running: boolean;
  mode: string | null;
  [key: string]: unknown; // 展平的渠道配置字段
}

export interface InstanceCreate {
  channel_type: "feishu" | "telegram";
  label?: string;
  enabled?: boolean;
  config?: Record<string, unknown>;
  secret?: string;
}

export interface InstanceUpdate {
  label?: string;
  enabled?: boolean;
  config?: Record<string, unknown>;
  secret?: string;
}

export async function listInstances(): Promise<InstanceView[]> {
  const { data } = await apiClient.get("/tongzheng/instances");
  return data.data?.instances ?? [];
}

export async function getInstance(id: string): Promise<InstanceView> {
  const { data } = await apiClient.get(`/tongzheng/instances/${id}`);
  return data.data;
}

export async function createInstance(
  body: InstanceCreate,
): Promise<{ instance_id: string; reloaded: boolean; reason: string }> {
  const { data } = await apiClient.post("/tongzheng/instances", body);
  return data.data;
}

export async function updateInstance(
  id: string,
  body: InstanceUpdate,
): Promise<{ reloaded: boolean; reason: string }> {
  const { data } = await apiClient.put(`/tongzheng/instances/${id}`, body);
  return data.data;
}

export async function setInstanceEnabled(
  id: string,
  enabled: boolean,
): Promise<{ enabled: boolean }> {
  const { data } = await apiClient.patch(`/tongzheng/instances/${id}/enabled`, {
    enabled,
  });
  return data.data;
}

export async function deleteInstance(id: string): Promise<{ deleted: string }> {
  const { data } = await apiClient.delete(`/tongzheng/instances/${id}`);
  return data.data;
}

export async function getInstanceStatus(
  id: string,
): Promise<{ running: boolean; mode: string | null }> {
  const { data } = await apiClient.get(`/tongzheng/instances/${id}/status`);
  return data.data;
}
