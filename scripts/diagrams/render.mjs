/** Render the editorial overview and every node/edge of the compiled topology. */
import { readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import sharp from 'sharp';

const destination = new URL('../../docs/diagrams/', import.meta.url);
const topology = JSON.parse(await readFile(new URL('langgraph-topology.json', destination), 'utf8'));
const colours = {
  text: '#172B42', muted: '#536477', line: '#78899B', border: '#CBD5DF',
  model: '#275CA3', modelFill: '#EFF5FC', hitl: '#9D5B13', hitlFill: '#FFF7EB',
  neutral: '#F7F9FB', white: '#FFFFFF',
};
const escape = (value) => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');

function canvas(width, height, title, description) {
  return [
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img" aria-labelledby="title description">`,
    `<title id="title">${escape(title)}</title><desc id="description">${escape(description)}</desc>`,
    `<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="${colours.line}"/></marker><marker id="amber-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="${colours.hitl}"/></marker></defs>`,
    `<style>text{font-family:Arial,Helvetica,sans-serif;fill:${colours.text}} .muted{fill:${colours.muted}} .mono{font-family:Menlo,Consolas,monospace} .edge{fill:none;stroke:${colours.line};stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round;marker-end:url(#arrow)}</style>`,
    `<rect width="${width}" height="${height}" fill="white"/>`,
  ];
}

function text(svg, x, y, value, size = 18, attrs = '') {
  svg.push(`<text x="${x}" y="${y}" font-size="${size}" ${attrs}>${escape(value)}</text>`);
}

function rect(svg, x, y, w, h, fill = colours.white, stroke = colours.border, radius = 9) {
  svg.push(`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${radius}" fill="${fill}" stroke="${stroke}"/>`);
}

function path(svg, d, attrs = '') {
  svg.push(`<path class="edge" d="${d}" ${attrs}/>`);
}

function box(svg, x, y, w, h, title, subtitle, kind = 'deterministic') {
  const fill = kind === 'hitl' ? colours.hitlFill : kind === 'model' || kind === 'tool' ? colours.modelFill : colours.white;
  const stroke = kind === 'hitl' ? '#D6B083' : kind === 'model' || kind === 'tool' ? '#A8C0DD' : colours.border;
  rect(svg, x, y, w, h, fill, stroke);
  text(svg, x + 20, y + 31, title, 21, 'font-weight="600"');
  const lines = Array.isArray(subtitle) ? subtitle : [subtitle];
  lines.forEach((line, index) => text(svg, x + 20, y + 56 + index * 22, line, 17, 'class="muted"'));
}

function hitl(svg, y, title, detail) {
  box(svg, 802, y, 348, 88, title, detail, 'hitl');
  path(svg, `M660 ${y + 26} H794`, 'style="stroke:#9D5B13;marker-end:url(#amber-arrow)"');
  path(svg, `M802 ${y + 64} H668`, 'style="stroke:#9D5B13;marker-end:url(#amber-arrow)"');
  text(svg, 730, y + 18, 'pause', 14, 'text-anchor="middle" class="muted"');
  text(svg, 730, y + 86, 'resume', 14, 'text-anchor="middle" class="muted"');
}

function overview() {
  const svg = canvas(1200, 1320, 'LangGraph workflow overview', 'Grouped workflow stages with a bounded evidence tool loop, typed human decisions, deterministic journey validation, one model revision and fallback. SQLite checkpoints, time travel and observability support the entire workflow.');
  text(svg, 50, 48, 'LONDON BIODIVERSITY EXPEDITION PLANNER', 15, 'letter-spacing="1.6" class="muted"');
  text(svg, 50, 96, 'LangGraph workflow overview', 35, 'font-weight="700"');
  text(svg, 50, 130, 'Grouped stages · seven typed interrupt paths · durable execution', 19, 'class="muted"');
  rect(svg, 52, 158, 13, 13, colours.modelFill, '#A8C0DD', 2);
  text(svg, 75, 170, 'Model / ToolNode', 15);
  rect(svg, 251, 158, 13, 13, colours.white, colours.border, 2);
  text(svg, 274, 170, 'Deterministic code', 15);
  rect(svg, 474, 158, 13, 13, colours.hitlFill, '#D6B083', 2);
  text(svg, 497, 170, 'Human decision', 15);

  // Main execution lane. Each rectangle may group multiple compiled nodes.
  box(svg, 80, 205, 580, 88, 'Parse request & resolve context', 'Typed constraints · London origin · accepted bird taxonomy');
  text(svg, 100, 279, 'Model parsing + deterministic location and taxonomy services', 14, 'class="muted"');
  hitl(svg, 205, 'Clarify request or identity', 'Request · location · bird selection');
  path(svg, 'M370 293 V332');

  rect(svg, 80, 340, 580, 174, colours.neutral);
  text(svg, 100, 366, 'BOUNDED EVIDENCE LOOP', 14, 'letter-spacing="1.2" class="muted"');
  box(svg, 100, 384, 234, 79, 'Evidence Agent', 'Select allowed tools', 'model');
  box(svg, 394, 384, 246, 79, 'ToolNode', 'Occurrence · weather · sites', 'tool');
  path(svg, 'M334 408 H386');
  path(svg, 'M394 444 H342');
  text(svg, 370, 492, 'Validate, deduplicate and merge results · up to 6 rounds', 15, 'text-anchor="middle" class="muted"');
  text(svg, 802, 389, 'Model proposes the next tool call.', 18);
  text(svg, 802, 420, 'Typed results update shared state.', 18);
  text(svg, 802, 451, 'Failures and limits stay explicit.', 18, 'class="muted"');
  path(svg, 'M370 514 V551');

  box(svg, 80, 560, 580, 88, 'Validate evidence & site grounding', 'Evidence thresholds · eligible sites · request constraints');
  hitl(svg, 560, 'Resolve evidence trade-offs', 'Validated choice → affected evidence');
  path(svg, 'M370 648 V685');

  box(svg, 80, 694, 580, 88, 'Validate & rank journeys', 'Audited entrances → outbound / return → constraints');
  hitl(svg, 694, 'Resolve route trade-offs', 'Entrance evidence · walking limit');
  path(svg, 'M370 782 V819');

  box(svg, 80, 828, 580, 78, 'Compose expedition plan', 'Model writes from the validated evidence and journey bundle', 'model');
  path(svg, 'M370 906 V941');

  box(svg, 80, 950, 580, 78, 'Check grounding & finalise', 'Verify claims, route facts and constraints');
  box(svg, 802, 828, 348, 78, 'Revise once', 'Repair an unsupported draft', 'model');
  path(svg, 'M660 971 H744 V850 H794', 'stroke-dasharray="6 5"');
  path(svg, 'M802 888 H769 V1005 H668');
  text(svg, 686, 939, 'repair', 14, 'class="muted"');
  box(svg, 802, 1046, 348, 78, 'Deterministic fallback', 'Used if model composition fails');
  path(svg, 'M660 1017 H730 V1085 H794', 'stroke-dasharray="6 5"');
  text(svg, 680, 1043, 'fallback', 14, 'class="muted"');
  path(svg, 'M370 1028 V1073');
  rect(svg, 80, 1082, 580, 42, colours.neutral);
  text(svg, 370, 1109, 'Final plan or explicit limited / stopped outcome', 18, 'text-anchor="middle" font-weight="600"');
  path(svg, 'M802 1104 H668');
  text(svg, 80, 1149, 'Early source failures and loop-limit exits are expanded in the full topology.', 14, 'class="muted"');

  svg.push(`<path d="M50 1160 H1150" stroke="${colours.border}"/>`);
  text(svg, 50, 1189, 'RUNTIME SUPPORT · ACTIVE THROUGHOUT THE WORKFLOW', 14, 'letter-spacing="1.1" class="muted"');
  const runtime = [
    [50, 'SQLite checkpoints', 'Persist state · validate resume identity'],
    [430, 'Replay / Fork / Compare', 'Preserve history · recompute affected state'],
    [810, 'Observability', 'SSE reconnect · node / model / tool / provider spans'],
  ];
  runtime.forEach(([x, title, detail]) => {
    text(svg, x, 1230, title, 21, 'font-weight="600"');
    const parts = detail.split(' · ');
    parts.forEach((part, index) => text(svg, x, 1257 + index * 22, part, 16, 'class="muted"'));
  });
  svg.push('</svg>');
  return svg.join('\n');
}

const kindNames = { model: 'MODEL', tool: 'TOOLNODE', hitl: 'HITL', deterministic: 'CODE', terminal: 'OUTCOME', start: 'ENTRY', end: 'END' };
const stageNames = { entry: 'Start', intake: 'Request', location: 'Location', taxonomy: 'Taxonomy', evidence: 'Evidence', validation: 'Validation', routing: 'Journeys', planning: 'Plan', outcome: 'End' };

function wrapIdentifier(value, limit = 35) {
  const words = value.split('_');
  const lines = [''];
  for (const word of words) {
    const last = lines.length - 1;
    const candidate = lines[last] ? `${lines[last]}_${word}` : word;
    if (candidate.length > limit) {
      lines[last] += '_';
      lines.push(word);
    } else lines[last] = candidate;
  }
  return lines;
}

async function fullTopology() {
  // Only geometry is curated. The renderer rejects missing/stale nodes or edges.
  const positions = {
    start: ['__start__', 665, 250],
    parse: ['parse_expedition_request', 580, 350],
    clarify: ['request_clarification_interrupt', 1060, 350],
    geocode: ['geocode_location_query', 100, 480],
    location: ['resolve_location', 580, 620],
    locationHitl: ['location_correction_interrupt', 1060, 480],
    taxon: ['resolve_taxon', 580, 760],
    preview: ['prepare_taxon_selection', 1060, 760],
    taxonHitl: ['taxon_selection_interrupt', 1060, 890],
    bird: ['bird_input_correction_interrupt', 100, 890],
    agent: ['evidence_agent', 580, 1050],
    tools: ['evidence_tools', 1060, 1050],
    record: ['record_structured_evidence', 1060, 1180],
    limit: ['evidence_loop_limit', 100, 1050],
    failure: ['source_resolution_failure', 100, 760],
    validate: ['deterministic_validation', 580, 1330],
    tradeoff: ['actionable_tradeoff_interrupt', 1060, 1330],
    apply: ['apply_validated_user_choice', 1060, 1460],
    related: ['related_taxon_selection_interrupt', 1060, 1590],
    refresh: ['refresh_invalidated_evidence', 100, 1590],
    entrances: ['resolve_public_site_entrances', 580, 1750],
    journeys: ['request_walking_routes', 580, 1880],
    routeValidate: ['validate_route_constraints', 580, 2010],
    rank: ['rank_route_options', 580, 2140],
    routeHitl: ['route_tradeoff_interrupt', 1060, 2140],
    routeApply: ['apply_route_tradeoff_choice', 1060, 2270],
    compose: ['compose_expedition_plan', 580, 2410],
    ground: ['grounding_and_safety_checks', 580, 2540],
    revise: ['revise_expedition_plan', 1060, 2410],
    fallback: ['deterministic_plan_fallback', 1060, 2670],
    end: ['__end__', 665, 2860],
  };
  const routes = [
    ['start', 'parse', 'M750 310 V350'],
    ['parse', 'clarify', 'M920 400 H1060'],
    ['parse', 'geocode', 'M580 400 H270 V480'],
    ['parse', 'location', 'M750 450 V620'],
    ['clarify', 'geocode', 'M1230 450 V465 H270 V480'],
    ['clarify', 'location', 'M1060 420 H1000 V600 H800 V620'],
    ['geocode', 'locationHitl', 'M440 530 H1060'],
    ['locationHitl', 'location', 'M1230 580 V608 H870 V620'],
    ['location', 'locationHitl', 'M920 660 H1020 V550 H1060'],
    ['location', 'taxon', 'M750 720 V760'],
    ['location', 'failure', 'M580 670 H270 V760'],
    ['taxon', 'preview', 'M920 790 H1060'],
    ['taxon', 'taxonHitl', 'M920 830 H1000 V940 H1060'],
    ['taxon', 'bird', 'M580 810 H500 V940 H440'],
    ['taxon', 'agent', 'M750 860 V1050'],
    ['taxon', 'failure', 'M580 790 H440'],
    ['preview', 'taxonHitl', 'M1230 860 V890'],
    ['taxonHitl', 'agent', 'M1060 970 H980 V1025 H850 V1050'],
    ['bird', 'taxon', 'M440 915 H525 V840 H580'],
    ['agent', 'tools', 'M920 1100 H1060'],
    ['tools', 'record', 'M1230 1150 V1180'],
    ['record', 'agent', 'M1060 1230 H990 V1130 H920'],
    ['agent', 'limit', 'M580 1100 H440'],
    ['agent', 'validate', 'M750 1150 V1330'],
    ['validate', 'tradeoff', 'M920 1380 H1060'],
    ['tradeoff', 'apply', 'M1230 1430 V1460'],
    ['apply', 'agent', 'M1060 1490 H970 V1170 H850 V1150'],
    ['apply', 'validate', 'M1060 1510 H1000 V1405 H920'],
    ['apply', 'related', 'M1230 1560 V1590'],
    ['apply', 'refresh', 'M1060 1540 H1020 V1720 H270 V1690'],
    ['related', 'refresh', 'M1060 1640 H440'],
    ['refresh', 'validate', 'M440 1620 H500 V1400 H580'],
    ['validate', 'entrances', 'M750 1430 V1750'],
    ['entrances', 'journeys', 'M750 1850 V1880'],
    ['journeys', 'routeValidate', 'M750 1980 V2010'],
    ['routeValidate', 'rank', 'M750 2110 V2140'],
    ['rank', 'routeHitl', 'M920 2190 H1060'],
    ['rank', 'compose', 'M750 2240 V2410'],
    ['routeHitl', 'routeApply', 'M1230 2240 V2270'],
    ['routeApply', 'journeys', 'M1060 2300 H970 V1930 H920'],
    ['routeApply', 'compose', 'M1060 2335 H1000 V2390 H850 V2410'],
    ['compose', 'ground', 'M750 2510 V2540'],
    ['ground', 'revise', 'M920 2570 H1010 V2460 H1060'],
    ['revise', 'ground', 'M1230 2510 V2525 H850 V2540'],
    ['ground', 'fallback', 'M920 2610 H990 V2720 H1060'],
    ['ground', 'end', 'M750 2640 V2860'],
    ['fallback', 'end', 'M1230 2770 V2890 H835'],
    ['limit', 'end', 'M100 1100 H60 V2880 H665'],
    ['failure', 'end', 'M100 810 H30 V2900 H665'],
    ['validate', 'end', 'M580 1360 H70 V2865 H665'],
  ];
  const routeGeometry = new Map(routes.map(([from, to, d]) => [`${positions[from][0]}>${positions[to][0]}`, d]));
  const layoutNodes = Object.values(positions).map(([id, x, y]) => ({ id, x, y, width: id.startsWith('__') ? 170 : 340, height: id.startsWith('__') ? 60 : 100 }));
  const renderedIds = layoutNodes.map((node) => node.id).sort();
  const compiledIds = topology.nodes.map((node) => node.node_id).sort();
  if (JSON.stringify(renderedIds) !== JSON.stringify(compiledIds) || routeGeometry.size !== topology.edges.length || routes.length !== topology.edges.length) {
    throw new Error('Compiled topology changed. Update the diagram layout before rendering.');
  }
  const width = 1500;
  const height = 3030;
  const svg = canvas(width, height, 'Compiled LangGraph topology', `Every compiled node and edge for ${topology.workflow_version}: ${topology.nodes.length - 2} workflow nodes, START and END, and ${topology.edges.length} edges. Solid lines are unconditional edges; dashed lines are conditional routes. Seven amber nodes interrupt for validated human input.`);
  text(svg, 50, 46, 'LONDON BIODIVERSITY EXPEDITION PLANNER', 15, 'letter-spacing="1.5" class="muted"');
  text(svg, 50, 91, 'Compiled LangGraph topology', 36, 'font-weight="700"');
  text(svg, 50, 128, `${topology.workflow_version} · ${topology.nodes.length - 2} workflow nodes + START / END · ${topology.edges.length} edges · 7 typed HITL nodes`, 21, 'class="muted"');
  const legends = [[50, colours.modelFill, '#A8C0DD', 'Model / ToolNode'], [285, colours.white, colours.border, 'Deterministic code'], [535, colours.hitlFill, '#D6B083', 'Human interrupt']];
  for (const [x, fill, border, label] of legends) {
    rect(svg, x, 154, 16, 16, fill, border, 2);
    text(svg, x + 28, 168, label, 17);
  }
  path(svg, 'M788 163 H832'); text(svg, 847, 168, 'Edge', 17);
  path(svg, 'M935 163 H979', 'stroke-dasharray="6 5"'); text(svg, 994, 168, 'Conditional', 17);
  text(svg, 50, 207, 'Read node titles first; exact Python node identifiers appear underneath. All connections come from the compiled graph.', 17, 'class="muted"');
  svg.push('<g>');

  for (const source of topology.edges) {
    const d = routeGeometry.get(`${source.source}>${source.target}`);
    if (!d) throw new Error(`Missing edge geometry: ${source.source} → ${source.target}`);
    svg.push(`<g data-source="${escape(source.source)}" data-target="${escape(source.target)}" data-conditional="${source.conditional}"><title>${escape(`${source.source} → ${source.target}${source.route_label ? ` (${source.route_label})` : ''}`)}</title>`);
    svg.push(`<path d="${d}" fill="none" stroke="white" stroke-width="7"/>`);
    path(svg, d, source.conditional ? 'stroke-dasharray="7 5"' : '');
    svg.push('</g>');
  }
  const metadata = new Map(topology.nodes.map((node) => [node.node_id, node]));
  for (const node of layoutNodes) {
    const meta = metadata.get(node.id);
    const isBoundary = node.id.startsWith('__');
    const isModel = ['model', 'tool'].includes(meta.kind);
    const fill = meta.kind === 'hitl' ? colours.hitlFill : isModel ? colours.modelFill : isBoundary ? colours.neutral : colours.white;
    const border = meta.kind === 'hitl' ? '#D6B083' : isModel ? '#A8C0DD' : colours.border;
    svg.push(`<g data-node-id="${escape(node.id)}"><title>${escape(meta.summary)}</title>`);
    rect(svg, node.x, node.y, node.width, node.height, fill, border, isBoundary ? 25 : 8);
    if (isBoundary) {
      text(svg, node.x + node.width / 2, node.y + 36, meta.label.toUpperCase(), 19, 'text-anchor="middle" font-weight="600"');
    } else {
      text(svg, node.x + 16, node.y + 23, `${stageNames[meta.stage].toUpperCase()} / ${kindNames[meta.kind]}`, 11, 'letter-spacing="0.9" class="muted"');
      text(svg, node.x + 16, node.y + 49, meta.label, 19, 'font-weight="600"');
      wrapIdentifier(node.id).forEach((line, index) => text(svg, node.x + 16, node.y + 73 + index * 17, line, 13, 'class="mono muted"'));
    }
    svg.push('</g>');
  }
  svg.push('</g>');
  text(svg, 50, height - 24, 'Runtime services (SQLite, run management and SSE) support this graph; they are documented separately from its execution nodes.', 16, 'class="muted"');
  svg.push('</svg>');
  return svg.join('\n');
}

const overviewSvg = overview();
const topologySvg = await fullTopology();
for (const [name, svg] of [['langgraph-overview', overviewSvg], ['langgraph-topology', topologySvg]]) {
  if (/[\u3400-\u9FFF]/u.test(svg)) throw new Error('Diagrams must be English-only.');
  await writeFile(new URL(`${name}.svg`, destination), `${svg}\n`);
  await sharp(Buffer.from(svg), { density: 144 }).png().toFile(fileURLToPath(new URL(`${name}.png`, destination)));
  const info = await sharp(fileURLToPath(new URL(`${name}.png`, destination))).metadata();
  console.log(`${name}: ${info.width} x ${info.height} PNG + vector SVG`);
}
