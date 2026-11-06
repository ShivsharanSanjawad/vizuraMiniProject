import { useEffect, useMemo } from 'react';
import { ReactFlow, Background, Controls, MiniMap, MarkerType, useNodesState, type Edge, type Node } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import type { Graph, Task } from '../types';
import { Empty } from './ui';
export default function DependencyGraph({graph, tasks}: {graph?: Graph; tasks: Task[]}) {
  const model = useMemo(() => {
    const source = graph?.visualization_graph;
    const nodes: Node[] = (source?.nodes || []).map((n, i) => {
      const task = tasks.find(t => t.task_id === n.id); const status = task?.status || n.data.status || 'pending';
      const critical = graph?.bottlenecks.some(b => b.entity_id === n.id && b.blocks_count > 1);
      const color = status === 'completed' ? '#47765b' : status === 'blocked' ? '#b4544b' : critical ? '#8b713a' : '#d4ccbd';
      return {id: n.id, position: {x: (i % 3) * 280, y: Math.floor(i / 3) * 170}, data: {label: <div><small>{n.data.is_external_blocker ? 'EXTERNAL BLOCKER' : n.id} ? {status}</small><p>{task?.description || n.data.description || n.id}</p>{critical && <small>Critical blocker</small>}</div>}, style: {borderColor: color, borderWidth: 2, background: '#fcfaf5', width: 240, padding: 12}};
    });
    const edges: Edge[] = (source?.edges || []).map((e, i) => {
      const task = tasks.find(t => t.task_id === e.target); const inferred = !!task?.inferred_prerequisites?.includes(e.source) && !task?.explicit_prerequisites?.includes(e.source);
      return {id: 'edge-'+i, source: e.source, target: e.target, label: inferred ? 'inferred' : undefined, style: {stroke: '#8b713a', strokeDasharray: inferred ? '5 4' : undefined}, markerEnd: {type: MarkerType.ArrowClosed, color: '#8b713a'}};
    });
    return {nodes, edges};
  }, [graph, tasks]);
  const [nodes, setNodes, onNodesChange] = useNodesState(model.nodes);
  useEffect(() => setNodes(model.nodes), [model, setNodes]);
  if (!model.nodes.length) return <Empty title="No dependency graph available">Process action items to see their relationships.</Empty>;
  return <>{!model.edges.length && <p className="notice">No dependencies were detected. Individual tasks are shown below.</p>}<p className="muted">Drag nodes, pan and zoom. Dashed edges indicate inferred dependencies. Relationships are read-only.</p>{graph?.is_feasible === false && <div className="notice danger">Circular dependencies detected: {graph.circular_dependencies.map(c => `${c.from} ? ${c.to}`).join(', ')}</div>}<div className="graph"><ReactFlow nodes={nodes} edges={model.edges} onNodesChange={onNodesChange} nodesConnectable={false} edgesReconnectable={false} fitView><Background color="#d4ccbd"/><MiniMap/><Controls showInteractive={false}/></ReactFlow></div>{graph && <div className="bottlenecks">{graph.bottlenecks.map(b => <span key={b.entity_id}>{b.entity_id}: blocks {b.blocks_count} {b.blocks_count===1 ? 'task' : 'tasks'}</span>)}</div>}</>;
}
