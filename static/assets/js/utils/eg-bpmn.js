(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }
  root.PSOPEgBpmn = api;
})(typeof globalThis !== "undefined" ? globalThis : window, function () {
  const BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL";
  const BPMNDI_NS = "http://www.omg.org/spec/BPMN/20100524/DI";
  const DC_NS = "http://www.omg.org/spec/DD/20100524/DC";
  const DI_NS = "http://www.omg.org/spec/DD/20100524/DI";

  function unwrapArtifact(payload) {
    if (payload && typeof payload === "object" && payload.artifact && typeof payload.artifact === "object") {
      return payload.artifact;
    }
    return payload && typeof payload === "object" ? payload : {};
  }

  function escapeXml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&apos;");
  }

  function actorName(actor) {
    if (typeof actor === "string") {
      return actor;
    }
    if (!actor || typeof actor !== "object") {
      return "";
    }
    if (typeof actor.name === "string") {
      return actor.name;
    }
    if (actor.type === "llm") {
      return "agent.llm";
    }
    if (actor.type === "tool") {
      return "capability.demo_tool";
    }
    if (actor.type === "runtime" && typeof actor.operation === "string") {
      return `runtime.${actor.operation}`;
    }
    return "";
  }

  function buildWorkflowMap(artifact) {
    const steps = artifact?.runtime_contract?.workflow_steps;
    const map = new Map();
    if (!Array.isArray(steps)) {
      return map;
    }
    for (const step of steps) {
      if (step && typeof step === "object" && typeof step.id === "string") {
        map.set(step.id, step);
      }
    }
    return map;
  }

  function normalizeXmlId(prefix, value, index) {
    const safe = String(value || "node")
      .normalize("NFKD")
      .replace(/[\u0300-\u036f]/g, "")
      .replace(/[^A-Za-z0-9_]+/g, "_")
      .replace(/^_+|_+$/g, "")
      .slice(0, 48);
    const suffix = safe && !/^[0-9]/.test(safe) ? safe : `id_${safe || index}`;
    return `${prefix}_${index}_${suffix}`;
  }

  function normalizeNodes(artifact) {
    const workflowMap = buildWorkflowMap(artifact);
    const rawNodes = Array.isArray(artifact.nodes) ? artifact.nodes : [];
    return rawNodes
      .filter((node) => node && typeof node === "object" && typeof node.id === "string" && node.id)
      .map((node, index) => {
        const workflowStep = workflowMap.get(node.id);
        return {
          id: node.id,
          bpmnId: normalizeXmlId("EGNode", node.id, index + 1),
          label: workflowStep?.title || node.label || node.title || node.id,
          kind: node.kind || "task",
          actor: actorName(node.actor),
          node,
          workflowStep
        };
      });
  }

  function readRawEdges(artifact) {
    const graph = artifact.dependency_graph_for_view;
    if (Array.isArray(graph)) {
      return graph;
    }
    if (!graph || typeof graph !== "object") {
      return [];
    }
    if (Array.isArray(graph.edges)) {
      return graph.edges;
    }
    if (Array.isArray(graph.links)) {
      return graph.links;
    }
    if (Array.isArray(graph.dependencies)) {
      return graph.dependencies;
    }
    return [];
  }

  function edgeEndpoint(edge, keys) {
    if (Array.isArray(edge)) {
      return keys.includes("from") || keys.includes("source") ? edge[0] : edge[1];
    }
    if (!edge || typeof edge !== "object") {
      return "";
    }
    for (const key of keys) {
      if (typeof edge[key] === "string" && edge[key]) {
        return edge[key];
      }
    }
    return "";
  }

  function normalizeEdges(artifact, nodes) {
    const nodeIds = new Set(nodes.map((node) => node.id));
    const rawEdges = readRawEdges(artifact);
    const edges = [];
    const seen = new Set();

    for (const edge of rawEdges) {
      const from = edgeEndpoint(edge, ["from", "source", "source_id", "source_node_id"]);
      const to = edgeEndpoint(edge, ["to", "target", "target_id", "target_node_id"]);
      if (!nodeIds.has(from) || !nodeIds.has(to)) {
        continue;
      }
      const key = `${from}->${to}`;
      if (seen.has(key)) {
        continue;
      }
      seen.add(key);
      edges.push({ from, to });
    }

    if (edges.length === 0 && nodes.length > 1) {
      for (let index = 0; index < nodes.length - 1; index += 1) {
        edges.push({ from: nodes[index].id, to: nodes[index + 1].id });
      }
    }

    return edges.map((edge, index) => ({
      ...edge,
      bpmnId: normalizeXmlId("EGFlow", `${edge.from}_${edge.to}`, index + 1)
    }));
  }

  function bpmnElementForKind(kind) {
    if (kind === "start") {
      return "bpmn:startEvent";
    }
    if (kind === "input") {
      return "bpmn:userTask";
    }
    if (kind === "llm" || kind === "tool") {
      return "bpmn:serviceTask";
    }
    if (kind === "terminal") {
      return "bpmn:endEvent";
    }
    return "bpmn:task";
  }

  function dimensionsForKind(kind) {
    if (kind === "start" || kind === "terminal") {
      return { width: 48, height: 48 };
    }
    return { width: 156, height: 82 };
  }

  function buildLayout(nodes) {
    const layout = new Map();
    const spacing = 230;
    nodes.forEach((node, index) => {
      const dimensions = dimensionsForKind(node.kind);
      layout.set(node.id, {
        x: 90 + index * spacing,
        y: 170 - Math.round(dimensions.height / 2),
        width: dimensions.width,
        height: dimensions.height
      });
    });
    return layout;
  }

  function buildEgBpmnViewModel(payload) {
    const artifact = unwrapArtifact(payload);
    const nodes = normalizeNodes(artifact);
    const edges = normalizeEdges(artifact, nodes);
    const nodeIdToBpmnId = {};
    const bpmnIdToNodeId = {};
    for (const node of nodes) {
      nodeIdToBpmnId[node.id] = node.bpmnId;
      bpmnIdToNodeId[node.bpmnId] = node.id;
    }
    return {
      artifact,
      nodes,
      edges,
      nodeIdToBpmnId,
      bpmnIdToNodeId
    };
  }

  function buildBpmnXml(payload) {
    const viewModel = buildEgBpmnViewModel(payload);
    const { nodes, edges } = viewModel;
    const layout = buildLayout(nodes);
    const incoming = new Map(nodes.map((node) => [node.id, []]));
    const outgoing = new Map(nodes.map((node) => [node.id, []]));
    const nodeById = new Map(nodes.map((node) => [node.id, node]));

    for (const edge of edges) {
      incoming.get(edge.to)?.push(edge.bpmnId);
      outgoing.get(edge.from)?.push(edge.bpmnId);
    }

    const processId = "EG_Process_1";
    const processElements = nodes.map((node) => {
      const tag = bpmnElementForKind(node.kind);
      const incomingXml = incoming.get(node.id).map((id) => `      <bpmn:incoming>${escapeXml(id)}</bpmn:incoming>`).join("\n");
      const outgoingXml = outgoing.get(node.id).map((id) => `      <bpmn:outgoing>${escapeXml(id)}</bpmn:outgoing>`).join("\n");
      const children = [incomingXml, outgoingXml].filter(Boolean).join("\n");
      const name = node.actor ? `${node.label} (${node.kind})` : `${node.label}`;
      return children
        ? `    <${tag} id="${escapeXml(node.bpmnId)}" name="${escapeXml(name)}">\n${children}\n    </${tag}>`
        : `    <${tag} id="${escapeXml(node.bpmnId)}" name="${escapeXml(name)}" />`;
    });

    const sequenceFlows = edges.map((edge) => {
      const source = nodeById.get(edge.from);
      const target = nodeById.get(edge.to);
      return `    <bpmn:sequenceFlow id="${escapeXml(edge.bpmnId)}" sourceRef="${escapeXml(source.bpmnId)}" targetRef="${escapeXml(target.bpmnId)}" />`;
    });

    const shapes = nodes.map((node) => {
      const bounds = layout.get(node.id);
      return `      <bpmndi:BPMNShape id="${escapeXml(node.bpmnId)}_di" bpmnElement="${escapeXml(node.bpmnId)}">\n        <dc:Bounds x="${bounds.x}" y="${bounds.y}" width="${bounds.width}" height="${bounds.height}" />\n      </bpmndi:BPMNShape>`;
    });

    const edgeShapes = edges.map((edge) => {
      const source = nodeById.get(edge.from);
      const target = nodeById.get(edge.to);
      const sourceBounds = layout.get(source.id);
      const targetBounds = layout.get(target.id);
      const startX = sourceBounds.x + sourceBounds.width;
      const startY = sourceBounds.y + Math.round(sourceBounds.height / 2);
      const endX = targetBounds.x;
      const endY = targetBounds.y + Math.round(targetBounds.height / 2);
      const midX = Math.round((startX + endX) / 2);
      return `      <bpmndi:BPMNEdge id="${escapeXml(edge.bpmnId)}_di" bpmnElement="${escapeXml(edge.bpmnId)}">\n        <di:waypoint x="${startX}" y="${startY}" />\n        <di:waypoint x="${midX}" y="${startY}" />\n        <di:waypoint x="${midX}" y="${endY}" />\n        <di:waypoint x="${endX}" y="${endY}" />\n      </bpmndi:BPMNEdge>`;
    });

    return {
      xml: [
        '<?xml version="1.0" encoding="UTF-8"?>',
        `<bpmn:definitions xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:bpmn="${BPMN_NS}" xmlns:bpmndi="${BPMNDI_NS}" xmlns:dc="${DC_NS}" xmlns:di="${DI_NS}" id="EG_Definitions_1" targetNamespace="https://psop.local/eg-bpmn">`,
        `  <bpmn:process id="${processId}" isExecutable="false">`,
        ...processElements,
        ...sequenceFlows,
        "  </bpmn:process>",
        '  <bpmndi:BPMNDiagram id="EG_Diagram_1">',
        `    <bpmndi:BPMNPlane id="EG_Plane_1" bpmnElement="${processId}">`,
        ...shapes,
        ...edgeShapes,
        "    </bpmndi:BPMNPlane>",
        "  </bpmndi:BPMNDiagram>",
        "</bpmn:definitions>"
      ].join("\n"),
      viewModel
    };
  }

  return {
    buildBpmnXml,
    buildEgBpmnViewModel,
    escapeXml
  };
});
