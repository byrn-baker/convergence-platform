/**
 * Org-chart mount point — the single seam between main.js and HUD 2.0.
 *
 * main.js calls mountOrgChart() once and updateOrgChart() on each poll. Keeping
 * the surface this small is what makes FR-026's hard replace reviewable: the
 * orbit layout is removed from main.js, but nothing else in that 132 KB file
 * needs to understand how the chart is built.
 *
 * Position stability (FR-034) lives here: updateOrgChart repaints appearance
 * and NEVER recomputes layout. A claw that fails changes how it looks, never
 * where it is.
 */

import * as THREE from 'three';

import { computeLayout, appendMember } from '../orgchart/layout.js';
import { buildNodes, animateNodes, applyHighlight, TREATMENTS } from './nodes.js';
import { buildBands } from './bands.js';
import { buildLinks, animateFlows } from './links.js';
import { ringLayout, gridLayout } from '../orgchart/presets.js';
import { forceLayout } from '../orgchart/force-layout.js';
import { classifyHealth } from '../orgchart/health.js';
import { toggleExpansion, collapseAll, isExpanded, expandedCount } from './expansion.js';
import { buildA11yOverlay } from './a11y.js';

export { TREATMENTS };

/** Live chart state. Rebuilt only on explicit remount, never on a poll. */
const chart = {
  root: null,
  nodes: null,
  bands: null,
  links: null,
  layout: null,
  catalog: [],
  entries: [],
  search: '',
  // Feature 101 (US2): the single selection marker. ONE mesh moved between
  // nodes rather than a per-node treatment, which makes FR-009 (exactly one
  // node reads as selected) structural instead of something to remember.
  selectionMarker: null,
  selectedNodeId: null,
};

