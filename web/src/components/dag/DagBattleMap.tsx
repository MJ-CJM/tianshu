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

interface DagBattleMapProps {
  execution: DAGExecution | null | undefined;
  poolStatus?: any;
  laneStatus?: any;
  onCancel: () => void;
  onRetry: () => void;
  cancelLoading?: boolean;
  retryLoading?: boolean;
}

function layoutNodes(
  dagNodes: DAGNodeType[],
  colors: Record<string, string>,
  fallbackColor: string,
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
  const Y_GAP = 120;
  const nodes: Node[] = [];
  const edges: Edge[] = [];

  layers.forEach((layerNodes, depth) => {
    const startX = -(layerNodes.length - 1) * X_GAP / 2;
    layerNodes.forEach((n, i) => {
      nodes.push({
        id: n.node_id,
        type: 'dagNode',
        position: { x: startX + i * X_GAP, y: depth * Y_GAP },
        data: {
          label: n.description,
          status: n.status,
          assignedOfficial: n.assigned_official,
          error: n.error,
        },
      });

      (n.depends_on || []).forEach((depId) => {
        edges.push({
          id: `${depId}-${n.node_id}`,
          source: depId,
          target: n.node_id,
          animated: n.status === 'running',
          style: { stroke: colors[n.status] || fallbackColor, strokeWidth: 2 },
          markerEnd: { type: MarkerType.ArrowClosed, color: colors[n.status] || fallbackColor },
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
  onCancel,
  onRetry,
  cancelLoading,
  retryLoading,
}: DagBattleMapProps) {
  const t = useT();
  const mode = useThemeMode();
  const colors = useMemo(() => edgeColors(mode), [mode]);
  const fallbackColor = palettes[mode].borderHover;
  const { nodes: layoutedNodes, edges: layoutedEdges } = useMemo(
    () => layoutNodes(execution?.nodes || [], colors, fallbackColor),
    [execution?.nodes, colors, fallbackColor],
  );

  if (!execution) {
    return <Empty description={t("comp.dag.battleEmpty")} />;
  }

  return (
    <div style={{ width: '100%', height: '100%', display: 'flex', flexDirection: 'column' }}>
      <DagToolbar
        dagId={execution.id}
        status={execution.status}
        onCancel={onCancel}
        onRetry={onRetry}
        cancelLoading={cancelLoading}
        retryLoading={retryLoading}
      />
      <div style={{ flex: 1, position: 'relative' }}>
        <ReactFlow
          nodes={layoutedNodes}
          edges={layoutedEdges}
          nodeTypes={nodeTypes}
          fitView
          fitViewOptions={{ padding: 0.2 }}
          minZoom={0.3}
          maxZoom={2}
        >
          <Background variant={BackgroundVariant.Dots} gap={16} size={1} />
          <Controls />
          <MiniMap
            nodeColor={(n) => colors[(n.data as any)?.status] || fallbackColor}
            style={{ height: 80 }}
          />
        </ReactFlow>
        <div style={{ position: 'absolute', top: 8, right: 8, zIndex: 10 }}>
          <WorkerPanel poolStatus={poolStatus} laneStatus={laneStatus} />
        </div>
      </div>
    </div>
  );
}
