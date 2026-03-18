export type TaskStatus =
  | "submitted"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

export interface UsageSummary {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}

export type EdictStatus = "open" | "completed" | "cancelled";

export interface Edict {
  id: string;
  title: string;
  goal: string;
  context: string | null;
  status: EdictStatus;
  created_at: string;
}

export interface Memorial {
  id: string;
  edict_id: string;
  instruction: string | null;
  status: TaskStatus;
  summary: string | null;
  result: string | null;
  usage: UsageSummary;
  error: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface EdictEvent {
  id: string;
  event_type: string;
  created_at: string;
  edict_id: string;
  memorial_id: string | null;
  payload: Record<string, unknown>;
}

export interface EdictUpdateRequest {
  title?: string;
  goal?: string;
  context?: string;
}

export interface LLMConfig {
  name: string;
  model: string;
  api_key_masked: string;
  api_base: string;
  max_retries: number;
  temperature: number;
  top_p: number;
  max_tokens: number;
  enabled: boolean;
}

export interface LLMConfigCreateRequest {
  name: string;
  model: string;
  api_key?: string;
  api_base?: string;
  max_retries?: number;
  temperature?: number;
  top_p?: number;
  max_tokens?: number;
  enabled?: boolean;
}

export interface LLMConfigUpdateRequest {
  model?: string;
  api_key?: string;
  api_base?: string;
  max_retries?: number;
  temperature?: number;
  top_p?: number;
  max_tokens?: number;
  enabled?: boolean;
}

export interface LLMConfigListResponse {
  configs: LLMConfig[];
  active_name: string;
}

export interface AgentConfig {
  agent_max_iterations: number;
  agent_timeout_seconds: number;
  skills_char_budget: number;
}

export interface AgentConfigUpdateRequest {
  agent_max_iterations?: number;
  agent_timeout_seconds?: number;
  skills_char_budget?: number;
}

export interface ApiResponse<T> {
  success: boolean;
  data: T | null;
  error: string | null;
  metadata: {
    total: number;
    limit: number;
    offset: number;
  } | null;
}
