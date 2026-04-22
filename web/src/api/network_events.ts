import apiClient from "./client";
import type { NetworkEventRow } from "./types";

export interface ListNetworkEventsParams {
  limit?: number;
  tool?: string;
  host?: string;
  status?: "ok" | "error";
}

export async function listNetworkEvents(
  params: ListNetworkEventsParams = {},
): Promise<NetworkEventRow[]> {
  const query = new URLSearchParams();
  if (params.limit) query.set("limit", String(params.limit));
  if (params.tool) query.set("tool", params.tool);
  if (params.host) query.set("host", params.host);
  if (params.status) query.set("status", params.status);
  const qs = query.toString();
  const { data } = await apiClient.get<NetworkEventRow[]>(
    `/audit/network-events${qs ? "?" + qs : ""}`,
  );
  return data;
}
