import apiClient from './client';
import type { ApiResponse, DAGExecution } from './types';

export async function getDagByEdict(edictId: string): Promise<ApiResponse<DAGExecution> | null> {
  try {
    const { data } = await apiClient.get(`/dag/by-edict/${edictId}`, { silentCodes: [404] } as any);
    return data;
  } catch (err: any) {
    // 404 = 该敕令尚无 DAG，属于正常状态
    if (err.response?.status === 404) return null;
    throw err;
  }
}

export async function getDag(dagId: string): Promise<ApiResponse<DAGExecution>> {
  const { data } = await apiClient.get(`/dag/${dagId}`);
  return data;
}

export async function cancelDag(dagId: string): Promise<ApiResponse<{ dag_id: string; cancelled_nodes: string[] }>> {
  const { data } = await apiClient.post(`/dag/${dagId}/cancel`);
  return data;
}

export async function retryDag(dagId: string, fromNodeIds?: string[]): Promise<ApiResponse<{ dag_id: string; reset_node_ids: string[] }>> {
  const { data } = await apiClient.post(`/dag/${dagId}/retry`, { from_node_ids: fromNodeIds });
  return data;
}

export async function getWorkers(): Promise<ApiResponse<{ active: string[] }>> {
  const { data } = await apiClient.get('/workers');
  return data;
}

export async function getWorkersStatus(): Promise<ApiResponse<any>> {
  const { data } = await apiClient.get('/workers/status');
  return data;
}
