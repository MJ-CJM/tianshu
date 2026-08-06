import { useEffect, useRef } from "react";
import { App } from "antd";
import { useNavigate } from "react-router-dom";
import type { WsMessage } from "../api/types";
import type { WsListener } from "./useWebSocket";
import { useT } from "../i18n";

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
 * - tool.approval_required → warning 提示，点击跳转到该敕令详情页就地裁决
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
  const t = useT();
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

      // 裁决完成 → 清理该 memorial 的去重缓存 + 关闭已弹出的 toast
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

        // Server-side downgrade always→once notice (bash tools don't support always)
        if (type === "decree.approved" && payload.grant_downgraded) {
          notification.info({
            message: t("comp.policyToast.downgradedTitle"),
            description:
              payload.grant_downgrade_reason ??
              t("comp.policyToast.downgradedDescDefault", {
                tool: toolName || t("comp.policyToast.fallbackTool"),
              }),
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
          message: t("comp.policyToast.approvalRequired"),
          description: `${toolName || "tool"}: ${payload.reason ?? ""}`,
          duration: 0,
          onClick: () => {
            // 跳到该敕令详情页就地裁决（详情页已内联「工具待裁决」卡）；
            // 仅在拿不到 edict_id 时回退到 /approvals 全局队列。
            const eid = msg.edict_id as string | undefined;
            navigate(eid ? `/edicts/${eid}` : "/approvals");
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
          message: t("comp.policyToast.policyDeny"),
          description: `${toolName || "tool"}: ${payload.reason ?? ""}`,
          duration: 5,
        });
        setTimeout(() => {
          seenRef.current.delete(dedupKey);
        }, 6000);
      }
    };

    return subscribe(handle);
  }, [subscribe, notification, navigate, t]);
}
