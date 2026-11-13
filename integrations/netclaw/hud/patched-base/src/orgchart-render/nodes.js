/**
 * Node rendering and the four health treatments (FR-009a, FR-009b, FR-029a).
 *
 * Consumes orgchart/ output. Never classifies health, chooses categories, or
 * computes positions — those answers arrive already decided (see
 * contracts/layout-contract.md, consumer contract).
 *
 * The encoding rule (FR-009a): each state differs in FORM, COLOUR TEMPERATURE
 * and MOTION at once, never opacity alone. Motion is deliberately REDUNDANT
 * (R8) — it is added on top of an already-sufficient form+colour distinction,
 * so suppressing it for prefers-reduced-motion (FR-032c) cannot collapse the
 * encoding. That is also why SC-007 (greyscale) and SC-010 (reduced motion)
 * test the same underlying property.
 */

import * as THREE from 'three';

/**
 * Health treatments. Form and colour alone must separate all four — verified
 * by SC-007's greyscale test, which is why `shape` and `lightness` differ
 * across every row and not just `color`.
 */
export const TREATMENTS = {
  HOT: {
    shape: 'sphere',
    color: 0x4dff9b,
    emissive: 0x1fbf68,
    emissiveIntensity: 1.5,
    lightness: 0.88,
    scale: 1.06,
    pulse: 0.10,          // alive: gentle breathing
    label: 'Running now',
  },
  WARM: {
    shape: 'rounded',
    color: 0x86bdf5,
    emissive: 0x2f6da8,
    emissiveIntensity: 1.0,
    lightness: 0.68,
    scale: 0.9,
    pulse: 0.0,           // idle: still
    label: 'Seen recently, idle',
  },
  COLD: {
    shape: 'flat',        // a disc reads as inert next to a lit sphere
    // Lifted well off near-black (was 0x3a4654, luminance ~66): cold must read
    // as INERT, not as absent — an operator has to see what capacity exists
    // before deciding to warm it. But NOT as bright as the first attempt
    // (0x7d8ea3, luminance 140), which landed within 10 luminance of FAULT and
    // made the two indistinguishable in greyscale. treatments.test.js caught
    // that. Luminance 107: clearly present, clearly dimmer than HOT, and 43
    // clear of FAULT.
    color: 0x5f6d80,
    emissive: 0x2c3a4a,
    emissiveIntensity: 0.5,
    lightness: 0.5,
    scale: 0.74,
    pulse: 0.0,
    label: 'Never started — inert by design',
  },
  FAULT: {
    shape: 'ring',        // a broken outline, unmistakable at any zoom
    color: 0xff7a7a,
    emissive: 0xd32f2f,
    emissiveIntensity: 2.1,
    lightness: 0.74,
    scale: 1.14,          // FR-009b: most salient state after HOT
    pulse: 0.22,          // urgent, faster and deeper than HOT's breathing
    label: 'Was reachable, now unreachable',
  },
};

/**
 * Peer state treatments (feature 101, US3 — visual-contract.md §3).
 *
 * ## What this replaces, and why
 *
 * Peers used to be "structural": one fixed octahedron, no motion, and a colour
 * from `colorForStructural()` that branched on only two things — `severed`, and
 * `channel_state ∈ {unreachable, reconnecting}`. Everything else fell through to
 * a single healthy blue. It **never read `stale`**, and `channel_state: "unknown"`
 * is not in that set, so against the live feed FIVE of seven peers landed in the
 * catch-all: Byrn (stale 12d), Nicholas (stale 19d), Hermes (stale 14d), AB and
 * Carapace (never seen) all rendered identically to a live Nate. Confirmed
 * visually in the pre-change screenshot, not just in source.
 *
 * ## The encoding rule (inherited from TREATMENTS above)
 *
 * Each state differs in FORM, COLOUR TEMPERATURE and (where present) MOTION at
 * once — never opacity alone, never colour alone. `scaleMul` and `affix` are
 * additional redundant channels. Motion is deliberately redundant so suppressing
 * it for prefers-reduced-motion cannot collapse the encoding.
 *
 * `lum` is the ITU-R BT.709 luminance of `color`, kept as a comment so the
 * ≥18-delta requirement is auditable by eye as well as by peer-treatments.test.js.
 */
