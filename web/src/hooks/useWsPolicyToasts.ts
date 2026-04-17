import { useEffect, useRef } from "react";
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
 *
 * 去重策略：以 (type, memorial_id, tool_name) 为键，Agent 迭代中重复触发同一条
 *   approval_required 不再堆叠 toast。decree.approved / decree.rejected 到达时
 *   清理对应 key，避免下一次审批被误判为重复。
 */
export function useWsPolicyToasts(lastMessage: WsMessage | null): void {
  const { notification } = App.useApp();
  const navigate = useNavigate();
  const seenRef = useRef<Set<string>>(new Set());
  const notifKeyRef = useRef<Map<string, string>>(new Map());

  useEffect(() => {
    if (!lastMessage) return;
    const type = lastMessage.type;
    const memorialId = (lastMessage.memorial_id as string | undefined) ?? "";
    const payload = (lastMessage.payload ?? {}) as ToastPayload;
    const toolName = payload.tool_name ?? "";

    // 审批解除 → 清理该 memorial 的去重缓存 + 关闭已弹出的 toast
    if (
      memorialId &&
      (type === "decree.approved" || type === "decree.rejected")
    ) {
      for (const key of Array.from(seenRef.current)) {
        if (key.startsWith(`approval:${memorialId}:`)) {
          seenRef.current.delete(key);
          const nkey = notifKeyRef.current.get(key);
          if (nkey) {
            notification.destroy(nkey);
            notifKeyRef.current.delete(key);
          }
        }
      }
      return;
    }

    if (type === "tool.approval_required") {
      const dedupKey = `approval:${memorialId}:${toolName}`;
      if (seenRef.current.has(dedupKey)) return;
      seenRef.current.add(dedupKey);

      const notifKey = `approval-${memorialId}-${toolName}-${Date.now()}`;
      notifKeyRef.current.set(dedupKey, notifKey);
      notification.warning({
        key: notifKey,
        message: "需要审批",
        description: `${toolName || "tool"}: ${payload.reason ?? ""}`,
        duration: 0,
        onClick: () => {
          navigate("/approvals");
          notification.destroy(notifKey);
        },
      });
      return;
    }

    if (type === "policy.decision" && payload.verdict === "deny") {
      const dedupKey = `deny:${memorialId}:${toolName}:${payload.reason ?? ""}`;
      if (seenRef.current.has(dedupKey)) return;
      seenRef.current.add(dedupKey);
      notification.error({
        message: "Policy 拒绝",
        description: `${toolName || "tool"}: ${payload.reason ?? ""}`,
        duration: 5,
      });
      // deny toast 5s 后自动关闭——同步清理缓存，避免相同原因再次触发被屏蔽
      setTimeout(() => {
        seenRef.current.delete(dedupKey);
      }, 6000);
    }
  }, [lastMessage, notification, navigate]);
}
