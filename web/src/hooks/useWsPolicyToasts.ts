import { useEffect } from "react";
import { App } from "antd";
import { useNavigate } from "react-router-dom";
import type { WsMessage } from "../api/types";

interface ToastPayload {
  tool_name?: string;
  reason?: string;
  rule_id?: string;
  verdict?: string;
}

/**
 * 订阅 WebSocket 中的 policy 事件并弹出 AntD notification toast。
 *
 * - tool.approval_required → warning 提示，点击跳转到批红台
 * - policy.decision (verdict=deny) → error 提示，5 秒后自动关闭
 */
export function useWsPolicyToasts(lastMessage: WsMessage | null): void {
  const { notification } = App.useApp();
  const navigate = useNavigate();

  useEffect(() => {
    if (!lastMessage) return;
    const type = lastMessage.type;
    const payload = (lastMessage.payload ?? {}) as ToastPayload;

    if (type === "tool.approval_required") {
      notification.warning({
        message: "需要审批",
        description: `${payload.tool_name ?? "tool"}: ${payload.reason ?? ""}`,
        duration: 0,
        onClick: () => {
          navigate("/approvals");
          notification.destroy();
        },
      });
      return;
    }

    if (type === "policy.decision" && payload.verdict === "deny") {
      notification.error({
        message: "Policy 拒绝",
        description: `${payload.tool_name ?? "tool"}: ${payload.reason ?? ""}`,
        duration: 5,
      });
    }
  }, [lastMessage, notification, navigate]);
}