export const PEER_TREATMENTS = {
  // Channel up right now. Brightest, alive, gentle breathing.
  LIVE: {
    shape: 'peer', color: 0x7ff0d0, emissive: 0x1fbf9b, emissiveIntensity: 1.6,
    scaleMul: 1.08, pulse: 0.09, affix: '', label: 'Channel up now',
  }, // lum ≈ 205
  // Federated, idle, inventory fresh. Normal steady state — nothing is wrong.
  IDLE: {
    shape: 'peer', color: 0x8ad6ff, emissive: 0x2f6da8, emissiveIntensity: 1.1,
    scaleMul: 1.0, pulse: 0.0, affix: '', label: 'Federated, idle',
  }, // lum ≈ 201 — nearly equal to LIVE, so MOTION + form carry this pair (R2)
  // Federated, but what we know is old.
  STALE: {
    shape: 'peerWorn', color: 0xb8a878, emissive: 0x5a4f2e, emissiveIntensity: 0.7,
    scaleMul: 0.92, pulse: 0.0, affix: 'stale', label: 'Data is old',
  }, // lum ≈ 168
  // Never sent an inventory. NOT a failure (FR-016/017) — deliberately cool and
  // hollow rather than warm/alarming, so "never heard from AB" cannot read as
  // "AB is broken".
  UNKNOWN: {
    shape: 'peerHollow', color: 0x6f7f96, emissive: 0x2a3440, emissiveIntensity: 0.45,
    scaleMul: 0.88, pulse: 0.0, affix: 'never seen', label: 'Never sent an inventory',
  }, // lum ≈ 126
  // Was reachable, is not now. Actionable — urgent pulse, broken outline.
  UNREACHABLE: {
    shape: 'ring', color: 0xff9d6e, emissive: 0xc2481a, emissiveIntensity: 2.0,
    scaleMul: 1.12, pulse: 0.2, affix: 'unreachable', label: 'Was reachable, now not',
  }, // lum ≈ 175 — separated from STALE by form (ring vs worn) + motion + affix
  // Deliberately cut. Darkest, inert.
  SEVERED: {
    shape: 'peerCut', color: 0x8a4a52, emissive: 0x3a1c20, emissiveIntensity: 0.5,
    scaleMul: 0.85, pulse: 0.0, affix: 'severed', label: 'Deliberately severed',
  }, // lum ≈ 92
};

export const KIND_SCALE = { border: 2.2, peer: 1.35, member: 1.0, edge: 1.15 };

/** Shared geometries — created once, reused across every node (FR-029a). */
function buildGeometries() {
  return {
    sphere: new THREE.SphereGeometry(1.6, 24, 18),
    rounded: new THREE.SphereGeometry(1.5, 16, 12),
    flat: new THREE.CylinderGeometry(1.5, 1.5, 0.35, 20).rotateX(Math.PI / 2),
    ring: new THREE.TorusGeometry(1.5, 0.42, 10, 24),
    border: new THREE.IcosahedronGeometry(2.4, 1),
    peer: new THREE.OctahedronGeometry(1.8, 0),
    edge: new THREE.BoxGeometry(1.5, 2.6, 0.5),
    // Feature 101 (US3, visual-contract R4): peer STATE modulates the octahedron
    // silhouette rather than replacing it with a member shape, so band membership
    // still reads at a glance while state reads on top of it.
    //   peerWorn   — lower-detail octahedron: same family, visibly degraded
    //   peerHollow — smaller octahedron rendered as a wireframe (see material
    //                below): present but empty, for "we have never heard from it"
    //   peerCut    — a half-height octahedron: the silhouette, truncated
    peerWorn: new THREE.OctahedronGeometry(1.7, 0),
    peerHollow: new THREE.OctahedronGeometry(1.75, 0),
    peerCut: new THREE.OctahedronGeometry(1.8, 0).scale(1, 0.45, 1),
  };
}

/**
 * Build every node mesh for a computed layout.
 *
 * @param {Array<object>} layoutNodes from computeLayout().nodes
 * @param {(text:string)=>object} makeLabel host label factory (CSS2D), reused per FR-028
 * @returns {{group: THREE.Group, entries: Array<object>, dispose: Function}}
 */
