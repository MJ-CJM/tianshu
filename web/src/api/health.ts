import axios from "axios";

const healthClient = axios.create({
  baseURL: "",
  timeout: 5_000,
});

export interface HealthStatus {
  status: string;
}

export async function getHealth(): Promise<HealthStatus> {
  const { data } = await healthClient.get<HealthStatus>("/health");
  return data;
}