export function prefersReducedMotion() {
  return typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/**
 * Build the chart and add it to the scene.
 *
 * @param {THREE.Scene} scene
 * @param {object} n2n /api/n2n payload
 * @param {Array<object>} integrationCatalog from /api/graph integrations[]
 * @param {(text:string)=>object} makeLabel host CSS2D factory (FR-028)
 * @returns {{nodes:Array<object>, bands:Array<object>, categories:Array<object>}}
 */
export function mountOrgChart(scene, n2n, integrationCatalog, makeLabel) {
  unmountOrgChart(scene);

  const nowEpochS = Date.now() / 1000;
  const layout = computeLayout(n2n, integrationCatalog, nowEpochS);

  const root = new THREE.Group();
  root.name = 'orgchart';

  const bands = buildBands(layout.bands, makeLabel);
  const links = buildLinks(layout.nodes, layout.categories);
  const nodes = buildNodes(layout.nodes, makeLabel);

  root.add(bands.group, links.group, nodes.group);
  scene.add(root);

  Object.assign(chart, {
    root, nodes, bands, links, layout,
    catalog: integrationCatalog || [],
    entries: nodes.entries,
  });
  return layout;
}

/**
 * Attach the keyboard / screen-reader overlay (FR-032). Kept separate from
 * mountOrgChart so the scene can be built headlessly in tests without a DOM.
 *
 * @param {HTMLElement} container element covering the canvas
 * @param {{onSelect:Function, onToggle:Function}} handlers
 */
export function mountA11y(container, handlers) {
  if (!container || !chart.layout) return null;
  chart.a11y = buildA11yOverlay(container, chart.layout.nodes, handlers);
  return chart.a11y;
}

export function unmountOrgChart(scene) {
  if (!chart.root) return;
  scene.remove(chart.root);
  chart.nodes?.dispose?.();
  chart.bands?.dispose?.();
  chart.links?.dispose?.();
  chart.a11y?.destroy?.();
  Object.assign(chart, { root: null, nodes: null, bands: null, links: null, layout: null, entries: [] });
}

/**
 * Refresh from a poll (FR-034a): appearance only.
 *
 * Health, labels and links are updated in place. Positions are never
 * recalculated and categories are never re-ordered — doing so would re-pack the
 * chart under an operator who is reading it, which is exactly what FR-022 and
 * FR-031a exist to prevent elsewhere.
 *
 * A member that enrolled mid-session is appended via appendMember (FR-034b),
 * which places it without moving anything already on screen.
 *
 * @param {THREE.Scene} scene
 * @param {object} n2n
 * @param {(text:string)=>object} makeLabel
 */
export function updateOrgChart(scene, n2n, makeLabel) {
  if (!chart.layout) return;

  const nowEpochS = Date.now() / 1000;
  const members = Array.isArray(n2n?.members) ? n2n.members : [];
  const byId = new Map(members.map((m) => [m.member_id, m]));

  // 1. Repaint existing nodes.
  for (const entry of chart.entries) {
    const fresh = byId.get(entry.node.id);
    if (!fresh) continue;

    const health = classifyHealth(fresh, nowEpochS);
    if (health === entry.node.health) continue;

    entry.node.health = health;
    entry.node.payload = fresh;
    const t = TREATMENTS[health] || TREATMENTS.COLD;
    entry.material.color.setHex(t.color);
    entry.material.emissive.setHex(t.emissive);
    entry.material.emissiveIntensity = t.emissiveIntensity ?? 1.0;
    entry.material.roughness = health === 'COLD' ? 0.8 : 0.28;
    entry.mesh.scale.setScalar(entry.baseScale);
    entry.pulse = t.pulse;
  }

  // 2. Append genuinely new members (FR-034b) — nothing existing moves.
  const known = new Set(chart.entries.map((e) => e.node.id));
  for (const m of members) {
    if (!m?.member_id || known.has(m.member_id)) continue;

    const node = appendMember(chart.layout, m, chart.catalog, nowEpochS);
    if (!node) continue;

    const built = buildNodes([node], makeLabel);
    chart.nodes.group.add(...built.group.children);
    chart.entries.push(...built.entries);
    chart.layout.nodes.push(node);
  }

  chart.a11y?.sync?.(chart.layout.nodes);
}

/**
 * Search: highlight matches, dim the rest, in place (FR-031a/b).
 * Never hides and never re-packs — hiding would destroy the spatial memory the
 * whole layout exists to build.
 *
 * @param {string} query
 */
export function searchOrgChart(query) {
  chart.search = String(query || '').trim().toLowerCase();
  const q = chart.search;

  applyHighlight(chart.entries, (node) => {
    if (!q) return true;
    if (String(node.label || '').toLowerCase().includes(q)) return true;
    if (String(node.category || '').toLowerCase().includes(q)) return true;
    // A tool match must surface its owner even while collapsed (FR-031b).
    return (node.tools || []).some((t) => String(t).toLowerCase().includes(q));
  }, q.length > 0);
}

/** Meshes eligible for picking (click -> setDetail, FR-020a). */
export function pickableObjects() {
  return chart.entries.map((e) => e.mesh);
}

export function chartNodes() {
  return chart.layout ? chart.layout.nodes : [];
}

/**
 * Click handling: select AND reveal tools (operator revision to FR-020a).
 *
 * Returns the layout node so main.js can drive setDetail() unchanged — the
 * right-hand panel contract is untouched (FR-017/018).
 *
 * @param {THREE.Object3D} mesh the picked mesh
 * @param {(text:string)=>object} makeLabel
 * @returns {object|null} the layout node behind that mesh
 */
export function activateNode(mesh, makeLabel) {
  const node = mesh?.userData?.node;
  if (!node) return null;
  // Only members and edges carry tools; peers and the Border just select.
  if (node.kind === 'member' || node.kind === 'edge') {
    node.expanded = toggleExpansion(chart.root, node, mesh, makeLabel);
    // Keep the accessibility tree in step with the pointer path — otherwise a
    // screen-reader user gets a stale "collapsed" for a node someone expanded
    // with a mouse (FR-032b).
    const item = document.querySelector(
      `#orgchart-a11y .a11y-node[data-node-id="${CSS.escape(node.id)}"]`,
    );
    item?.setAttribute('aria-expanded', String(node.expanded));
  }
  return node;
}

/** Keyboard/affordance route to the same toggle (FR-032a). */
export function toggleNodeExpansion(nodeId, makeLabel) {
  const entry = chart.entries.find((e) => e.node.id === nodeId);
  if (!entry) return false;
  return toggleExpansion(chart.root, entry.node, entry.mesh, makeLabel);
}

export function collapseAllExpansions() {
  if (chart.root) collapseAll(chart.root);
}

export { isExpanded, expandedCount };

/**
 * Selection as its own visual channel (feature 101, US2 — visual-contract §2).
 *
 * ## Why an outline and not brightness
 *
 * The org chart had NO selection treatment at all — clicking set the detail panel
 * and nothing else, so the only feedback was the panel itself. The legacy orbit
 * scene used `emissiveIntensity = 1.8` plus a scale bump, and copying that here
 * would have reused STATE channels: brightening a dim node pushes it toward the
 * healthy treatment, so a selected STALE peer would look like a live one. That is
 * the concrete failure FR-007 names.
 *
 * So selection lives outside the silhouette, in space no state channel uses:
 * a ring drawn around the node, in a neutral colour that belongs to no state.
 *
 * Additive blending is deliberately NOT used — the scene already runs
 * UnrealBloomPass, and an additive ring washes out into the glow it is supposed
 * to stand against.
 */
const SELECTION_COLOR = 0xffffff;

function ensureSelectionMarker() {
  if (chart.selectionMarker) return chart.selectionMarker;
  const geo = new THREE.TorusGeometry(1, 0.075, 8, 48);
  const mat = new THREE.MeshBasicMaterial({
    color: SELECTION_COLOR, transparent: true, opacity: 0.95,
    depthTest: false,          // always legible, even behind a nearer node
    toneMapped: false,         // keep it pure white through the tone-mapped chain
  });
  const ring = new THREE.Mesh(geo, mat);
  ring.renderOrder = 999;
  ring.visible = false;
  ring.name = 'orgchart-selection';
  chart.selectionMarker = ring;
  if (chart.root) chart.root.add(ring);
  return ring;
}

/**
 * Mark one node as selected, or clear when nodeId is falsy.
 *
 * @param {string|null} nodeId layout node id
 */
export function setSelectedNode(nodeId) {
  const ring = ensureSelectionMarker();
  if (chart.root && ring.parent !== chart.root) chart.root.add(ring);

  const entry = nodeId ? chart.entries.find((e) => e.node.id === nodeId) : null;
  if (!entry) {
    ring.visible = false;
    chart.selectedNodeId = null;
    return;
  }

  // Sized from the node's own base scale so it reads at every zoom (FR-011) and
  // never depends on the pulse-modulated live scale.
  const r = entry.baseScale * 1.9 + 0.9;
  ring.scale.setScalar(r);
  ring.position.set(entry.node.position.x, entry.node.position.y, entry.node.position.z);
  ring.visible = true;
  chart.selectedNodeId = nodeId;
}

/** FR-008: full restoration on deselect, with no residue. */
export function clearSelectedNode() {
  setSelectedNode(null);
}

export function selectedNodeId() {
  return chart.selectedNodeId;
}

/**
 * Compute positions for a preset (feature 102, US2).
 *
 * `orgchart` returns null meaning "use what computeLayout produced" — it is not a
 * preset implementation, it is the absence of one (FR-010). `freeform` likewise has
 * no geometry: it starts from the org chart and everything after is the operator's.
 *
 * @returns {{[nodeId:string]: {x,y,z}}|null}
 */
export function presetPositions(presetId, store) {
  const nodes = chart.layout?.nodes || [];
  if (presetId === 'ring') return ringLayout(nodes);
  if (presetId === 'grid') return gridLayout(nodes);
  if (presetId === 'force') {
    // Solved ONCE here, off the render loop, and the result is applied as plain
    // positions (FR-040). There is no tick loop to leave running.
    const links = [];
    const border = nodes.find((n) => n.kind === 'border');
    if (border) for (const n of nodes) if (n !== border) links.push([border.id, n.id]);
    const pinnedPositions = {};
    for (const id of (store?.pinned?.force || [])) {
      const p = store.positions?.force?.[id];
      if (p) pinnedPositions[id] = p;
    }
    return forceLayout({
      nodes: nodes.map((n) => ({ id: n.id })),
      links,
      pinned: [...(store?.pinned?.force || [])],
      pinnedPositions,
    });
  }
  return null;
}

/**
 * Move meshes, labels and links to the positions the store and preset dictate.
 *
 * Precedence: an operator-placed position always wins over the preset, which always
 * wins over computeLayout. That ordering IS the FR-002/FR-027 guarantee — the system
 * never overrides where the operator put something.
 *
 * @param {object} store layout-store
 */
export function applyLayoutPositions(store) {
  if (!chart.entries?.length) return;
  const preset = store?.activePreset || 'orgchart';
  chart.lastAppliedPreset = preset;
  const presetMap = presetPositions(preset, store);
  const manual = store?.positions?.[preset] || {};

  for (const entry of chart.entries) {
    const id = entry.node.id;
    const next = manual[id] || presetMap?.[id] || entry.node.computedPosition || null;
    if (!next) continue;
    // Remember where computeLayout originally put it, so returning to the org chart
    // preset is possible without recomputing the whole layout.
    if (!entry.node.computedPosition) {
      entry.node.computedPosition = { ...entry.node.position };
    }
    entry.node.position = { x: next.x, y: next.y, z: next.z ?? 0 };
    entry.mesh.position.set(next.x, next.y, next.z ?? 0);
    if (entry.label) {
      entry.label.position.set(next.x, next.y - (entry.baseScale * 1.6 + 1.4), next.z ?? 0);
    }
  }
  rebuildLinks();

  // Bands and the trust boundary are ORG-CHART FURNITURE: horizontal strips at fixed
  // Y positions that only mean something when nodes are arranged in those strips.
  // Under Ring/Grid/force the nodes have moved but the bands cannot follow — they
  // have no meaningful position in a radial or uniform arrangement — so they were
  // left cutting across the scene labelling nothing. Hide them instead.
  //
  // This does not lose information: band membership is still carried by node form
  // and colour (feature 101's six peer states), and by the detail panel. Only the
  // strip graphic goes away, and only where it would be actively misleading.
  chart.lastAppliedPreset = preset;
  const bandsMeaningful = preset === 'orgchart' || preset === 'freeform';
  if (chart.bands?.group) chart.bands.group.visible = bandsMeaningful;

  if (chart.selectedNodeId) setSelectedNode(chart.selectedNodeId);
}

/** Links are geometry built from positions, so they must be rebuilt when those move. */
function rebuildLinks() {
  if (!chart.root || !chart.layout) return;
  if (chart.links) {
    chart.root.remove(chart.links.group);
    chart.links.dispose?.();
  }
  const preset = chart.lastAppliedPreset || 'orgchart';
  const categoryRouting = preset === 'orgchart' || preset === 'freeform';
  chart.links = buildLinks(chart.layout.nodes, chart.layout.categories, categoryRouting);
  chart.root.add(chart.links.group);
}

/** Live preview during a drag — mesh and label only, links on release. */
export function previewNodePosition(nodeId, pos) {
  const entry = chart.entries.find((e) => e.node.id === nodeId);
  if (!entry) return;
  entry.mesh.position.set(pos.x, pos.y, pos.z ?? 0);
  if (entry.label) entry.label.position.set(pos.x, pos.y - (entry.baseScale * 1.6 + 1.4), pos.z ?? 0);
  if (chart.selectedNodeId === nodeId) setSelectedNode(nodeId);
}

export function tickOrgChart(elapsed, camera) {
  animateNodes(chart.entries, elapsed, prefersReducedMotion());
  // Feature 101 (US4): flow markers on LIVE peer links only.
  animateFlows(chart.links?.flows, elapsed, prefersReducedMotion());
  // Billboard the selection ring: an unrotated torus seen edge-on collapses to
  // a line and the selection appears to vanish at some camera angles.
  if (camera && chart.selectionMarker?.visible) chart.selectionMarker.quaternion.copy(camera.quaternion);
}