export function buildNodes(layoutNodes, makeLabel) {
  const group = new THREE.Group();
  group.name = 'orgchart-nodes';
  const geometries = buildGeometries();
  const materials = [];
  const entries = [];

  for (const node of layoutNodes || []) {
    const treatment = TREATMENTS[node.health] || TREATMENTS.COLD;

    // Feature 101 (US3): peers now carry a state treatment of their own instead
    // of being lumped in with the Border as "structural". Only the Border remains
    // structural — it has no state to convey, it IS the centre.
    const peerT = node.kind === 'peer'
      ? (PEER_TREATMENTS[node.peerState] || PEER_TREATMENTS.IDLE)
      : null;

    let geometry;
    if (node.kind === 'border') geometry = geometries.border;
    else if (peerT) geometry = geometries[peerT.shape] || geometries.peer;
    else if (node.kind === 'edge') geometry = geometries.edge;
    else geometry = geometries[treatment.shape] || geometries.sphere;

    const isStructural = node.kind === 'border';
    let color;
    if (isStructural) color = colorForStructural(node);
    else if (peerT) color = peerT.color;
    else color = treatment.color;

    const material = new THREE.MeshStandardMaterial({
      color,
      emissive: isStructural ? 0x102030 : (peerT ? peerT.emissive : treatment.emissive),
      emissiveIntensity: isStructural ? 1.1
        : (peerT ? peerT.emissiveIntensity : (treatment.emissiveIntensity ?? 1.0)),
      roughness: node.health === 'COLD' ? 0.8 : 0.28,
      metalness: node.health === 'COLD' ? 0.1 : 0.5,
      // UNKNOWN renders hollow: present, but visibly containing nothing. A third
      // form channel that survives greyscale and reduced motion alike.
      wireframe: peerT?.shape === 'peerHollow',
    });
    materials.push(material);

    const mesh = new THREE.Mesh(geometry, material);
    const scale = (KIND_SCALE[node.kind] || 1)
      * (isStructural ? 1 : (peerT ? peerT.scaleMul : treatment.scale));
    mesh.scale.setScalar(scale);
    mesh.position.set(node.position.x, node.position.y, node.position.z);
    mesh.userData = { nodeId: node.id, kind: node.kind, payload: node.payload, node };

    // Label: never blank — orgchart/normalize guarantees a fallback (FR-015).
    let text = node.label;
    if (node.kind === 'member' && node.toolCount > 0) text += `  ·${node.toolCount}`;
    if (node.kind === 'edge' && node.heartbeatAgeS != null) text += `  ${formatAge(node.heartbeatAgeS)}`;
    // Feature 101 (US3, visual-contract R5): the state affix is ADDITIVE — it
    // extends the label normalize.js already disambiguated, never replaces it.
    // This is the only channel that survives greyscale, reduced motion and
    // colour-blindness simultaneously, so a state with an affix is never
    // conveyed by rendering alone.
    if (peerT && peerT.affix) text += `  · ${peerT.affix}`;

    // The label is a sibling of the mesh, NOT a child. Parenting it to the mesh
    // made it inherit the pulse scale, so every HOT and FAULT label drifted a
    // few pixels each frame — caught by SC-011, which requires positions to be
    // stable. Anchoring in world space decouples the label from the animation.
    const label = makeLabel(text);
    label.position.set(
      node.position.x,
      node.position.y - (scale * 1.6 + 1.4),
      node.position.z,
    );

    group.add(mesh);
    group.add(label);
    entries.push({
      node, mesh, material, label, baseScale: scale,
      pulse: peerT ? peerT.pulse : treatment.pulse,
    });
  }

  return {
    group,
    entries,
    dispose() {
      for (const g of Object.values(geometries)) g.dispose();
      for (const m of materials) m.dispose();
    },
  };
}

function colorForStructural(node) {
  if (node.kind === 'border') return 0xffd97a;
  if (node.severed) return 0xa85c5c;
  if (node.channelState === 'unreachable' || node.channelState === 'reconnecting') return 0x86a9cc;
  return 0x8ad6ff;
}

/**
 * Human-readable last-seen age for edge nodes (US2 AC2) — legible on the node
 * itself, without opening the detail panel.
 *
 * @param {number} seconds
 * @returns {string}
 */
export function formatAge(seconds) {
  const s = Number(seconds);
  if (!Number.isFinite(s) || s < 0) return '';
  if (s < 90) return `${Math.round(s)}s ago`;
  if (s < 5400) return `${Math.round(s / 60)}m ago`;
  if (s < 172800) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

/**
 * Per-frame animation. Motion is redundant to the encoding (R8), so honouring
 * reduced motion simply skips this entirely (FR-032c).
 *
 * @param {Array<object>} entries from buildNodes
 * @param {number} elapsed seconds
 * @param {boolean} reducedMotion
 */
export function animateNodes(entries, elapsed, reducedMotion) {
  if (reducedMotion) return;
  for (const e of entries) {
    if (!e.pulse) continue;
    // FAULT beats faster than HOT: urgency reads differently from liveness.
    // Feature 101: UNREACHABLE is the peer-side equivalent of FAULT and gets the
    // same urgent rate, so 'needs attention' reads identically in both bands.
    const urgent = e.node.health === 'FAULT' || e.node.peerState === 'UNREACHABLE';
    const rate = urgent ? 4.2 : 1.6;
    const factor = 1 + Math.sin(elapsed * rate) * e.pulse;
    e.mesh.scale.setScalar(e.baseScale * factor);
  }
}

/**
 * Apply search highlight/dim in place (FR-031a) — never hides, never re-packs.
 *
 * @param {Array<object>} entries
 * @param {(node:object)=>boolean} matches
 * @param {boolean} searching
 */
export function applyHighlight(entries, matches, searching) {
  for (const e of entries) {
    const hit = !searching || matches(e.node);
    e.material.opacity = hit ? 1 : 0.18;
    e.material.transparent = !hit;
    e.material.emissiveIntensity = hit ? (TREATMENTS[e.node.health]?.emissiveIntensity ?? 1.0) : 0.08;
    if (e.label?.element) e.label.element.style.opacity = hit ? '1' : '0.2';
  }
}
