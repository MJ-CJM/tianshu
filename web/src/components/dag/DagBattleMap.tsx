import { useMemo } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  type Node,
  type Edge,
  MarkerType,
  BackgroundVariant,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { Empty } from 'antd';
import DagNodeComponent from './DagNode';
import DagToolbar from './DagToolbar';
import WorkerPanel from './WorkerPanel';
import type { DAGExecution, DAGNode as DAGNodeType } from '../../api/types';
import { usePersonas } from '../../hooks/usePersonas';
import { useDepartments } from '../../hooks/useDepartments';
import { useT } from "../../i18n";
import { useThemeMode, type ThemeMode } from '../../hooks/useTheme';
import { palettes } from '../../theme/palette';

const nodeTypes = { dagNode: DagNodeComponent };

// React Flow 的 marker / MiniMap 走 SVG 属性取色,CSS 变量不生效,
// 因此按当前主题从调色板取字面值。
function edgeColors(mode: ThemeMode): Record<string, string> {
  const p = palettes[mode];
  return {
    pending: p.borderHover,
    ready: p.status.running,
    running: p.warning,
    completed: p.status.completed,
    failed: p.status.failed,
    cancelled: p.status.cancelled,
  };
}

interface OfficialMeta {
  name: string;
  dept: string;
}

interface DagBattleMapProps {
  execution: DAGExecution | null | undefined;
  poolStatus?: any;
  laneStatus?: any;
  /** 规划预演模式:节点虚线、隐藏工况面板、显示预演横幅 */
  preview?: boolean;
  onCancel?: () => void;
  onRetry?: () => void;
  cancelLoading?: boolean;
  retryLoading?: boolean;
}

function layoutNodes(
  dagNodes: DAGNodeType[],
  colors: Record<string, string>,
  fallbackColor: string,
  officials: Map<string, OfficialMeta>,
  preview: boolean,
): { nodes: Node[]; edges: Edge[] } {
  const depthMap = new Map<string, number>();
  const nodeMap = new Map(dagNodes.map((n) => [n.node_id, n]));

  function getDepth(nodeId: string, visited = new Set<string>()): number {
    if (depthMap.has(nodeId)) return depthMap.get(nodeId)!;
    if (visited.has(nodeId)) return 0;
    visited.add(nodeId);
    const node = nodeMap.get(nodeId);
    if (!node || !node.depends_on?.length) {
      depthMap.set(nodeId, 0);
      return 0;
    }
    const maxParent = Math.max(...node.depends_on.map((d) => getDepth(d, visited)));
    const depth = maxParent + 1;
    depthMap.set(nodeId, depth);
    return depth;
  }

  dagNodes.forEach((n) => getDepth(n.node_id));

  const layers = new Map<number, DAGNodeType[]>();
  dagNodes.forEach((n) => {
    const d = depthMap.get(n.node_id) || 0;
    if (!layers.has(d)) layers.set(d, []);
    layers.get(d)!.push(n);
  });

  const X_GAP = 300;
  const Y_GAP = 160;
  const nodes: Node[] = [];
  const edges: Edge[] = [];

  layers.forEach((layerNodes, depth) => {
    const startX = -(layerNodes.length - 1) * X_GAP / 2;
    layerNodes.forEach((n, i) => {
      const official = n.assigned_official ? officials.get(n.assigned_official) : undefined;
      nodes.push({
        id: n.node_id,
        type: 'dagNode',
        position: { x: startX + i * X_GAP, y: depth * Y_GAP },
        data: {
          label: n.description,
          status: n.status,
          nodeId: n.node_id,
          assignedOfficial: n.assigned_official,
          officialName: official?.name,
          officialDept: official?.dept,
          error: n.error,
          preview,
        },
      });

      (n.depends_on || []).forEach((depId) => {
        edges.push({
          id: `${depId}-${n.node_id}`,
          source: depId,
          target: n.node_id,
          type: 'smoothstep',
          animated: n.status === 'running' || n.status === 'ready',
          style: {
            stroke: colors[n.status] || fallbackColor,
            strokeWidth: 1.5,
            ...(preview ? { strokeDasharray: '6 4' } : {}),
          },
          markerEnd: {
            type: MarkerType.ArrowClosed,
            color: colors[n.status] || fallbackColor,
            width: 16,
            height: 16,
          },
        });
      });
    });
  });

  return { nodes, edges };
}

export default function DagBattleMap({
  execution,
  poolStatus,
  laneStatus,
  preview = false,
  onCancel,
  onRetry,
  cancelLoading,
  retryLoading,
}: DagBattleMapProps) {
  const t = useT();
  const mode = useThemeMode();
  const colors = useMemo(() => edgeColors(mode), [mode]);
  const fallbackColor = palettes[mode].borderHover;
  const { data: personas } = usePersonas();
  const { data: departments } = useDepartments();

  // 官员 id → 姓名/部门中文名(落款用);部门名去掉英文括注
  const officials = useMemo(() => {
    const deptNames = new Map<string, string>(
      (departments ?? []).map((d: any) => [d.id, String(d.name || d.id).split('(')[0]!.trim()]),
    );
    const map = new Map<string, OfficialMeta>();
    (personas ?? []).forEach((p: any) => {
      map.set(p.id, {
        name: p.name,
        dept: deptNames.get(p.department) || p.department_name || p.department,
      });
    });
    return map;
  }, [personas, departments]);

  const { nodes: layoutedNodes, edges: layoutedEdges } = useMemo(
    () => layoutNodes(execution?.nodes || [], colors, fallbackColor, officials, preview),
    [execution?.nodes, colors, fallbackColor, officials, preview],
  );

  if (!execution) {
    return <Empty description={t("comp.dag.battleEmpty")} />;
  }

  return (
    <div style={{ width: '100%', height: '100%', display: 'flex', flexDirection: 'column' }}>
      {preview ? (
        <div
          style={{
            padding: '6px 14px',
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            borderBottom: '1px dashed var(--ts-color-border)',
            background: 'color-mix(in srgb, var(--ts-color-accent) 4%, var(--ts-color-bg-container))',
          }}
        >
          <span
            style={{
              fontSize: 12,
              fontWeight: 600,
              color: 'var(--ts-color-accent)',
              letterSpacing: '0.04em',
            }}
          >
            {t("comp.dag.previewBadge")}
          </span>
          <span style={{ fontSize: 12, color: 'var(--ts-color-text-secondary)' }}>
            {t("comp.dag.previewHint")}
          </span>
        </div>
      ) : (
        <DagToolbar
          dagId={execution.id}
          status={execution.status}
          onCancel={onCancel}
          onRetry={onRetry}
          cancelLoading={cancelLoading}
          retryLoading={retryLoading}
        />
      )}
      <div style={{ flex: 1, position: 'relative' }}>
        <ReactFlow
          nodes={layoutedNodes}
          edges={layoutedEdges}
          nodeTypes={nodeTypes}
          fitView
          fitViewOptions={{ padding: 0.2 }}
          minZoom={0.3}
          maxZoom={2}
          proOptions={{ hideAttribution: true }}
        >
          <Background variant={BackgroundVariant.Dots} gap={20} size={1} />
          <Controls showInteractive={false} />
          <MiniMap
            nodeColor={(n) => colors[(n.data as any)?.status] || fallbackColor}
            style={{ height: 80 }}
            pannable
            zoomable
          />
        </ReactFlow>
        {!preview && (
          <div style={{ position: 'absolute', top: 8, right: 8, zIndex: 10 }}>
            <WorkerPanel poolStatus={poolStatus} laneStatus={laneStatus} />
          </div>
        )}
      </div>
    </div>
  );
}
