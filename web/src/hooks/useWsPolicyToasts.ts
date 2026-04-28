import { useEffect, useRef } from "react";
import { App } from "antd";
import { useNavigate } from "react-router-dom";
import type { WsMessage } from "../api/types";
import type { WsListener } from "./useWebSocket";

interface ToastPayload {
  tool_name?: string;
  reason?: string;
  rule_id?: string;
  verdict?: string;
  grant_scope?: string;
  requested_grant_scope?: string;
  grant_downgraded?: boolean;
  grant_downgrade_reason?: string;
}

/**
 * 订阅 WebSocket 中的 policy 事件并弹出 AntD notification toast。
 *
 * - tool.approval_required → warning 提示，点击跳转到批红台
 * - policy.decision (verdict=deny) → error 提示，5 秒后自动关闭
 * - decree.approved (grant_downgraded=true) → info 提示"已降级为本次"
 *
 * 改造说明（2026-04-28）：
 * 之前用 `lastMessage` 单值依赖 + useEffect，相邻两条 WS 消息（如
 * decree.approved → tool.approval_required 仅相隔 80ms）会被 React 18 自动批处理
 * 合并/覆盖，导致中间消息的 effect 跑不到、dedup 缓存不被清理，新消息被误判为重复。
 *
 * 现在改用 `subscribe(listener)`：listener 在 ws.onmessage 同步触发，
 * 每条消息按到达顺序被处理，无 race。
 */
export function useWsPolicyToasts(
  subscribe: (listener: WsListener) => () => void,
): void {
  const { notification } = App.useApp();
  const navigate = useNavigate();
  const seenRef = useRef<Set<string>>(new Set());
  const notifKeyRef = useRef<Map<string, string>>(new Map());

  useEffect(() => {
    const handle = (msg: WsMessage) => {
      const type = msg.type;
      const memorialId = (msg.memorial_id as string | undefined) ?? "";
      const payload = (msg.payload ?? {}) as ToastPayload;
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

        // 服务端降级 always→once 的提示（bash 类工具不支持 always）
        if (type === "decree.approved" && payload.grant_downgraded) {
          notification.info({
            message: "永久授权已降级为本次",
            description:
              payload.grant_downgrade_reason ??
              `${toolName || "该工具"} 不支持 always 授权，已降级为 once。`,
            duration: 6,
          });
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
    };

    return subscribe(handle);
  }, [subscribe, notification, navigate]);
}
