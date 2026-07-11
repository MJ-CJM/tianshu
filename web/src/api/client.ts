import axios from "axios";
import { notification } from "antd";
import { refreshAuthSession } from "./authFetch";

const apiClient = axios.create({
  baseURL: "/api",
  timeout: 30_000,
  withCredentials: true,
  headers: { "Content-Type": "application/json" },
});

apiClient.interceptors.response.use(
  (response) => {
    const body = response.data;
    if (body && body.success === false && body.error) {
      notification.error({
        message: "请求失败",
        description: body.error,
        placement: "topRight",
      });
    }
    return response;
  },
  async (error) => {
    const original = error.config as (typeof error.config & { _authRetried?: boolean }) | undefined;
    const url = String(original?.url ?? "");
    if (
      error.response?.status === 401 &&
      original &&
      !original._authRetried &&
      !url.includes("/auth/session") &&
      !url.includes("/auth/refresh")
    ) {
      original._authRetried = true;
      if (await refreshAuthSession()) return apiClient.request(original);
    }
    // 允许请求通过 config.silentCodes 标记可静默的 HTTP 状态码
    const silentCodes: number[] = error.config?.silentCodes ?? [];
    if (silentCodes.includes(error.response?.status)) {
      return Promise.reject(error);
    }
    const message =
      error.response?.data?.detail ??
      error.response?.data?.error ??
      error.message ??
      "网络错误";
    notification.error({
      message: "请求异常",
      description: String(message),
      placement: "topRight",
    });
    return Promise.reject(error);
  },
);

export default apiClient;
