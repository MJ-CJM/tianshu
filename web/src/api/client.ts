import axios from "axios";
import { notification } from "antd";

const apiClient = axios.create({
  baseURL: "/api",
  timeout: 30_000,
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
  (error) => {
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
