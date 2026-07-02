import dagre from "dagre";
import type { Edge, Node } from "@xyflow/svelte";

export type StepKind =
  | "generate"
  | "analyze"
  | "report"
  | "wait"
  | "notify"
  | "script";

export interface PipelineStepDSL {
  name: string;
  kind: string;
  order?: number;
  config: Record<string, unknown>;
  depends_on?: string[];
  max_retries?: number;
}

export interface PipelineConfig {
  steps: PipelineStepDSL[];
}

export interface WorkflowNodeData extends Record<string, unknown> {
  name: string;
  kind: StepKind;
  config: Record<string, unknown>;
  max_retries: number;
  runStatus?: string;
}

export type WorkflowNode = Node<WorkflowNodeData, StepKind>;

const NODE_WIDTH = 280;
const NODE_HEIGHT = 160;

export function pipelineToFlow(config: PipelineConfig | undefined | null): {
  nodes: WorkflowNode[];
  edges: Edge[];
} {
  const steps = config?.steps ?? [];
  const nodes: WorkflowNode[] = steps.map((step) => {
    const cfg = (step.config ?? {}) as Record<string, unknown>;
    const pos = cfg._position as { x: number; y: number } | undefined;
    return {
      id: step.name,
      type: (step.kind as StepKind) ?? "script",
      position:
        pos && typeof pos.x === "number" && typeof pos.y === "number"
          ? { x: pos.x, y: pos.y }
          : { x: 0, y: 0 },
      data: {
        name: step.name,
        kind: (step.kind as StepKind) ?? "script",
        config: cfg,
        max_retries: step.max_retries ?? 0,
      },
    };
  });

  const edges: Edge[] = [];
  steps.forEach((step) => {
    (step.depends_on ?? []).forEach((dep) => {
      edges.push({
        id: `${dep}->${step.name}`,
        source: dep,
        target: step.name,
        animated: true,
        type: "smoothstep",
      });
    });
  });

  const allMissingPositions =
    nodes.length > 0 &&
    nodes.every((n) => {
      const cfg = (n.data.config ?? {}) as Record<string, unknown>;
      return !cfg._position;
    });
  if (allMissingPositions) {
    autoLayout(nodes, edges);
  }
  return { nodes, edges };
}

export function flowToPipeline(
  nodes: WorkflowNode[],
  edges: Edge[],
): PipelineConfig {
  const depMap = new Map<string, string[]>();
  edges.forEach((e) => {
    if (!depMap.has(e.target)) depMap.set(e.target, []);
    depMap.get(e.target)!.push(e.source);
  });

  const steps: PipelineStepDSL[] = nodes.map((node, i) => {
    const cfg = { ...(node.data.config ?? {}) } as Record<string, unknown>;
    cfg._position = {
      x: Math.round(node.position.x),
      y: Math.round(node.position.y),
    };
    return {
      name: node.data.name || node.id,
      kind: node.data.kind ?? (node.type as StepKind) ?? "script",
      order: i,
      config: cfg,
      depends_on: depMap.get(node.id) ?? [],
      max_retries: node.data.max_retries ?? 0,
    };
  });
  return { steps };
}

export function autoLayout(nodes: WorkflowNode[], edges: Edge[]): void {
  if (!nodes.length) return;
  const g = new dagre.graphlib.Graph();
  g.setGraph({
    rankdir: "LR",
    nodesep: 80,
    ranksep: 140,
    marginx: 40,
    marginy: 40,
  });
  g.setDefaultEdgeLabel(() => ({}));

  nodes.forEach((n) => {
    g.setNode(n.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
  });
  edges.forEach((e) => {
    g.setEdge(e.source, e.target);
  });

  dagre.layout(g);

  nodes.forEach((n) => {
    const dn = g.node(n.id);
    if (dn) {
      n.position = { x: dn.x - NODE_WIDTH / 2, y: dn.y - NODE_HEIGHT / 2 };
    }
  });
}

export function uniqueStepName(existing: Set<string>, kind: string): string {
  let i = 1;
  while (existing.has(`${kind}_${i}`)) i += 1;
  return `${kind}_${i}`;
}

export function makeNode(
  kind: StepKind,
  existingIds: Iterable<string>,
  position: { x: number; y: number },
): WorkflowNode {
  const name = uniqueStepName(new Set(existingIds), kind);
  return {
    id: name,
    type: kind,
    position,
    data: {
      name,
      kind,
      config: defaultConfigFor(kind),
      max_retries: 0,
    },
  };
}

export function validateFlow(nodes: WorkflowNode[]): string[] {
  const errors: string[] = [];
  const names = new Set<string>();
  nodes.forEach((n, idx) => {
    const name = (n.data.name ?? "").trim();
    if (!name) errors.push(`Node ${idx + 1}: name is required`);
    if (name && names.has(name)) errors.push(`Duplicate step name: "${name}"`);
    if (name) names.add(name);
  });
  return errors;
}

export function defaultConfigFor(kind: StepKind): Record<string, unknown> {
  switch (kind) {
    case "generate":
      return { app_num: 1 };
    case "analyze":
      return { analyzers: ["bandit"], live_target: false };
    case "report":
      return { report_type: "comprehensive" };
    case "wait":
      return { seconds: 5 };
    case "notify":
      return { channel: "general", message: "" };
    case "script":
      return { code: "noop" };
    default:
      return {};
  }
}
