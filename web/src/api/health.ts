import axios from "axios";

const healthClient = axios.create({
  baseURL: "",
  timeout: 5_000,
});

export type ReadinessState = "ready" | "degraded" | "not_ready";

export interface ReadinessStatus {
  schema_version: string;
  status: ReadinessState;
  profile?: string;
}

export async function getReadiness(): Promise<ReadinessStatus> {
  // 503（not_ready）也携带可解析 body，不作为传输错误抛出
  const { data } = await healthClient.get<ReadinessStatus>("/health/ready", {
    validateStatus: (code) => code === 200 || code === 503,
  });
  return data;
}
