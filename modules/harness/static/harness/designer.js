// --- Wire Harness Designer (simplified SpliceCAD-style editor) ---
// A device shows its connectors as plain squares on its left/right side.
// Clicking a source connector then a destination connector creates a harness:
// a single bundle line with its own mating connector (named opposite of the
// device connector, e.g. J01 <-> P01). Selecting the harness line opens a
// dense pinout table below the canvas.

// Osmia: the designer lives at /harness/designer/ and its API under /harness/api/.
// api() maps the original "/api/..." paths onto that and sends Django's CSRF token.
const OSMIA = document.getElementById("harness-app").dataset;
function api(path, opts = {}) {
  const url = OSMIA.api + path.replace(/^\/api/, "");
  const method = (opts.method || "GET").toUpperCase();
  if (method !== "GET") {
    opts.headers = { ...(opts.headers || {}), "X-CSRFToken": OSMIA.csrf };
  }
  return fetch(url, { credentials: "same-origin", ...opts });
}

const canvas = document.getElementById("canvas");
const ctx = canvas.getContext("2d");

// ---------- View: zoom (mouse wheel) and pan (drag empty space) ----------
// Device positions and hit tests stay in "world" coordinates; the view maps
// them to the screen: screen = world * scale + offset.
const view = { scale: 1, x: 20, y: 20 };
const MIN_SCALE = 0.2;
const MAX_SCALE = 4;
const canvasWrap = document.getElementById("canvas-wrap");

function viewportSize() {
  // Fallback for a hidden/unsized page (e.g. before layout).
  return { w: canvasWrap.clientWidth || 1000, h: canvasWrap.clientHeight || 700 };
}

// Osmia's top bar gets taller when it wraps on a narrow window; the designer
// fills whatever height is left below it.
function fitBelowTopBar() {
  const bar = document.querySelector(".topbar");
  document.documentElement.style.setProperty("--osmia-bar-height", `${(bar && bar.offsetHeight) || 48}px`);
}
fitBelowTopBar();
window.addEventListener("resize", fitBelowTopBar);

// The canvas fills the visible area (sharp on high-DPI screens).
function resizeCanvas() {
  const dpr = window.devicePixelRatio || 1;
  const { w, h } = viewportSize();
  canvas.style.width = `${w}px`;
  canvas.style.height = `${h}px`;
  canvas.width = Math.max(1, Math.round(w * dpr));
  canvas.height = Math.max(1, Math.round(h * dpr));
}

function toWorld(sx, sy) {
  return { x: (sx - view.x) / view.scale, y: (sy - view.y) / view.scale };
}

function toScreen(wx, wy) {
  return { x: wx * view.scale + view.x, y: wy * view.scale + view.y };
}

// Zoom by `factor`, keeping the world point under (sx, sy) where it is.
function zoomAt(sx, sy, factor) {
  const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, view.scale * factor));
  const anchor = toWorld(sx, sy);
  view.scale = scale;
  view.x = sx - anchor.x * scale;
  view.y = sy - anchor.y * scale;
  render();
}

canvas.addEventListener("wheel", (e) => {
  e.preventDefault();
  const rect = canvas.getBoundingClientRect();
  zoomAt(e.clientX - rect.left, e.clientY - rect.top, Math.exp(-e.deltaY * 0.0015));
}, { passive: false });

// The area taken by the devices wired into harnesses (all devices when there
// are no harnesses yet), in world coordinates.
function contentBounds() {
  const wired = new Set();
  for (const harness of state.project.harnesses) {
    wired.add(harness.from_instance);
    for (const branch of harness.branches) wired.add(branch.to_instance);
  }
  const inHarness = state.project.instances.filter(i => wired.has(i.instance_id));
  const instances = inHarness.length ? inHarness : state.project.instances;
  let box = null;
  for (const inst of instances) {
    const device = state.devices[inst.device_id];
    if (!device) continue;
    const { width, height } = deviceBoxSize(device);
    const margin = CONN_W + LOOP_LEG;  // connectors and loop-backs stick out sideways
    const b = { minX: inst.x - margin, minY: inst.y, maxX: inst.x + width + margin, maxY: inst.y + height };
    box = box ? {
      minX: Math.min(box.minX, b.minX), minY: Math.min(box.minY, b.minY),
      maxX: Math.max(box.maxX, b.maxX), maxY: Math.max(box.maxY, b.maxY),
    } : b;
  }
  return box;
}

// Centre the view on the middle of the harnesses, zooming out only if they
// don't fit (never zooming in past 100%).
function centerView() {
  resizeCanvas();
  const { w, h } = viewportSize();
  const box = contentBounds();
  if (!box) {
    view.scale = 1;
    view.x = 20;
    view.y = 20;
  } else {
    const pad = 40;
    const fit = Math.min((w - 2 * pad) / Math.max(1, box.maxX - box.minX), (h - 2 * pad) / Math.max(1, box.maxY - box.minY));
    view.scale = Math.max(MIN_SCALE, Math.min(1, fit));
    view.x = w / 2 - ((box.minX + box.maxX) / 2) * view.scale;
    view.y = h / 2 - ((box.minY + box.maxY) / 2) * view.scale;
  }
  render();
}

function applyViewDecorations() {
  // The dotted grid moves and scales with the view.
  const grid = 20 * view.scale;
  canvasWrap.style.backgroundSize = `${grid}px ${grid}px`;
  canvasWrap.style.backgroundPosition = `${view.x}px ${view.y}px`;
  const level = document.getElementById("zoom-level");
  if (level) level.textContent = `${Math.round(view.scale * 100)}%`;
  positionPinoutHint();
  if (typeof positionConnectorTip === "function") positionConnectorTip();
}

if (window.ResizeObserver) new ResizeObserver(() => { resizeCanvas(); render(); }).observe(canvasWrap);
else window.addEventListener("resize", () => { resizeCanvas(); render(); });

const state = {
  devices: {},        // deviceId -> full device object (cache)
  project: { id: null, name: "Untitled Harness", instances: [], harnesses: [] },
  selectedInstance: null,
  selectedHarness: null,
  pendingPlaceDeviceId: null,
  pendingConnectorFrom: null, // { instance_id, connector_id }
  dragging: null,             // { instance_id, offsetX, offsetY }
  signalRules: [],            // [[sigA, sigB], ...] — global compatibility pairs
  users: [],                  // [{id, name}, ...] — no auth, just a name registry
};

const BOX_HEADER = 26;
const ROW_H = 78;
const MARGIN_TOP = 12;
const MARGIN_BOTTOM = 12;
const BOX_WIDTH = 170;
const CONN_W = 18;
const CONN_H = 66;

// A pin's cable set (defined on the device in the Devices module).
const SET_TYPES = { straight: "Straight", twisted: "Twisted", shielded: "Shielded", twisted_shielded: "Twisted shielded" };

// Swatches for the common wire colours; anything else is shown as typed.
const WIRE_COLORS = {
  black: "#111", red: "#d32f2f", blue: "#1e63d0", green: "#2e8b57", yellow: "#f2c200", white: "#fff",
  brown: "#7b4b2a", orange: "#f28c28", violet: "#7e57c2", purple: "#7e57c2", grey: "#8a8a8a", gray: "#8a8a8a",
  pink: "#f48fb1", "green/yellow": "linear-gradient(90deg, #2e8b57 50%, #f2c200 50%)",
};

function genId() {
  return Math.random().toString(36).slice(2, 10);
}

function setStatus(msg) {
  document.getElementById("status-msg").textContent = msg || "";
  if (msg) setTimeout(() => { if (document.getElementById("status-msg").textContent === msg) setStatus(""); }, 2500);
}

function oppositeConnectorName(id) {
  const m = id.match(/^([A-Za-z]+)(.*)$/);
  if (!m) return id;
  const prefix = m[1].toUpperCase();
  const rest = m[2];
  if (prefix === "J") return "P" + rest;
  if (prefix === "P") return "J" + rest;
  return "P" + rest;
}

// ---------- Pin availability & signal compatibility ----------

function pinKey(instanceId, connectorId, pinId) {
  return `${instanceId}::${connectorId}::${pinId}`;
}

// A loop branch wires pins of one connector to each other. It sits on the
// trunk connector (to == trunk) or, marked `loop: true`, on any other connector
// of the harness. Both ends of its wires are on (to_instance, to_connector).
function isLoopBranch(harness, branch) {
  return !!branch.loop || (branch.to_instance === harness.from_instance && branch.to_connector === harness.from_connector);
}

// The connector a wire's first end (from_pin) is on: the trunk, or for a loop
// branch the loop's own connector.
function fromEndOf(harness, branch) {
  return isLoopBranch(harness, branch)
    ? { instanceId: branch.to_instance, connectorId: branch.to_connector }
    : { instanceId: harness.from_instance, connectorId: harness.from_connector };
}

// Every pin already wired into any harness/branch in the current project.
function usedPinKeys() {
  const used = new Set();
  for (const harness of state.project.harnesses) {
    for (const branch of harness.branches) {
      const from = fromEndOf(harness, branch);
      for (const conn of branch.connections) {
        used.add(pinKey(from.instanceId, from.connectorId, conn.from_pin));
        used.add(pinKey(branch.to_instance, branch.to_connector, conn.to_pin));
      }
    }
  }
  return used;
}

function signalHasRules(sig) {
  const s = sig.trim().toUpperCase();
  return state.signalRules.some(([a, b]) => a.trim().toUpperCase() === s || b.trim().toUpperCase() === s);
}

// Whether two pin signal names are allowed to be wired together, per the
// global Signal Rules. A signal with no rule mentioning it anywhere is
// unrestricted (so devices that never define rules keep working as before);
// once a signal has at least one rule, only its explicitly listed partners
// (or itself, if that pair is listed) are allowed.
function signalsCompatible(sigA, sigB) {
  if (!sigA || !sigB) return true;
  const a = sigA.trim().toUpperCase();
  const b = sigB.trim().toUpperCase();
  if (!signalHasRules(a) && !signalHasRules(b)) return true;
  return state.signalRules.some(([x, y]) => {
    const ux = x.trim().toUpperCase();
    const uy = y.trim().toUpperCase();
    return (ux === a && uy === b) || (ux === b && uy === a);
  });
}

// Pairs pins from two pin lists, skipping anything already used elsewhere in
// the project, matching by signal compatibility first, then whatever's left
// over positionally.
function pairAvailablePins(fromPins, toPins, usedKeys, fromCtx, toCtx) {
  const sameConnector = fromCtx.instanceId === toCtx.instanceId && fromCtx.connectorId === toCtx.connectorId;

  if (sameConnector) {
    // Loopback: from/to are the same connector's own pins. Pair them up
    // within one shared pool so a pin is never used twice and never paired
    // with itself (which the two-list algorithm below can't guarantee once
    // both lists are literally the same pins).
    const pool = fromPins.filter(p => !usedKeys.has(pinKey(fromCtx.instanceId, fromCtx.connectorId, p.id)));
    const used = new Set();
    const pairs = [];
    for (const fp of pool) {
      if (used.has(fp.id)) continue;
      const match = pool.find(tp => tp.id !== fp.id && !used.has(tp.id) && signalsCompatible(fp.signal, tp.signal));
      if (match) {
        pairs.push([fp, match]);
        used.add(fp.id);
        used.add(match.id);
      }
    }
    const remaining = pool.filter(p => !used.has(p.id));
    for (let i = 0; i + 1 < remaining.length; i += 2) pairs.push([remaining[i], remaining[i + 1]]);
    return pairs;
  }

  const availableFrom = fromPins.filter(p => !usedKeys.has(pinKey(fromCtx.instanceId, fromCtx.connectorId, p.id)));
  const availableTo = toPins.filter(p => !usedKeys.has(pinKey(toCtx.instanceId, toCtx.connectorId, p.id)));
  const usedTo = new Set();
  const usedFrom = new Set();
  const pairs = [];
  for (const fp of availableFrom) {
    const match = availableTo.find(tp => !usedTo.has(tp.id) && signalsCompatible(fp.signal, tp.signal));
    if (match) {
      pairs.push([fp, match]);
      usedTo.add(match.id);
      usedFrom.add(fp.id);
    }
  }
  const remainingFrom = availableFrom.filter(p => !usedFrom.has(p.id));
  const remainingTo = availableTo.filter(p => !usedTo.has(p.id));
  const n = Math.min(remainingFrom.length, remainingTo.length);
  for (let i = 0; i < n; i++) pairs.push([remainingFrom[i], remainingTo[i]]);
  return pairs;
}

// ---------- Device / connector geometry ----------

function connectorsOnSide(device, side) {
  return device.connectors.filter(c => c.side === side);
}

function deviceBoxSize(device) {
  const leftCount = connectorsOnSide(device, "left").length;
  const rightCount = connectorsOnSide(device, "right").length;
  const rows = Math.max(leftCount, rightCount, 1);
  const height = Math.max(60, BOX_HEADER + MARGIN_TOP + rows * ROW_H + MARGIN_BOTTOM);
  return { width: BOX_WIDTH, height };
}

// Center point of the device-side connector square, plus which side it's on.
function connectorSquarePosition(instance, device, connectorId) {
  const connector = device.connectors.find(c => c.id === connectorId);
  if (!connector) return null;
  const side = connector.side;
  const sideConnectors = connectorsOnSide(device, side);
  const idx = sideConnectors.findIndex(c => c.id === connectorId);
  const { width } = deviceBoxSize(device);
  const x = instance.x + (side === "left" ? 0 : width);
  const y = instance.y + BOX_HEADER + MARGIN_TOP + idx * ROW_H + ROW_H / 2;
  return { x, y, side };
}

function getInstance(instanceId) {
  return state.project.instances.find(i => i.instance_id === instanceId);
}

function getHarness(harnessId) {
  return state.project.harnesses.find(h => h.id === harnessId);
}

function getConnector(device, connectorId) {
  return device.connectors.find(c => c.id === connectorId);
}

function pinOf(instanceId, connectorId, pinId) {
  const inst = getInstance(instanceId);
  const device = inst && state.devices[inst.device_id];
  const connector = device && getConnector(device, connectorId);
  return (connector && connector.pins.find(p => p.id === pinId)) || null;
}

function isInterconnect(device) {
  return !!device && device.role === "interconnect";
}

// The pins wired to this pin, anywhere in the project: [{instanceId, connectorId, pinId}].
function wiredTo(instanceId, connectorId, pinId) {
  const ends = [];
  for (const harness of state.project.harnesses) {
    for (const branch of harness.branches) {
      const from = fromEndOf(harness, branch);
      for (const conn of branch.connections) {
        const a = { instanceId: from.instanceId, connectorId: from.connectorId, pinId: conn.from_pin };
        const b = { instanceId: branch.to_instance, connectorId: branch.to_connector, pinId: conn.to_pin };
        const isA = a.instanceId === instanceId && a.connectorId === connectorId && a.pinId === pinId;
        const isB = b.instanceId === instanceId && b.connectorId === connectorId && b.pinId === pinId;
        if (isA) ends.push(b);
        if (isB) ends.push(a);
      }
    }
  }
  return ends;
}

// On an interconnect: the pins this one is passed through to (both directions).
function mappedPins(device, connectorId, pinId) {
  const out = [];
  for (const [c1, p1, c2, p2] of device.pin_map || []) {
    if (c1 === connectorId && p1 === pinId) out.push({ connectorId: c2, pinId: p2 });
    if (c2 === connectorId && p2 === pinId) out.push({ connectorId: c1, pinId: p1 });
  }
  return out;
}

// A pin's tags (Tag 1-4 in Devices; older device versions had one "tag").
function pinTags(pin) {
  const tags = pin && (pin.tags || (pin.tag ? [pin.tag] : []));
  return (tags || []).filter(Boolean);
}

// The tags to show for a pin, as one text (e.g. "CAN1_H · Bus A"). A unit's
// pins have their own. An interconnect's pins take the tags of the unit on the
// other side of it: follow the pin map through, then the wire out, then (for
// chained interconnects) on again. Returns {tag, inherited, from} or null.
function effectiveTag(instanceId, connectorId, pinId, seen = new Set()) {
  const key = pinKey(instanceId, connectorId, pinId);
  if (seen.has(key)) return null;
  seen.add(key);
  const inst = getInstance(instanceId);
  const device = inst && state.devices[inst.device_id];
  const pin = pinOf(instanceId, connectorId, pinId);
  if (!pin) return null;
  const own = pinTags(pin).join(" · ");
  if (!isInterconnect(device)) return own ? { tag: own, inherited: false } : null;
  for (const through of mappedPins(device, connectorId, pinId)) {
    seen.add(pinKey(instanceId, through.connectorId, through.pinId));
    for (const far of wiredTo(instanceId, through.connectorId, through.pinId)) {
      const found = effectiveTag(far.instanceId, far.connectorId, far.pinId, seen);
      if (found) {
        const farInst = getInstance(far.instanceId);
        const farPin = pinOf(far.instanceId, far.connectorId, far.pinId);
        const from = found.from || `${farInst.label || state.devices[farInst.device_id].name} ${far.connectorId}.${farPin ? farPin.label : far.pinId}`;
        return { tag: found.tag, inherited: true, from };
      }
    }
  }
  return own ? { tag: own, inherited: false } : null;
}

function tagCell(ctx, pinId) {
  const td = document.createElement("td");
  td.className = "tag-cell";
  const found = effectiveTag(ctx.instanceId, ctx.connectorId, pinId);
  if (found) {
    td.textContent = (found.inherited ? "↪ " : "") + found.tag;
    if (found.inherited) {
      td.classList.add("inherited");
      td.title = `Taken from ${found.from}, through the interconnect`;
    }
  }
  return td;
}

function setText(pin) {
  if (!pin || !(pin.set || pin.set_type)) return "";
  return [pin.set, SET_TYPES[pin.set_type]].filter(Boolean).join(" · ");
}

// A wire's set: the pins' cable set, one text if both ends agree.
function wireSetText(fromPin, toPin) {
  const a = setText(fromPin), b = setText(toPin);
  if (a && b && a !== b) return `${a} / ${b}`;
  return a || b || "—";
}

function colorSwatch(name) {
  const css = WIRE_COLORS[String(name || "").trim().toLowerCase()];
  const span = document.createElement("span");
  span.className = "wire-swatch" + (css ? "" : " unknown");
  if (css) span.style.background = css;
  return span;
}

// A harness has ONE trunk connector (from_instance/from_connector) and one or
// more branches fanning out to different destination connectors — all wires
// sharing that same source connector live in a single harness. The line runs
// directly between the actual device connector rectangles (no separate
// "harness-side" square/stub) — from the source connector to a split point,
// then from that split point out to each destination connector. With a
// single branch the split point collapses onto the source, so it renders as
// one plain line straight into the destination.
function harnessGeometry(harness) {
  const fromInst = getInstance(harness.from_instance);
  if (!fromInst) return null;
  const fromDevice = state.devices[fromInst.device_id];
  if (!fromDevice) return null;
  const fromDevPos = connectorSquarePosition(fromInst, fromDevice, harness.from_connector);
  if (!fromDevPos) return null;

  // Loopback branches (self-referencing: to_instance/to_connector are the
  // same as the trunk's) have no real destination, so they're kept out of
  // the centroid the split point is based on — but they still get a slot
  // alongside the real branches (see loopbackVirtualSlot below).
  const branchGeo = [];
  const loopbackBranches = [];
  for (const branch of harness.branches) {
    if (isLoopBranch(harness, branch)) { loopbackBranches.push(branch); continue; }
    const toInst = getInstance(branch.to_instance);
    if (!toInst) continue;
    const toDevice = state.devices[toInst.device_id];
    if (!toDevice) continue;
    const toDevPos = connectorSquarePosition(toInst, toDevice, branch.to_connector);
    if (!toDevPos) continue;
    branchGeo.push({ branch, toInst, toDevice, toDevPos });
  }
  if (branchGeo.length === 0 && loopbackBranches.length === 0) return null;

  let splitPoint = fromDevPos;
  if (branchGeo.length > 1) {
    const cx = branchGeo.reduce((s, b) => s + b.toDevPos.x, 0) / branchGeo.length;
    const cy = branchGeo.reduce((s, b) => s + b.toDevPos.y, 0) / branchGeo.length;
    const SPLIT_FRACTION = 0.4;
    splitPoint = {
      x: fromDevPos.x + (cx - fromDevPos.x) * SPLIT_FRACTION,
      y: fromDevPos.y + (cy - fromDevPos.y) * SPLIT_FRACTION,
    };
  }

  // Each loop is drawn on its own connector; several on one connector stack up.
  const perConnector = {};
  const loopbackGeo = [];
  for (const branch of loopbackBranches) {
    const inst = getInstance(branch.to_instance);
    const device = inst && state.devices[inst.device_id];
    const pos = device && connectorSquarePosition(inst, device, branch.to_connector);
    if (!pos) continue;
    const key = `${branch.to_instance}::${branch.to_connector}`;
    const idx = perConnector[key] = (perConnector[key] ?? -1) + 1;
    loopbackGeo.push({ branch, hairpin: loopbackHairpin(pos, idx) });
  }

  return { fromInst, fromDevice, fromDevPos, branchGeo, splitPoint, loopbackGeo };
}

const LOOP_LEG = 48;      // how far the hairpin's legs extend from the connector
const LOOP_GAP = 18;      // vertical spacing between the two legs
const LOOP_RADIUS = LOOP_GAP / 2;
const LOOP_STEP = 34;     // vertical offset between stacked loopbacks on the same connector
const LOOP_Y_OFFSET = 18; // shifts the hairpin off the connector's dead center, so it
                           // doesn't attach at the exact same point the main line does

// A proper hairpin: two straight legs off the connector, capped by a
// semicircle — always anchored to the connector's own position and side,
// completely independent of wherever any real branch lines happen to go, so
// it never drifts or skews when devices move or a branch's angle changes.
// Offset above center so it doesn't land on top of the main harness line's
// exit point.
function loopbackHairpin(fromDevPos, loopIndex) {
  const dir = fromDevPos.side === "left" ? -1 : 1;
  const centerY = fromDevPos.y - LOOP_Y_OFFSET + loopIndex * LOOP_STEP;
  const nearX = fromDevPos.x;
  const farX = fromDevPos.x + dir * LOOP_LEG;
  const arcCenterX = farX - dir * LOOP_RADIUS;
  return { dir, nearX, farX, arcCenterX, centerY, y1: centerY - LOOP_RADIUS, y2: centerY + LOOP_RADIUS };
}

// ---------- Rendering ----------

function render() {
  const dpr = window.devicePixelRatio || 1;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.setTransform(dpr * view.scale, 0, 0, dpr * view.scale, dpr * view.x, dpr * view.y);
  applyViewDecorations();

  for (const harness of state.project.harnesses) {
    drawHarness(harness, harness.id === state.selectedHarness);
  }

  for (const inst of state.project.instances) {
    drawInstance(inst, inst.instance_id === state.selectedInstance);
  }

  if (state.pendingConnectorFrom) {
    const inst = getInstance(state.pendingConnectorFrom.instance_id);
    const device = state.devices[inst.device_id];
    const pos = connectorSquarePosition(inst, device, state.pendingConnectorFrom.connector_id);
    ctx.save();
    ctx.strokeStyle = "#3b7dd8";
    ctx.lineWidth = 2;
    ctx.strokeRect(pos.x - CONN_W / 2 - 4, pos.y - CONN_H / 2 - 4, CONN_W + 8, CONN_H + 8);
    ctx.restore();
  }
}

// Two straight legs off the connector, capped by a semicircle — a proper
// hairpin, not a straight-plus-curve teardrop.
function drawLoopback(hairpin, color, thickness) {
  const { dir, nearX, arcCenterX, centerY, y1, y2 } = hairpin;
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = thickness;
  ctx.lineCap = "round";
  ctx.beginPath();
  ctx.moveTo(nearX, y1);
  ctx.lineTo(arcCenterX, y1);
  ctx.arc(arcCenterX, centerY, LOOP_RADIUS, -Math.PI / 2, Math.PI / 2, dir === -1);
  ctx.lineTo(nearX, y2);
  ctx.stroke();
  ctx.restore();
}

function hitTestLoopback(hairpin, x, y) {
  const { nearX, farX, y1, y2 } = hairpin;
  const minX = Math.min(nearX, farX) - 8;
  const maxX = Math.max(nearX, farX) + 8;
  return x >= minX && x <= maxX && y >= y1 - 8 && y <= y2 + 8;
}

function drawHarness(harness, selected) {
  const geo = harnessGeometry(harness);
  if (!geo) return;
  const { fromDevPos, branchGeo, splitPoint, loopbackGeo } = geo;
  const totalCount = harness.branches.reduce((n, b) => n + b.connections.length, 0);
  const trunkColor = selected ? "#3b7dd8" : "#555";

  ctx.save();

  // single line from the source connector out to the split point
  if (branchGeo.length > 0) {
    const trunkThickness = selected ? Math.min(4 + totalCount, 14) : Math.min(2 + totalCount, 12);
    ctx.strokeStyle = trunkColor;
    ctx.lineWidth = trunkThickness;
    ctx.lineCap = "round";
    ctx.beginPath();
    ctx.moveTo(fromDevPos.x, fromDevPos.y);
    ctx.lineTo(splitPoint.x, splitPoint.y);
    ctx.stroke();
  }

  for (const lg of loopbackGeo) {
    const loopThickness = selected ? Math.min(4 + lg.branch.connections.length, 14) : Math.min(2 + lg.branch.connections.length, 12);
    drawLoopback(lg.hairpin, trunkColor, loopThickness);
  }

  // branch lines fanning out from the split point directly into each
  // destination connector — no separate stub or mating-connector square
  for (const bg of branchGeo) {
    const branchThickness = selected ? Math.min(4 + bg.branch.connections.length, 14) : Math.min(2 + bg.branch.connections.length, 12);
    ctx.strokeStyle = trunkColor;
    ctx.lineWidth = branchThickness;
    ctx.lineCap = "round";
    ctx.beginPath();
    ctx.moveTo(splitPoint.x, splitPoint.y);
    ctx.lineTo(bg.toDevPos.x, bg.toDevPos.y);
    ctx.stroke();
  }

  // junction dot where the trunk splits (only visible once there's a fan-out)
  if (branchGeo.length > 1) {
    ctx.fillStyle = trunkColor;
    ctx.beginPath();
    ctx.arc(splitPoint.x, splitPoint.y, 4, 0, Math.PI * 2);
    ctx.fill();
  }

  // harness label + total wire count, placed on the trunk (or on the single
  // branch/loopback line when there's only one thing off the split point)
  const labelP1 = fromDevPos;
  let labelP2;
  if (branchGeo.length > 1) labelP2 = splitPoint;
  else if (branchGeo.length === 1) labelP2 = branchGeo[0].toDevPos;
  else if (loopbackGeo.length > 0) labelP2 = { x: loopbackGeo[0].hairpin.arcCenterX, y: loopbackGeo[0].hairpin.centerY };
  else labelP2 = fromDevPos;
  const mx = (labelP1.x + labelP2.x) / 2;
  const my = (labelP1.y + labelP2.y) / 2;
  ctx.font = "bold 11px sans-serif";
  const label = `${harness.label || "H"} (${totalCount})`;
  const textW = ctx.measureText(label).width;
  ctx.fillStyle = "#fff";
  ctx.fillRect(mx - textW / 2 - 4, my - 9, textW + 8, 16);
  ctx.fillStyle = selected ? "#3b7dd8" : "#333";
  ctx.textAlign = "center";
  ctx.fillText(label, mx, my + 3);
  ctx.textAlign = "left";

  ctx.restore();
}

function drawConnectorRect(pos, fill, stroke, width) {
  const w = width || CONN_W;
  ctx.save();
  ctx.fillStyle = fill;
  ctx.strokeStyle = stroke;
  ctx.lineWidth = 2;
  ctx.fillRect(pos.x - w / 2, pos.y - CONN_H / 2, w, CONN_H);
  ctx.strokeRect(pos.x - w / 2, pos.y - CONN_H / 2, w, CONN_H);
  ctx.restore();
}

function drawConnectorLabel(pos, text, color, width) {
  ctx.save();
  ctx.font = "bold 10px sans-serif";
  ctx.fillStyle = color;
  const gap = (width || CONN_W) / 2 + 6;
  if (pos.side === "left") {
    ctx.textAlign = "right";
    ctx.fillText(text, pos.x - gap, pos.y + 3);
  } else {
    ctx.textAlign = "left";
    ctx.fillText(text, pos.x + gap, pos.y + 3);
  }
  ctx.restore();
}

function drawInstance(inst, selected) {
  const device = state.devices[inst.device_id];
  if (!device) return;
  const { width, height } = deviceBoxSize(device);

  ctx.save();
  ctx.fillStyle = "#fff";
  ctx.strokeStyle = selected ? "#3b7dd8" : "#333";
  ctx.lineWidth = selected ? 3 : 1.5;
  ctx.fillRect(inst.x, inst.y, width, height);
  if (isInterconnect(device)) ctx.setLineDash([6, 4]);  // passes signals through
  ctx.strokeRect(inst.x, inst.y, width, height);
  ctx.setLineDash([]);

  ctx.fillStyle = device.color || "#3b7dd8";
  ctx.fillRect(inst.x, inst.y, width, BOX_HEADER);
  ctx.strokeRect(inst.x, inst.y, width, BOX_HEADER);
  ctx.fillStyle = "#fff";
  ctx.font = "12px sans-serif";
  ctx.textAlign = "center";
  ctx.fillText((isInterconnect(device) ? "⇄ " : "") + (inst.label || device.name), inst.x + width / 2, inst.y + 17);
  ctx.textAlign = "left";

  for (const connector of device.connectors) {
    const pos = connectorSquarePosition(inst, device, connector.id);
    const isPending = state.pendingConnectorFrom &&
      state.pendingConnectorFrom.instance_id === inst.instance_id &&
      state.pendingConnectorFrom.connector_id === connector.id;
    drawConnectorRect(pos, isPending ? "#eaf1fd" : "#fff", isPending ? "#3b7dd8" : "#333");
    drawConnectorLabel(pos, connector.id, "#222");
  }

  ctx.restore();
}

// ---------- Hit testing ----------

function hitTestConnector(x, y) {
  for (const inst of state.project.instances) {
    const device = state.devices[inst.device_id];
    if (!device) continue;
    for (const connector of device.connectors) {
      const pos = connectorSquarePosition(inst, device, connector.id);
      if (Math.abs(x - pos.x) <= CONN_W / 2 + 4 && Math.abs(y - pos.y) <= CONN_H / 2 + 4) {
        return { instance_id: inst.instance_id, connector_id: connector.id };
      }
    }
  }
  return null;
}

function hitTestInstance(x, y) {
  for (let i = state.project.instances.length - 1; i >= 0; i--) {
    const inst = state.project.instances[i];
    const device = state.devices[inst.device_id];
    if (!device) continue;
    const { width, height } = deviceBoxSize(device);
    if (x >= inst.x && x <= inst.x + width && y >= inst.y && y <= inst.y + height) {
      return inst.instance_id;
    }
  }
  return null;
}

function distToSegment(p, a, b) {
  const l2 = (b.x - a.x) ** 2 + (b.y - a.y) ** 2;
  if (l2 === 0) return Math.hypot(p.x - a.x, p.y - a.y);
  let t = ((p.x - a.x) * (b.x - a.x) + (p.y - a.y) * (b.y - a.y)) / l2;
  t = Math.max(0, Math.min(1, t));
  const projX = a.x + t * (b.x - a.x);
  const projY = a.y + t * (b.y - a.y);
  return Math.hypot(p.x - projX, p.y - projY);
}

function hitTestHarness(x, y) {
  for (const harness of state.project.harnesses) {
    const geo = harnessGeometry(harness);
    if (!geo) continue;
    const { fromDevPos, branchGeo, splitPoint, loopbackGeo } = geo;
    const totalCount = harness.branches.reduce((n, b) => n + b.connections.length, 0);
    const trunkThickness = Math.min(2 + totalCount, 12);
    if (branchGeo.length > 0 && distToSegment({ x, y }, fromDevPos, splitPoint) <= trunkThickness / 2 + 4) {
      return harness.id;
    }
    for (const bg of branchGeo) {
      const branchThickness = Math.min(2 + bg.branch.connections.length, 12);
      if (distToSegment({ x, y }, splitPoint, bg.toDevPos) <= branchThickness / 2 + 4) {
        return harness.id;
      }
    }
    for (const lg of loopbackGeo) {
      if (hitTestLoopback(lg.hairpin, x, y)) {
        return harness.id;
      }
    }
  }
  return null;
}

// ---------- Canvas interaction ----------

function canvasMouseDown(e) {
  const { x, y } = mousePosFromEvent(e);

  const connector = hitTestConnector(x, y);
  if (connector) {
    if (!state.pendingConnectorFrom) {
      state.pendingConnectorFrom = connector;
      setStatus("Select the destination connector… (Esc to cancel)");
    } else if (state.pendingConnectorFrom.instance_id === connector.instance_id) {
      state.pendingConnectorFrom = null;
      setStatus("Cancelled");
    } else {
      const from = state.pendingConnectorFrom;
      state.pendingConnectorFrom = null;
      startHarnessCreation(from, connector);
    }
    updateLoopbackButton();
    render();
    return;
  }

  const instId = hitTestInstance(x, y);
  if (instId) {
    state.pendingConnectorFrom = null;
    updateLoopbackButton();
    const inst = getInstance(instId);
    state.dragging = { instance_id: instId, offsetX: x - inst.x, offsetY: y - inst.y };
    selectInstance(instId);
    return;
  }

  const harnessId = hitTestHarness(x, y);
  if (harnessId) {
    state.pendingConnectorFrom = null;
    updateLoopbackButton();
    selectHarness(harnessId, { x, y });
    render();
    return;
  }

  if (state.pendingPlaceDeviceId) {
    placeDevice(state.pendingPlaceDeviceId, x, y);
    return;
  }

  // Empty space: dragging pans the view; a plain click (no drag) deselects.
  const s = screenPosFromEvent(e);
  state.panning = { sx: s.x, sy: s.y, vx: view.x, vy: view.y, moved: false };
  canvas.style.cursor = "grabbing";
}

function canvasMouseMove(e) {
  if (state.panning) {
    const s = screenPosFromEvent(e);
    const dx = s.x - state.panning.sx;
    const dy = s.y - state.panning.sy;
    if (Math.abs(dx) + Math.abs(dy) > 3) state.panning.moved = true;
    view.x = state.panning.vx + dx;
    view.y = state.panning.vy + dy;
    render();
    return;
  }

  const { x, y } = mousePosFromEvent(e);
  if (state.dragging) {
    const inst = getInstance(state.dragging.instance_id);
    inst.x = x - state.dragging.offsetX;
    inst.y = y - state.dragging.offsetY;
    render();
  }
}

function canvasMouseUp() {
  state.dragging = null;
  if (state.panning) {
    const wasClick = !state.panning.moved;
    state.panning = null;
    canvas.style.cursor = "";
    if (wasClick) {
      state.pendingConnectorFrom = null;
      updateLoopbackButton();
      selectNone();
      render();
    }
  }
}

function canvasMouseLeave() {
  state.dragging = null;
  state.panning = null;
  canvas.style.cursor = "";
}

function screenPosFromEvent(e) {
  const rect = canvas.getBoundingClientRect();
  return { x: e.clientX - rect.left, y: e.clientY - rect.top };
}

// World coordinates of the mouse (what hit tests and placement use).
function mousePosFromEvent(e) {
  const s = screenPosFromEvent(e);
  return toWorld(s.x, s.y);
}

// A tip next to a clicked (pending) connector: loop it back, create an
// extension for it, or click another connector to wire the two.
function updateLoopbackButton() {
  const tip = document.getElementById("connector-tip");
  const from = state.pendingConnectorFrom;
  tip.classList.toggle("hidden", !from);
  if (!from) return;
  const inst = getInstance(from.instance_id);
  const device = inst && state.devices[inst.device_id];
  document.getElementById("connector-tip-title").textContent = device ? `${inst.label || device.name} ${from.connector_id}` : from.connector_id;
  positionConnectorTip();
}

function positionConnectorTip() {
  const tip = document.getElementById("connector-tip");
  const from = state.pendingConnectorFrom;
  if (!tip || !from || tip.classList.contains("hidden")) return;
  const inst = getInstance(from.instance_id);
  const device = inst && state.devices[inst.device_id];
  const pos = device && connectorSquarePosition(inst, device, from.connector_id);
  if (!pos) return;
  const s = toScreen(pos.x + (pos.side === "left" ? -CONN_W : CONN_W), pos.y + CONN_H / 2 + 6);
  tip.style.top = `${Math.round(s.y)}px`;
  // Open away from the device: to the right of a right-side connector, to the left of a left-side one.
  if (pos.side === "left") { tip.style.left = ""; tip.style.right = `${Math.round(canvasWrap.clientWidth - s.x)}px`; }
  else { tip.style.right = ""; tip.style.left = `${Math.round(s.x)}px`; }
}

// "Create extension": a new interconnect device (made in Devices) with this
// connector's exact pinout on its J02, placed beside the device, and a harness
// from the connector to its J01 with a wire per pin.
async function createExtension() {
  const from = state.pendingConnectorFrom;
  if (!from) return;
  const inst = getInstance(from.instance_id);
  const device = state.devices[inst.device_id];
  const connector = getConnector(device, from.connector_id);
  state.pendingConnectorFrom = null;
  updateLoopbackButton();
  setStatus("Creating the extension…");
  const res = await api("/api/extensions", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ device_id: device.id, connector_id: connector.id }),
  });
  const ext = await res.json().catch(() => null);
  if (!res.ok || !ext || !ext.connectors) { setStatus((ext && ext.error) || "Couldn't create the extension"); render(); return; }
  state.devices[ext.id] = ext;
  loadDeviceLibrary();  // the new device shows up in "Place device" too

  const pos = connectorSquarePosition(inst, device, connector.id);
  const size = deviceBoxSize(ext);
  const gap = 140;
  const input = ext.connectors.find(c => c.id === "J01") || ext.connectors[0];
  const inputIdx = connectorsOnSide(ext, input.side).findIndex(c => c.id === input.id);
  const x = pos.side === "left" ? inst.x - gap - size.width : inst.x + deviceBoxSize(device).width + gap;
  const y = pos.y - (BOX_HEADER + MARGIN_TOP + inputIdx * ROW_H + ROW_H / 2);
  const extInst = { instance_id: genId(), device_id: ext.id, device_version: ext.version, x, y, label: "" };
  state.project.instances.push(extInst);

  // One wire per pin that isn't wired yet; the extension's J01 pins have the same ids.
  const used = usedPinKeys();
  const toConn = { instance_id: extInst.instance_id, connector_id: input.id };
  const target = resolveHarnessTarget(from, toConn);
  const fromIsSource = target.effectiveFromConn.instance_id === from.instance_id;
  const pairs = [];
  for (const pin of connector.pins) {
    if (used.has(pinKey(from.instance_id, connector.id, pin.id))) continue;
    const twin = input.pins.find(p => p.id === pin.id);
    if (twin) pairs.push(fromIsSource ? [pin, twin] : [twin, pin]);
  }
  const harness = commitBranch(target.existingHarness, target.effectiveFromConn, target.effectiveToConn, pairs, target.reroot);
  selectHarness(harness.id);
  renderHarnessList();
  render();
  setStatus(`Added ${ext.name} (${pairs.length} wires). Save the project to keep it.`);
}
document.getElementById("btn-create-extension").onclick = () => createExtension();

document.getElementById("btn-loopback-pending").onclick = () => {
  const from = state.pendingConnectorFrom;
  if (!from) return;
  state.pendingConnectorFrom = null;
  updateLoopbackButton();
  startHarnessCreation(from, from);
  render();
};

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    const orderBox = document.getElementById("order-modal");
    if (orderBox && !orderBox.classList.contains("hidden")) {
      orderBox.classList.add("hidden");
    } else if (connectModal && !connectModal.classList.contains("hidden")) {
      closeConnectModal();
    } else if (state.pinoutOpen) {
      closePinout();
    }
    state.pendingConnectorFrom = null;
    updateLoopbackButton();
    render();
  }
  if (e.key === "Delete" || e.key === "Backspace") {
    if (document.activeElement && ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName)) return;
    if (document.querySelector(".modal:not(.hidden)")) return;  // never act behind an open popup
    if (state.selectedInstance) deleteInstance(state.selectedInstance);
    else if (state.selectedHarness) deleteHarness(state.selectedHarness);
  }
});

canvas.addEventListener("mousedown", canvasMouseDown);
canvas.addEventListener("mousemove", canvasMouseMove);
canvas.addEventListener("mouseup", canvasMouseUp);
canvas.addEventListener("mouseleave", canvasMouseLeave);

// ---------- Harness creation ----------
// A harness has one trunk connector (from_instance/from_connector) and a list
// of branches, each fanning out to a different destination connector. Any
// connector already part of a harness — whether it's currently the trunk or
// one of the branches — pulls a newly-wired connector into that SAME
// harness. If the shared connector was a branch (not the trunk), the harness
// is re-rooted so that connector becomes the trunk instead, since it's now
// the actual hub multiple things converge on — that's what makes the split
// point land in the middle of the units again instead of drawing a separate
// straight line per source.

function findHarnessContainingConnector(instanceId, connectorId) {
  // A harness where this connector is already the trunk wins over one where
  // it's a branch, so joining never has to re-root (and re-attach) a harness.
  for (const harness of state.project.harnesses) {
    if (harness.from_instance === instanceId && harness.from_connector === connectorId) {
      return { harness, role: "trunk" };
    }
  }
  for (const harness of state.project.harnesses) {
    const branch = harness.branches.find(b => !isLoopBranch(harness, b) && b.to_instance === instanceId && b.to_connector === connectorId);
    if (branch) return { harness, role: "branch", branch };
  }
  return null;
}

// Makes `newTrunkConn` (currently one of `harness`'s branches) the trunk,
// demoting the old trunk to a branch in its place. Connections on the
// swapped branch have their from/to sides flipped so the pin pairing stays
// correct from the new trunk's point of view.
function rerootHarness(harness, newTrunkConn) {
  const branchIdx = harness.branches.findIndex(
    b => !isLoopBranch(harness, b) && b.to_instance === newTrunkConn.instance_id && b.to_connector === newTrunkConn.connector_id
  );
  if (branchIdx < 0) return; // already the trunk, or not part of this harness — nothing to do

  const newTrunkBranch = harness.branches[branchIdx];
  // Loops stay in the harness: the old trunk's loops keep looping on that
  // connector (now marked `loop`), loops on the new trunk simply become trunk loops.
  for (const b of harness.branches) if (isLoopBranch(harness, b)) b.loop = true;
  const oldTrunk = {
    to_instance: harness.from_instance,
    to_connector: harness.from_connector,
    to_harness_connector: harness.from_harness_connector,
    to_verified: harness.from_verified,
  };

  harness.branches.splice(harness.branches.indexOf(newTrunkBranch), 1, {
    id: genId(),
    ...oldTrunk,
    connections: newTrunkBranch.connections.map(c => ({
      ...c,
      from_pin: c.to_pin,
      to_pin: c.from_pin,
      from_verified: c.to_verified,
      to_verified: c.from_verified,
    })),
  });

  harness.from_instance = newTrunkConn.instance_id;
  harness.from_connector = newTrunkConn.connector_id;
  harness.from_harness_connector = newTrunkBranch.to_harness_connector;
  harness.from_verified = newTrunkBranch.to_verified;
}

// Figures out which (if either) existing harness the two just-clicked
// connectors already belong to, and whether that harness would have to be
// re-rooted onto the shared connector. Nothing is changed here: the re-root
// (`reroot`) is only applied by commitBranch once wires are actually added, so
// opening the connect popup and cancelling leaves the project untouched.
function resolveHarnessTarget(fromConn, toConn) {
  // Loopback: never re-root. Re-rooting would move an existing harness's trunk
  // onto this connector, so the harness would re-attach and its split point
  // and mating-connector names would change after the initial connection.
  // It joins the harness this connector already belongs to, as its trunk or as
  // one of its destinations (stored as a `loop` branch on that connector); only
  // a connector that isn't in any harness gets a new harness for its loopback.
  if (fromConn.instance_id === toConn.instance_id && fromConn.connector_id === toConn.connector_id) {
    const match = findHarnessContainingConnector(fromConn.instance_id, fromConn.connector_id);
    return { existingHarness: match ? match.harness : null, effectiveFromConn: fromConn, effectiveToConn: toConn };
  }
  const matchFrom = findHarnessContainingConnector(fromConn.instance_id, fromConn.connector_id);
  if (matchFrom) {
    const reroot = matchFrom.role === "branch" ? fromConn : null;
    return { existingHarness: matchFrom.harness, effectiveFromConn: fromConn, effectiveToConn: toConn, reroot };
  }
  const matchTo = findHarnessContainingConnector(toConn.instance_id, toConn.connector_id);
  if (matchTo) {
    const reroot = matchTo.role === "branch" ? toConn : null;
    return { existingHarness: matchTo.harness, effectiveFromConn: toConn, effectiveToConn: fromConn, reroot };
  }
  return { existingHarness: null, effectiveFromConn: fromConn, effectiveToConn: toConn };
}

// Creates the harness (if it doesn't exist yet) and/or the branch to this
// destination (if it doesn't exist yet), then appends the given pin pairs as
// connections on that branch. Returns the harness.
function commitBranch(existingHarness, fromConn, toConn, pinPairs, reroot = null) {
  let harness = existingHarness;
  if (harness && reroot) rerootHarness(harness, reroot);
  if (!harness) {
    harness = {
      id: genId(),
      label: `H${state.project.harnesses.length + 1}`,
      from_instance: fromConn.instance_id,
      from_connector: fromConn.connector_id,
      from_harness_connector: oppositeConnectorName(fromConn.connector_id),
      from_verified: false, // confirmed by the trunk device's responsible user
      branches: [],
    };
    state.project.harnesses.push(harness);
  }

  // A loopback goes to the loop branch on its connector, other wires to the
  // ordinary branch; a connector can have both in the same harness.
  const wantLoop = fromConn.instance_id === toConn.instance_id && fromConn.connector_id === toConn.connector_id;
  let branch = harness.branches.find(b => b.to_instance === toConn.instance_id && b.to_connector === toConn.connector_id
    && isLoopBranch(harness, b) === wantLoop);
  if (!branch) {
    branch = {
      id: genId(),
      to_instance: toConn.instance_id,
      to_connector: toConn.connector_id,
      to_harness_connector: oppositeConnectorName(toConn.connector_id),
      to_verified: false, // confirmed by this destination device's responsible user
      connections: [],
    };
    if (wantLoop) branch.loop = true;
    harness.branches.push(branch);
  }

  for (const [fp, tp] of pinPairs) branch.connections.push(makeConnection(fp, tp));
  return harness;
}

function makeConnection(fromPin, toPin) {
  return {
    id: genId(),
    from_pin: fromPin.id,
    to_pin: toPin.id,
    color: "",  // wire colour, e.g. "Red"; the set comes from the pins (Devices)
    awg: "",
    length: "",
    from_verified: false, // confirmed by the from-side device's responsible user
    to_verified: false,   // confirmed by the to-side device's responsible user
  };
}

function startHarnessCreation(fromConn, toConn) {
  const { existingHarness, effectiveFromConn, effectiveToConn, reroot } = resolveHarnessTarget(fromConn, toConn);
  const fromConnector = getConnector(state.devices[getInstance(effectiveFromConn.instance_id).device_id], effectiveFromConn.connector_id);
  const toConnector = getConnector(state.devices[getInstance(effectiveToConn.instance_id).device_id], effectiveToConn.connector_id);
  // Always pin to pin: every wire's two pins are picked in the connect popup.
  openConnectModal(existingHarness, effectiveFromConn, effectiveToConn, fromConnector, toConnector, reroot);
  render();
}

// ---------- Connect modal (pin to pin) ----------
// One row per wire: pick a pin on each side. A pin can only be used once,
// across these rows and every wire already in the project; in a loopback a pin
// can't loop to itself. Rows whose signals break the Signal Rules are flagged
// and won't be created.

const connectModal = document.getElementById("connect-modal");
const connectPickBodyEl = document.getElementById("connect-pick-body");
let connectModalState = null; // { existingHarness, fromConn, toConn, fromConnector, toConnector, fromCtx, toCtx, reroot }
let connectPickRows = [];     // [{ id, fromPinId, toPinId }]

function isSameConnector(a, b) {
  return a.instanceId === b.instanceId && a.connectorId === b.connectorId;
}

// Loads (or reloads, when the Destination selector changes) which from/to
// connector pair the modal targets, and starts it with one wire.
function applyConnectTarget(existingHarness, fromConn, toConn, fromConnector, toConnector) {
  const fromInst = getInstance(fromConn.instance_id);
  const toInst = getInstance(toConn.instance_id);
  const fromName = fromInst.label || state.devices[fromInst.device_id].name;
  const toName = toInst.label || state.devices[toInst.device_id].name;
  const fromCtx = { instanceId: fromConn.instance_id, connectorId: fromConn.connector_id };
  const toCtx = { instanceId: toConn.instance_id, connectorId: toConn.connector_id };
  const reroot = connectModalState ? connectModalState.reroot : null;
  connectModalState = { existingHarness, fromConn, toConn, fromConnector, toConnector, fromCtx, toCtx, reroot };
  connectPickRows = [];

  document.getElementById("connect-modal-title").textContent = isSameConnector(fromCtx, toCtx)
    ? `Loop back ${fromName}.${fromConnector.id}`
    : `Connect ${fromName}.${fromConnector.id} → ${toName}.${toConnector.id}`;
  if (!addPickRow()) setStatus("No free pins left on one side — everything is already wired");
  renderPickTable();
}

// Entry point 1: clicking a source connector then a destination connector on
// the canvas (or "Loop Back") — both ends are fixed.
function openConnectModal(existingHarness, fromConn, toConn, fromConnector, toConnector, reroot = null) {
  document.getElementById("connect-unit-row").classList.add("hidden");
  connectModalState = null;
  applyConnectTarget(existingHarness, fromConn, toConn, fromConnector, toConnector);
  connectModalState.reroot = reroot;  // applied only if wires are added
  connectModal.classList.remove("hidden");
}

// Entry point 2: "+ Add wires" in the drawer — the trunk side is this harness,
// and which existing destination gets the wires is picked in the modal.
function openAddWiresModalForHarness(harness) {
  const fromConn = { instance_id: harness.from_instance, connector_id: harness.from_connector };
  const fromInst = getInstance(harness.from_instance);
  const fromConnector = getConnector(state.devices[fromInst.device_id], harness.from_connector);

  const unitRow = document.getElementById("connect-unit-row");
  const unitSelect = document.getElementById("connect-unit-select");
  unitSelect.innerHTML = "";
  for (const branch of harness.branches) {
    const toInst = getInstance(branch.to_instance);
    if (!toInst) continue;
    const isLoop = isLoopBranch(harness, branch);
    const toName = toInst.label || state.devices[toInst.device_id]?.name || "?";
    unitSelect.appendChild(new Option(isLoop ? `↻ Loop back ${branch.to_connector}` : `${toName}.${branch.to_connector}`, branch.id));
  }
  if (unitSelect.options.length === 0) {
    setStatus("This harness has no destinations yet — connect one from the canvas first.");
    return;
  }
  unitRow.classList.remove("hidden");

  function loadSelectedUnit() {
    const branch = harness.branches.find(b => b.id === unitSelect.value);
    const toConn = { instance_id: branch.to_instance, connector_id: branch.to_connector };
    const toConnector = getConnector(state.devices[getInstance(branch.to_instance).device_id], branch.to_connector);
    // A loop's wires start on the loop's own connector, not the trunk.
    if (isLoopBranch(harness, branch)) applyConnectTarget(harness, toConn, toConn, toConnector, toConnector);
    else applyConnectTarget(harness, fromConn, toConn, fromConnector, toConnector);
  }
  unitSelect.onchange = loadSelectedUnit;
  connectModalState = null;
  loadSelectedUnit();

  connectModal.classList.remove("hidden");
}

function closeConnectModal() {
  connectModal.classList.add("hidden");
  connectModalState = null;
  connectPickRows = [];
  document.getElementById("connect-unit-row").classList.add("hidden");
}

// Pins already claimed by OTHER rows in this modal, plus everything already
// wired elsewhere in the project. In a loopback both ends sit on the same
// connector, so a pin used on either side of another row is taken; `selfPinId`
// also excludes this row's own pin on the opposite side.
function pickRowsUsedKeys(ctx, side, excludeRowId, selfPinId) {
  const used = usedPinKeys();
  if (selfPinId) used.add(pinKey(ctx.instanceId, ctx.connectorId, selfPinId));
  const loop = isSameConnector(connectModalState.fromCtx, connectModalState.toCtx);
  for (const row of connectPickRows) {
    if (row.id === excludeRowId) continue;
    const pinIds = loop ? [row.fromPinId, row.toPinId] : [side === "from" ? row.fromPinId : row.toPinId];
    for (const pinId of pinIds) if (pinId) used.add(pinKey(ctx.instanceId, ctx.connectorId, pinId));
  }
  return used;
}

function availablePins(connector, ctx, side, row, selfPinId) {
  const used = pickRowsUsedKeys(ctx, side, row.id, selfPinId);
  const keep = side === "from" ? row.fromPinId : row.toPinId;
  return connector.pins.filter(p => p.id === keep || !used.has(pinKey(ctx.instanceId, ctx.connectorId, p.id)));
}

// Adds a wire using the first free pin on the from side and, on the to side,
// the first free pin whose signal is allowed with it. Returns false (and adds
// nothing) when either side has no free pin left.
function addPickRow() {
  const { fromConnector, toConnector, fromCtx, toCtx } = connectModalState;
  const loop = isSameConnector(fromCtx, toCtx);
  const row = { id: genId(), fromPinId: null, toPinId: null };
  connectPickRows.push(row);
  const fromOptions = availablePins(fromConnector, fromCtx, "from", row, null);
  row.fromPinId = fromOptions.length ? fromOptions[0].id : null;
  const fromPin = fromConnector.pins.find(p => p.id === row.fromPinId);
  const toOptions = availablePins(toConnector, toCtx, "to", row, loop ? row.fromPinId : null);
  const match = toOptions.find(p => fromPin && signalsCompatible(fromPin.signal, p.signal)) || toOptions[0];
  row.toPinId = match ? match.id : null;
  if (!row.fromPinId || !row.toPinId) {
    connectPickRows = connectPickRows.filter(r => r !== row);
    return false;
  }
  return true;
}

function rowPins(row) {
  const { fromConnector, toConnector } = connectModalState;
  return {
    fromPin: fromConnector.pins.find(p => p.id === row.fromPinId) || null,
    toPin: toConnector.pins.find(p => p.id === row.toPinId) || null,
  };
}

function rowIsCompatible(row) {
  const { fromPin, toPin } = rowPins(row);
  return !fromPin || !toPin || signalsCompatible(fromPin.signal, toPin.signal);
}

function updateCreateConnectButton() {
  const btn = document.getElementById("btn-create-connect");
  if (!connectModalState) { btn.textContent = "Create Harness"; return; }
  const count = connectPickRows.filter(r => r.fromPinId && r.toPinId).length;
  const base = connectModalState.existingHarness ? "Add Wires" : "Create Harness";
  btn.textContent = count > 0 ? `${base} (${count} wire${count === 1 ? "" : "s"})` : base;
}

function pinOptionLabel(pin) {
  return [pin.label, ...pinTags(pin), pin.signal].filter(Boolean).join(" · ");
}

function pickPinSelect(options, value, onChange) {
  const select = document.createElement("select");
  if (options.length === 0) {
    select.appendChild(new Option("— none —", ""));
    select.disabled = true;
  } else {
    for (const p of options) select.appendChild(new Option(pinOptionLabel(p), p.id));
    select.value = value || "";
  }
  select.onchange = () => onChange(select.value || null);
  return select;
}

function renderPickTable() {
  const { fromConnector, toConnector, fromCtx, toCtx } = connectModalState;
  const loop = isSameConnector(fromCtx, toCtx);
  connectPickBodyEl.innerHTML = "";

  if (connectPickRows.length === 0) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 6;
    td.className = "no-rows";
    td.textContent = "No wires yet — click + Add wire.";
    tr.appendChild(td);
    connectPickBodyEl.appendChild(tr);
    updateCreateConnectButton();
    return;
  }

  for (const row of connectPickRows) {
    const tr = document.createElement("tr");
    const { fromPin, toPin } = rowPins(row);
    if (!rowIsCompatible(row)) {
      tr.className = "pick-bad";
      tr.title = `${fromPin.signal} ↔ ${toPin.signal} is not allowed by the Signal Rules`;
    }

    const fromSigTd = document.createElement("td");
    fromSigTd.className = "pick-signal";
    fromSigTd.textContent = (fromPin && fromPin.signal) || "—";
    tr.appendChild(fromSigTd);

    const fromPinTd = document.createElement("td");
    fromPinTd.appendChild(pickPinSelect(
      availablePins(fromConnector, fromCtx, "from", row, loop ? row.toPinId : null), row.fromPinId,
      (v) => { row.fromPinId = v; renderPickTable(); },
    ));
    tr.appendChild(fromPinTd);

    const arrowTd = document.createElement("td");
    arrowTd.className = "pick-arrow";
    arrowTd.textContent = loop ? "↻" : "→";
    tr.appendChild(arrowTd);

    const toPinTd = document.createElement("td");
    toPinTd.appendChild(pickPinSelect(
      availablePins(toConnector, toCtx, "to", row, loop ? row.fromPinId : null), row.toPinId,
      (v) => { row.toPinId = v; renderPickTable(); },
    ));
    tr.appendChild(toPinTd);

    const toSigTd = document.createElement("td");
    toSigTd.className = "pick-signal";
    toSigTd.textContent = (toPin && toPin.signal) || "—";
    tr.appendChild(toSigTd);

    const tdDel = document.createElement("td");
    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "mapping-remove";
    delBtn.textContent = "✕";
    delBtn.title = "Remove this wire";
    delBtn.onclick = () => { connectPickRows = connectPickRows.filter(r => r.id !== row.id); renderPickTable(); };
    tdDel.appendChild(delBtn);
    tr.appendChild(tdDel);

    connectPickBodyEl.appendChild(tr);
  }

  updateCreateConnectButton();
}

document.getElementById("btn-add-wire").onclick = () => {
  if (!addPickRow()) setStatus("No free pins left on one side");
  renderPickTable();
};

// A pairing counts as a match for auto-matching only when both pins have a
// signal and the Signal Rules allow it; blank signals are left for manual picks.
function signalsHaveMatch(sigA, sigB) {
  if (!sigA || !sigB) return false;
  if (!signalHasRules(sigA) && !signalHasRules(sigB)) return sigA.trim().toUpperCase() === sigB.trim().toUpperCase();
  return signalsCompatible(sigA, sigB);
}

// Optional helper: pair every still-free pin whose signal matches, e.g.
// PWR ↔ PWR and GND ↔ GND. Anything it can't match is left for manual picks.
document.getElementById("btn-auto-match").onclick = () => {
  const { fromConnector, toConnector, fromCtx, toCtx } = connectModalState;
  const loop = isSameConnector(fromCtx, toCtx);
  const used = usedPinKeys();
  for (const row of connectPickRows) {
    if (row.fromPinId) used.add(pinKey(fromCtx.instanceId, fromCtx.connectorId, row.fromPinId));
    if (row.toPinId) used.add(pinKey(toCtx.instanceId, toCtx.connectorId, row.toPinId));
  }
  const freeFrom = fromConnector.pins.filter(p => !used.has(pinKey(fromCtx.instanceId, fromCtx.connectorId, p.id)));
  const takenTo = new Set();
  let added = 0;
  for (const fp of freeFrom) {
    if (loop && takenTo.has(fp.id)) continue;
    const tp = toConnector.pins.find(p =>
      !used.has(pinKey(toCtx.instanceId, toCtx.connectorId, p.id)) && !takenTo.has(p.id)
      && !(loop && (p.id === fp.id)) && signalsHaveMatch(fp.signal, p.signal));
    if (!tp) continue;
    takenTo.add(tp.id);
    if (loop) { takenTo.add(fp.id); used.add(pinKey(fromCtx.instanceId, fromCtx.connectorId, tp.id)); }
    connectPickRows.push({ id: genId(), fromPinId: fp.id, toPinId: tp.id });
    added += 1;
  }
  setStatus(added ? `Matched ${added} wire${added === 1 ? "" : "s"} by signal` : "No more free pins with matching signals");
  renderPickTable();
};
document.getElementById("btn-cancel-connect").onclick = () => closeConnectModal();

document.getElementById("btn-create-connect").onclick = () => {
  const { existingHarness, fromConn, toConn, fromConnector, toConnector, reroot } = connectModalState;

  const pinPairs = [];
  for (const row of connectPickRows) {
    if (!row.fromPinId || !row.toPinId) continue;
    const fp = fromConnector.pins.find(p => p.id === row.fromPinId);
    const tp = toConnector.pins.find(p => p.id === row.toPinId);
    if (fp && tp) pinPairs.push([fp, tp]);
  }

  if (pinPairs.length === 0) {
    alert("Pick at least one pin on both sides for a wire.");
    return;
  }
  const bad = connectPickRows.filter(r => r.fromPinId && r.toPinId && !rowIsCompatible(r));
  if (bad.length) {
    alert(`${bad.length} wire${bad.length === 1 ? " connects signals" : "s connect signals"} the Signal Rules don't allow (shown in red). Change or remove ${bad.length === 1 ? "it" : "them"} first.`);
    return;
  }

  const harness = commitBranch(existingHarness, fromConn, toConn, pinPairs, reroot);
  closeConnectModal();
  selectHarness(harness.id);
  const totalWires = harness.branches.reduce((n, b) => n + b.connections.length, 0);
  setStatus(`${harness.label}: ${harness.branches.length} destination(s), ${totalWires} wire(s) total`);
};

function deleteHarness(harnessId) {
  state.project.harnesses = state.project.harnesses.filter(h => h.id !== harnessId);
  selectNone();
  render();
  renderHarnessList();
}

function deleteConnection(harness, branch, connId) {
  branch.connections = branch.connections.filter(c => c.id !== connId);
  if (branch.connections.length === 0) {
    harness.branches = harness.branches.filter(b => b.id !== branch.id);
  }
  if (harness.branches.length === 0) {
    deleteHarness(harness.id);
    return;
  }
  render();
  renderHarnessList();
  renderDrawer();
}

function deleteBranch(harness, branchId) {
  harness.branches = harness.branches.filter(b => b.id !== branchId);
  if (harness.branches.length === 0) {
    deleteHarness(harness.id);
    return;
  }
  render();
  renderProps();
  renderHarnessList();
  renderDrawer();
}

// ---------- Instance placement / deletion ----------

function placeDevice(deviceId, x, y) {
  const device = state.devices[deviceId];
  const { width, height } = deviceBoxSize(device);
  const instance = {
    instance_id: genId(),
    device_id: deviceId,
    device_version: device.version, // records which device version was used when placed
    x: x - width / 2,
    y: y - height / 2,
    label: "",
  };

  // Multiple instances of the same device would otherwise all show the same
  // default name (e.g. three unlabeled "DSUB-9 Receiving Unit"s) — number
  // them "(1)", "(2)", ... so they stay distinguishable everywhere a name is
  // shown. Only touches instances that haven't been given a custom label.
  const existing = state.project.instances.filter(i => i.device_id === deviceId);
  if (existing.length > 0) {
    if (existing.length === 1 && !existing[0].label) {
      existing[0].label = `${device.name} (1)`;
    }
    instance.label = `${device.name} (${existing.length + 1})`;
  }

  state.project.instances.push(instance);
  state.pendingPlaceDeviceId = null;
  document.getElementById("device-select").value = "";
  updateDeviceButtons();
  selectInstance(instance.instance_id);
  setStatus(`Placed ${instance.label || device.name}`);
}

function deleteInstance(instanceId) {
  state.project.instances = state.project.instances.filter(i => i.instance_id !== instanceId);
  const remaining = [];
  for (const harness of state.project.harnesses) {
    if (harness.from_instance === instanceId) continue; // trunk device removed: whole harness goes
    harness.branches = harness.branches.filter(b => b.to_instance !== instanceId);
    if (harness.branches.length > 0) remaining.push(harness);
  }
  state.project.harnesses = remaining;
  selectNone();
  render();
  renderHarnessList();
}

// ---------- Selection / properties panel ----------

function selectInstance(instanceId) {
  state.selectedInstance = instanceId;
  state.selectedHarness = null;
  render();
  renderProps();
  renderHarnessList();
  hideDrawer();
  hidePinoutHint();
}

// Selecting a harness shows a small "Show pinout" hint (at `at`, e.g. where the
// wire was clicked, or else next to the harness); the pinout table itself
// opens in a popup only when the hint is clicked.
function selectHarness(harnessId, at = null) {
  const changed = state.selectedHarness !== harnessId;
  state.selectedHarness = harnessId;
  state.selectedInstance = null;
  if (changed) state.pinoutOpen = false;
  render();
  renderProps();
  renderHarnessList();
  renderDrawer();
  if (!state.pinoutOpen) showPinoutHint(at || harnessAnchor(getHarness(harnessId)));
}

function selectNone() {
  state.selectedInstance = null;
  state.selectedHarness = null;
  renderProps();
  renderHarnessList();
  hideDrawer();
  hidePinoutHint();
}

// A point on the harness line: halfway to its first destination, or at its loop.
function harnessAnchor(harness) {
  const geo = harness && harnessGeometry(harness);
  if (!geo) return null;
  if (geo.branchGeo.length) {
    const to = geo.branchGeo[0].toDevPos;
    return { x: (geo.fromDevPos.x + to.x) / 2, y: (geo.fromDevPos.y + to.y) / 2 };
  }
  if (geo.loopbackGeo.length) return { x: geo.loopbackGeo[0].hairpin.farX, y: geo.loopbackGeo[0].hairpin.centerY };
  return { x: geo.fromDevPos.x, y: geo.fromDevPos.y };
}

function showPinoutHint(at) {
  const hint = document.getElementById("pinout-hint");
  state.hintAt = at;
  if (!at) { hint.classList.add("hidden"); return; }
  hint.classList.remove("hidden");
  positionPinoutHint();
}

function positionPinoutHint() {
  if (!state.hintAt) return;
  const s = toScreen(state.hintAt.x, state.hintAt.y);
  const hint = document.getElementById("pinout-hint");
  hint.style.left = `${Math.round(s.x + 10)}px`;
  hint.style.top = `${Math.round(s.y + 10)}px`;
}

function hidePinoutHint() {
  document.getElementById("pinout-hint").classList.add("hidden");
}

function openPinout() {
  if (!state.selectedHarness) return;
  state.pinoutOpen = true;
  hidePinoutHint();
  renderDrawer();
}

function closePinout() {
  state.pinoutOpen = false;
  hideDrawer();
  if (state.selectedHarness) showPinoutHint(harnessAnchor(getHarness(state.selectedHarness)));
}

document.getElementById("pinout-hint").onclick = openPinout;
// Clicking the dark backdrop around the popup closes it.
document.getElementById("drawer").addEventListener("mousedown", (e) => {
  if (e.target.id === "drawer") closePinout();
});

// The selected device or harness is edited in the bar above the canvas.
function renderProps() {
  const bar = document.getElementById("selection-bar");
  bar.innerHTML = "";

  if (state.selectedInstance) {
    const inst = getInstance(state.selectedInstance);
    const device = state.devices[inst.device_id];
    bar.appendChild(mkInput("Label", inst.label || "", (v) => { inst.label = v; render(); renderHarnessList(); }));
    const info = document.createElement("span");
    info.className = "hint";
    info.textContent = `${device.name}${device.part_number ? " · " + device.part_number : ""}`;
    bar.appendChild(info);
    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.textContent = "Delete device";
    delBtn.className = "danger";
    delBtn.onclick = () => deleteInstance(inst.instance_id);
    bar.appendChild(delBtn);
    return;
  }

  if (state.selectedHarness) {
    const harness = getHarness(state.selectedHarness);
    bar.appendChild(mkInput("Harness", harness.label || "", (v) => { harness.label = v; render(); renderHarnessList(); renderDrawer(); }));
    bar.appendChild(mkInput("Trunk plug", harness.from_harness_connector || "", (v) => { harness.from_harness_connector = v; render(); renderDrawer(); }));
    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.textContent = "Delete harness";
    delBtn.className = "danger";
    delBtn.onclick = () => deleteHarness(harness.id);
    bar.appendChild(delBtn);
    return;
  }

  const hint = document.createElement("span");
  hint.className = "hint";
  hint.textContent = "Select a device or harness on the canvas.";
  bar.appendChild(hint);
}

function mkInput(labelText, value, onChange) {
  const label = document.createElement("label");
  label.textContent = labelText;
  const input = document.createElement("input");
  input.type = "text";
  input.value = value;
  input.oninput = () => onChange(input.value);
  label.appendChild(input);
  return label;
}

// The harness dropdown in the bar above the canvas.
function renderHarnessList() {
  const select = document.getElementById("harness-select");
  select.innerHTML = "";
  select.appendChild(new Option(state.project.harnesses.length ? "Choose a harness…" : "No harnesses yet", ""));
  for (const harness of state.project.harnesses) {
    const fromInst = getInstance(harness.from_instance);
    if (!fromInst) continue;
    const fromName = fromInst.label || state.devices[fromInst.device_id]?.name || "?";
    const totalWires = harness.branches.reduce((n, b) => n + b.connections.length, 0);
    select.appendChild(new Option(`${harness.label || "H"}: ${fromName}.${harness.from_connector} · ${harness.branches.length} dest, ${totalWires} wire(s)`, harness.id));
  }
  select.value = state.selectedHarness || "";
}

document.getElementById("harness-select").onchange = (e) => {
  if (e.target.value) selectHarness(e.target.value);
  else { selectNone(); render(); }
};

// ---------- Pinout popup: dense pinout table ----------

function hideDrawer() {
  document.getElementById("drawer").classList.add("hidden");
}

function renderDrawer() {
  const harness = getHarness(state.selectedHarness);
  const drawer = document.getElementById("drawer");
  if (!harness || !state.pinoutOpen) { drawer.classList.add("hidden"); return; }
  drawer.classList.remove("hidden");

  const fromInst = getInstance(harness.from_instance);
  const fromDevice = state.devices[fromInst.device_id];
  const fromConnector = getConnector(fromDevice, harness.from_connector);
  const fromName = fromInst.label || fromDevice.name;
  const totalWires = harness.branches.reduce((n, b) => n + b.connections.length, 0);
  const fromCtx = { instanceId: harness.from_instance, connectorId: harness.from_connector };
  const usedKeys = usedPinKeys();

  document.getElementById("drawer-title").textContent =
    `${harness.label || "Harness"} — ${fromName}.${harness.from_connector} (↔ ${harness.from_harness_connector}) · ${harness.branches.length} destination(s), ${totalWires} wires`;

  const tbody = document.getElementById("conn-table-body");
  tbody.innerHTML = "";

  // Sub-headers sit right above the Pin columns they describe. The source
  // connector is the same for every destination, so it's only shown once.
  let lastFromHeaderText = null;

  for (const branch of harness.branches) {
    const toInst = getInstance(branch.to_instance);
    if (!toInst) continue;
    const toDevice = state.devices[toInst.device_id];
    const toConnector = getConnector(toDevice, branch.to_connector);
    const toName = toInst.label || toDevice.name;
    const toCtx = { instanceId: branch.to_instance, connectorId: branch.to_connector };
    const isLoopback = isLoopBranch(harness, branch);
    // A loop's pins are both on its own connector (the trunk or another one).
    const rowFromConnector = isLoopback ? toConnector : fromConnector;
    const rowFromCtx = isLoopback ? toCtx : fromCtx;
    const rowFromDevice = isLoopback ? toDevice : fromDevice;

    const fromHeaderText = isLoopback ? `${toName}.${branch.to_connector}` : `${fromName}.${harness.from_connector}`;
    const toHeaderText = isLoopback ? "↻ Loop Back" : `${toName}.${branch.to_connector}`;

    const headerTr = document.createElement("tr");
    headerTr.className = "branch-header-row";

    const leadTd = document.createElement("td");
    leadTd.colSpan = 3; // Owner(from), Verify(from), Tag(from)
    headerTr.appendChild(leadTd);

    const fromHeadTd = document.createElement("td");
    fromHeadTd.className = "sub-header-cell";
    if (fromHeaderText !== lastFromHeaderText || isLoopback) {
      fromHeadTd.innerHTML = `<span class="end-name">${escapeHtml(fromHeaderText)}</span>${connectorPartHtml(rowFromConnector)}`;
      lastFromHeaderText = fromHeaderText;
    }
    headerTr.appendChild(fromHeadTd);

    const toHeadTd = document.createElement("td");
    toHeadTd.className = "sub-header-cell";
    // Every destination is its own connector (even when two units share a name), so it's always labelled.
    toHeadTd.innerHTML = `<span class="end-name">${escapeHtml(toHeaderText)}</span>${isLoopback ? "" : connectorPartHtml(toConnector)}`;
    headerTr.appendChild(toHeadTd);

    const trailTd = document.createElement("td");
    trailTd.colSpan = 7; // Tag(to), Verify(to), Owner(to), Set, Color, AWG, Length
    headerTr.appendChild(trailTd);

    const headerActionTd = document.createElement("td");
    const removeBranchBtn = document.createElement("button");
    removeBranchBtn.className = "conn-del";
    removeBranchBtn.textContent = "✕ destination";
    removeBranchBtn.title = "Remove this destination and its wires";
    removeBranchBtn.onclick = () => deleteBranch(harness, branch.id);
    headerActionTd.appendChild(removeBranchBtn);
    headerTr.appendChild(headerActionTd);
    tbody.appendChild(headerTr);

    for (const conn of branch.connections) {
      const tr = document.createElement("tr");
      // A loopback wire takes two rows: both of its pins on the left (they're
      // on the same connector), everything else merged over the two rows.
      const span = isLoopback ? 2 : 1;
      const merge = td => { td.rowSpan = span; return td; };

      const tdFromOwner = document.createElement("td");
      tdFromOwner.className = "conn-owner";
      tdFromOwner.textContent = userName(rowFromDevice && rowFromDevice.responsible_user_id);
      tr.appendChild(merge(tdFromOwner));

      const tdFromVerify = document.createElement("td");
      const fromVerifyInput = document.createElement("input");
      fromVerifyInput.type = "checkbox";
      fromVerifyInput.checked = !!conn.from_verified;
      fromVerifyInput.title = `Verified by ${userName(rowFromDevice && rowFromDevice.responsible_user_id)}`;
      fromVerifyInput.onchange = () => { conn.from_verified = fromVerifyInput.checked; };
      tdFromVerify.appendChild(fromVerifyInput);
      tr.appendChild(merge(tdFromVerify));

      tr.appendChild(tagCell(rowFromCtx, conn.from_pin));
      tr.appendChild(pinSelectCell(rowFromConnector, rowFromCtx, conn.from_pin, usedKeys, (v) => { conn.from_pin = v; renderDrawer(); }));

      let loopSecondRow = null;
      if (isLoopback) {
        // The other side (where the far connector's pins would go) just says
        // "loop back", merged over the wire's two rows.
        const tdLoop = document.createElement("td");
        tdLoop.colSpan = 4; // Pin(to), Tag(to), Verify(to), Owner(to)
        tdLoop.rowSpan = 2;
        tdLoop.className = "loop-indicator-cell";
        tdLoop.textContent = "↻ Loop back";
        tr.appendChild(tdLoop);
        loopSecondRow = document.createElement("tr");
        loopSecondRow.className = "loop-second-pin";
        loopSecondRow.appendChild(tagCell(toCtx, conn.to_pin));
        loopSecondRow.appendChild(pinSelectCell(toConnector, toCtx, conn.to_pin, usedKeys, (v) => { conn.to_pin = v; renderDrawer(); }));
      } else {
        tr.appendChild(pinSelectCell(toConnector, toCtx, conn.to_pin, usedKeys, (v) => { conn.to_pin = v; renderDrawer(); }));
        tr.appendChild(tagCell(toCtx, conn.to_pin));

        const tdToVerify = document.createElement("td");
        const toVerifyInput = document.createElement("input");
        toVerifyInput.type = "checkbox";
        toVerifyInput.checked = !!conn.to_verified;
        toVerifyInput.title = `Verified by ${userName(toDevice && toDevice.responsible_user_id)}`;
        toVerifyInput.onchange = () => { conn.to_verified = toVerifyInput.checked; };
        tdToVerify.appendChild(toVerifyInput);
        tr.appendChild(tdToVerify);

        const tdToOwner = document.createElement("td");
        tdToOwner.className = "conn-owner";
        tdToOwner.textContent = userName(toDevice && toDevice.responsible_user_id);
        tr.appendChild(tdToOwner);
      }

      const tdSet = document.createElement("td");
      tdSet.className = "set-cell";
      tdSet.textContent = wireSetText(pinOf(rowFromCtx.instanceId, rowFromCtx.connectorId, conn.from_pin),
                                      pinOf(toCtx.instanceId, toCtx.connectorId, conn.to_pin));
      tr.appendChild(merge(tdSet));

      const tdColor = document.createElement("td");
      tdColor.className = "color-cell";
      const swatch = colorSwatch(conn.color);
      const colorInput = document.createElement("input");
      colorInput.type = "text";
      colorInput.setAttribute("list", "wire-colors");
      colorInput.placeholder = "e.g. Red";
      colorInput.value = conn.color || "";
      colorInput.oninput = () => {
        conn.color = colorInput.value;
        const next = colorSwatch(conn.color);
        swatch.className = next.className;
        swatch.style.background = next.style.background;
      };
      tdColor.append(swatch, colorInput);
      tr.appendChild(merge(tdColor));

      const tdAwg = document.createElement("td");
      const awgInput = document.createElement("input");
      awgInput.type = "text";
      awgInput.setAttribute("list", "awg-list");
      awgInput.value = conn.awg || "";
      awgInput.oninput = () => { conn.awg = awgInput.value; };
      tdAwg.appendChild(awgInput);
      tr.appendChild(merge(tdAwg));

      const tdLength = document.createElement("td");
      const lengthInput = document.createElement("input");
      lengthInput.type = "text";
      lengthInput.placeholder = "e.g. 1.5m";
      lengthInput.value = conn.length || "";
      lengthInput.oninput = () => { conn.length = lengthInput.value; };
      tdLength.appendChild(lengthInput);
      tr.appendChild(merge(tdLength));

      const tdDel = document.createElement("td");
      const delBtn = document.createElement("button");
      delBtn.className = "conn-del";
      delBtn.textContent = "✕";
      delBtn.title = "Delete connection";
      delBtn.onclick = () => deleteConnection(harness, branch, conn.id);
      tdDel.appendChild(delBtn);
      tr.appendChild(merge(tdDel));

      tbody.appendChild(tr);
      if (loopSecondRow) tbody.appendChild(loopSecondRow);
    }
  }

  renderAddConnRow(harness);
}

// Pin dropdown for an existing connection's from/to cell: only lists pins
// that are available (not wired elsewhere) plus whichever pin is currently
// assigned here, so the field never loses its own selection.
function pinSelectCell(connector, ctx, selectedPinId, usedKeys, onChange) {
  const td = document.createElement("td");
  td.className = "pin-cell";
  const select = document.createElement("select");
  for (const pin of connector.pins) {
    const isUsedElsewhere = usedKeys.has(pinKey(ctx.instanceId, ctx.connectorId, pin.id)) && pin.id !== selectedPinId;
    if (isUsedElsewhere) continue;
    const opt = document.createElement("option");
    opt.value = pin.id;
    opt.textContent = pin.signal ? `${pin.label} (${pin.signal})` : pin.label;  // the tag has its own column
    if (pin.id === selectedPinId) opt.selected = true;
    select.appendChild(opt);
  }
  select.onchange = () => onChange(select.value);
  td.appendChild(select);
  return td;
}

// Opens the connect popup, with a Destination selector for which branch of
// this harness gets the new wires.
// The connector's part under its name in the table: part number (a link to
// the Part page in Osmia) and type, e.g. "DB25-F · D-sub 25 (female)".
function connectorPartHtml(connector) {
  const part = connector && connector.part;
  if (!part) return "";
  const type = part.type && part.type !== part.part_number ? ` · ${escapeHtml(part.type)}` : "";
  return `<div class="conn-part"><a href="${escapeHtml(part.url)}" target="_blank" title="Open ${escapeHtml(part.part_number)} in Osmia">${escapeHtml(part.part_number)}</a>${type}</div>`;
}

function renderAddConnRow(harness) {
  const container = document.getElementById("add-conn-row");
  container.innerHTML = "";
  if (harness.branches.length === 0) return;

  const addBtn = document.createElement("button");
  addBtn.textContent = "+ Add wires";
  addBtn.onclick = () => openAddWiresModalForHarness(harness);
  container.appendChild(addBtn);
}

document.getElementById("btn-close-drawer").onclick = () => closePinout();

// ---------- CSV export of the harness table ----------
// One row per wire, in the same order as the table, with
// both ends spelled out so the file stands on its own in a spreadsheet. Uses
// the harness as it is on screen, including edits that aren't saved yet.

const CSV_COLUMNS = [
  "Harness", "Destination", "Loopback",
  "From device", "From connector", "From connector part", "From connector type", "From mating connector", "From pin", "From signal", "From owner", "From verified",
  "To device", "To connector", "To connector part", "To connector type", "To mating connector", "To pin", "To signal", "To owner", "To verified",
  "From tag", "To tag", "Set", "Wire color", "AWG", "Length",
];

function harnessCsvRows(harness) {
  const fromInst = getInstance(harness.from_instance);
  const fromDevice = state.devices[fromInst.device_id];
  const fromConnector = getConnector(fromDevice, harness.from_connector);
  const fromName = fromInst.label || fromDevice.name;
  const pinDef = (connector, pinId) => (connector && connector.pins.find(p => p.id === pinId)) || null;
  const yesNo = value => (value ? "yes" : "no");
  const partNumber = connector => (connector && connector.part ? connector.part.part_number : "");
  const partType = connector => (connector && connector.part ? connector.part.type : "");

  const rows = [];
  harness.branches.forEach((branch, branchIdx) => {
    const toInst = getInstance(branch.to_instance);
    if (!toInst) return;
    const toDevice = state.devices[toInst.device_id];
    const toConnector = getConnector(toDevice, branch.to_connector);
    const toName = toInst.label || toDevice.name;
    const isLoopback = isLoopBranch(harness, branch);
    const endConnector = isLoopback ? toConnector : fromConnector;
    const endName = isLoopback ? toName : fromName;
    const endConnectorId = isLoopback ? branch.to_connector : harness.from_connector;
    const endPlug = isLoopback ? branch.to_harness_connector : harness.from_harness_connector;
    const endDevice = isLoopback ? toDevice : fromDevice;

    const endCtx = isLoopback
      ? { instanceId: branch.to_instance, connectorId: branch.to_connector }
      : { instanceId: harness.from_instance, connectorId: harness.from_connector };
    const toCtx = { instanceId: branch.to_instance, connectorId: branch.to_connector };
    const tagOf = (c, pinId) => { const t = effectiveTag(c.instanceId, c.connectorId, pinId); return t ? t.tag : ""; };
    for (const conn of branch.connections) {
      const fp = pinDef(endConnector, conn.from_pin);
      const tp = pinDef(toConnector, conn.to_pin);
      rows.push([
        harness.label || "Harness", branchIdx + 1, yesNo(isLoopback),
        endName, endConnectorId, partNumber(endConnector), partType(endConnector), endPlug || "",
        fp ? fp.label : conn.from_pin, fp ? fp.signal : "",
        userName(endDevice.responsible_user_id), yesNo(conn.from_verified),
        toName, branch.to_connector, partNumber(toConnector), partType(toConnector),
        isLoopback ? endPlug || "" : branch.to_harness_connector || "",
        tp ? tp.label : conn.to_pin, tp ? tp.signal : "",
        userName(toDevice.responsible_user_id), yesNo(isLoopback ? conn.from_verified : conn.to_verified),
        tagOf(endCtx, conn.from_pin), tagOf(toCtx, conn.to_pin),
        wireSetText(fp, tp) === "—" ? "" : wireSetText(fp, tp), conn.color || "", conn.awg || "", conn.length || "",
      ]);
    }
  });
  return rows;
}

function toCsv(rows) {
  const cell = value => {
    const s = value === null || value === undefined ? "" : String(value);
    return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return rows.map(r => r.map(cell).join(",")).join("\r\n") + "\r\n";
}

function downloadText(filename, text, mime) {
  // The BOM makes Excel read the file as UTF-8 (↔, ↻, accented names, ...).
  const blob = new Blob(["\ufeff" + text], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

document.getElementById("btn-export-csv").onclick = () => {
  const harness = getHarness(state.selectedHarness);
  if (!harness) return;
  const rows = harnessCsvRows(harness);
  if (rows.length === 0) { setStatus("This harness has no wires to export yet"); return; }
  const safe = s => String(s).replace(/[\\/:*?"<>|]+/g, "_").trim();
  const projectName = document.getElementById("project-name").value || state.project.name || "Harness";
  downloadText(`${safe(projectName)} - ${safe(harness.label || "Harness")}.csv`, toCsv([CSV_COLUMNS, ...rows]), "text/csv;charset=utf-8");
  setStatus(`Exported ${rows.length} wire${rows.length === 1 ? "" : "s"} from ${harness.label || "harness"}`);
};

// ---------- Order parts ----------
// A harness needs a plug at each connector it goes to. "Order parts" lists
// those ends with a part for each (remembered on the harness, suggesting the
// device connector's own part until one is picked) and makes a draft order in
// Osmia's Orders module.

let partsLoaded = false;
async function loadParts() {
  if (partsLoaded) return;
  const res = await api("/api/parts");
  if (!res.ok) return;
  const { parts } = await res.json();
  const list = document.getElementById("part-list");
  list.innerHTML = "";
  for (const p of parts) {
    const opt = document.createElement("option");
    opt.value = p.part_number;
    opt.label = p.name || p.part_number;
    list.appendChild(opt);
  }
  partsLoaded = true;
}

// One entry per connector the harness plugs into: the trunk, each destination,
// and loops on connectors that aren't already an end (a loop on the trunk or a
// destination shares that plug).
function harnessEnds(harness) {
  const ends = [];
  const seen = new Set();
  const add = (instanceId, connectorId, plug, holder, key) => {
    const k = `${instanceId}::${connectorId}`;
    if (seen.has(k)) return;
    seen.add(k);
    const inst = getInstance(instanceId);
    const device = inst && state.devices[inst.device_id];
    if (!device) return;
    const connector = getConnector(device, connectorId);
    ends.push({
      name: `${inst.label || device.name}.${connectorId}`, plug: plug || "", holder, key,
      suggested: connector && connector.part ? connector.part.part_number : "",
    });
  };
  add(harness.from_instance, harness.from_connector, harness.from_harness_connector, harness, "from_plug_part");
  for (const b of harness.branches) if (!isLoopBranch(harness, b)) add(b.to_instance, b.to_connector, b.to_harness_connector, b, "to_plug_part");
  for (const b of harness.branches) if (isLoopBranch(harness, b)) add(b.to_instance, b.to_connector, b.to_harness_connector, b, "to_plug_part");
  return ends;
}

const orderModal = document.getElementById("order-modal");
let orderEnds = [];

function openOrderModal() {
  const harness = getHarness(state.selectedHarness);
  if (!harness) return;
  loadParts();
  orderEnds = harnessEnds(harness);
  document.getElementById("order-modal-title").textContent = `Order parts for ${harness.label || "harness"}`;
  const body = document.getElementById("order-rows");
  body.innerHTML = "";
  for (const end of orderEnds) {
    const tr = document.createElement("tr");
    const nameTd = document.createElement("td");
    nameTd.textContent = end.name;
    const plugTd = document.createElement("td");
    plugTd.textContent = end.plug || "—";
    const partTd = document.createElement("td");
    const input = document.createElement("input");
    input.type = "text";
    input.setAttribute("list", "part-list");
    input.placeholder = "Part number";
    input.value = end.holder[end.key] || end.suggested;
    if (!end.holder[end.key] && end.suggested) {
      input.classList.add("suggested");
      input.title = "The device connector's own part; the plug that mates with it may be a different part.";
    }
    input.oninput = () => input.classList.remove("suggested");
    end.input = input;
    partTd.appendChild(input);
    const qtyTd = document.createElement("td");
    const qty = document.createElement("input");
    qty.type = "number";
    qty.min = "1";
    qty.value = "1";
    qty.className = "qty";
    end.qty = qty;
    qtyTd.appendChild(qty);
    tr.append(nameTd, plugTd, partTd, qtyTd);
    body.appendChild(tr);
  }
  orderModal.classList.remove("hidden");
}

async function createOrder() {
  const harness = getHarness(state.selectedHarness);
  if (!harness) return;
  const lines = [];
  for (const end of orderEnds) {
    const number = end.input.value.trim();
    end.holder[end.key] = number;  // remembered for next time (saved with the project)
    if (number) lines.push({ part_number: number, quantity: parseInt(end.qty.value, 10) || 1 });
  }
  if (!lines.length) { setStatus("Pick a part for at least one plug"); return; }
  const projectName = document.getElementById("project-name").value || state.project.name || "harness project";
  const res = await api("/api/order", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ lines, note: `Parts for harness ${harness.label || ""} in ${projectName}.` }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) { setStatus(data.error || "Couldn't create the order"); return; }
  orderModal.classList.add("hidden");
  window.open(data.url, "_blank");
  setStatus(`Draft order ${data.number} created` + (data.missing && data.missing.length ? ` (not in Inventory: ${data.missing.join(", ")})` : ""));
}

document.getElementById("btn-order-parts").onclick = openOrderModal;
document.getElementById("btn-cancel-order").onclick = () => orderModal.classList.add("hidden");
document.getElementById("btn-create-order").onclick = () => createOrder();

// ---------- Device library ----------

async function loadDeviceLibrary() {
  const res = await api("/api/devices");
  const list = await res.json();
  for (const d of list) state.devices[d.id] = d;

  const select = document.getElementById("device-select");
  const previous = select.value;
  select.innerHTML = "";
  select.appendChild(new Option("Choose a device to place…", ""));
  for (const d of [...list].sort((a, b) => a.name.localeCompare(b.name))) {
    const pinCount = d.connectors.reduce((n, c) => n + c.pins.length, 0);
    const pn = d.part_number ? ` · ${d.part_number}` : "";
    select.appendChild(new Option(`${d.name}${pn} — ${d.connectors.length} connector(s), ${pinCount} pins · v${d.version}`, d.id));
  }
  if ([...select.options].some(o => o.value === previous)) select.value = previous;
  updateDeviceButtons();
}

function selectedLibraryDevice() {
  const id = document.getElementById("device-select").value;
  return id ? state.devices[id] : null;
}

// Devices are created and edited only in the Devices module; the designer
// just links there for the chosen device.
function updateDeviceButtons() {
  const device = selectedLibraryDevice();
  const open = document.getElementById("btn-open-device");
  open.classList.toggle("hidden", !(device && device.url));
  open.href = device && device.url ? device.url : "#";
}

document.getElementById("device-select").onchange = () => {
  const device = selectedLibraryDevice();
  state.pendingPlaceDeviceId = device ? device.id : null;
  setStatus(device ? `Click the canvas to place "${device.name}"` : "");
  updateDeviceButtons();
};

// Pick up devices added or edited in the Devices module (usually in another
// tab): on the reload button, and whenever the designer tab gets focus again.
let reloadingLibrary = false;
async function reloadDevices() {
  if (reloadingLibrary) return;
  reloadingLibrary = true;
  try {
    await loadDeviceLibrary();
    render();
    renderProps();
    renderHarnessList();
    renderDrawer();
  } finally {
    reloadingLibrary = false;
  }
}
document.getElementById("btn-center-view").onclick = centerView;
document.getElementById("zoom-level").onclick = () => {
  const { w, h } = viewportSize();
  zoomAt(w / 2, h / 2, 1 / view.scale);  // back to 100%
};

document.getElementById("btn-reload-devices").onclick = async () => {
  await reloadDevices();
  setStatus("Devices reloaded");
};
window.addEventListener("focus", reloadDevices);

function userName(userId) {
  if (!userId) return "Unassigned";
  const u = state.users.find(u => u.id === userId);
  return u ? u.name : "Unknown";
}

async function ensureDevicesLoaded(ids) {
  for (const id of ids) {
    if (!state.devices[id]) {
      const res = await api(`/api/devices/${id}`);
      if (res.ok) state.devices[id] = await res.json();
    }
  }
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

// ---------- Signal Rules modal (global signal-type compatibility) ----------

const signalRulesModal = document.getElementById("signal-rules-modal");
const signalRuleRowsEl = document.getElementById("signal-rule-rows");

async function loadSignalRules() {
  const res = await api("/api/signal-rules");
  const data = await res.json();
  state.signalRules = data.pairs || [];
}

function addRuleRow(sigA, sigB) {
  const row = document.createElement("div");
  row.className = "rule-row";
  row.innerHTML = `
    <input type="text" class="rule-a" placeholder="e.g. PWR" value="${escapeHtml(sigA || "")}">
    <span>↔</span>
    <input type="text" class="rule-b" placeholder="e.g. PWR" value="${escapeHtml(sigB || "")}">
    <button type="button" class="rule-remove">✕</button>
  `;
  row.querySelector(".rule-remove").onclick = () => row.remove();
  signalRuleRowsEl.appendChild(row);
}

function openSignalRulesModal() {
  signalRuleRowsEl.innerHTML = "";
  if (state.signalRules.length === 0) {
    addRuleRow("", "");
  } else {
    for (const [a, b] of state.signalRules) addRuleRow(a, b);
  }
  signalRulesModal.classList.remove("hidden");
}

document.getElementById("btn-signal-rules").onclick = openSignalRulesModal;
document.getElementById("btn-add-rule").onclick = () => addRuleRow("", "");
document.getElementById("btn-cancel-rules").onclick = () => signalRulesModal.classList.add("hidden");

document.getElementById("btn-save-rules").onclick = async () => {
  const pairs = [...signalRuleRowsEl.querySelectorAll(".rule-row")]
    .map(row => [row.querySelector(".rule-a").value.trim(), row.querySelector(".rule-b").value.trim()])
    .filter(([a, b]) => a && b);

  const res = await api("/api/signal-rules", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pairs }),
  });
  const saved = await res.json();
  state.signalRules = saved.pairs || [];
  signalRulesModal.classList.add("hidden");
  renderDrawer();
  setStatus("Signal rules saved");
};

// ---------- Project save / load ----------

async function refreshProjectSelect() {
  const res = await api("/api/projects");
  const list = await res.json();
  const select = document.getElementById("project-select");
  select.innerHTML = '<option value="">Load project…</option>';
  for (const p of list) {
    const opt = document.createElement("option");
    opt.value = p.id;
    opt.textContent = p.name;
    select.appendChild(opt);
  }
}

function updateProjectVersionBadge() {
  const badge = document.getElementById("project-version-badge");
  const versionsBtn = document.getElementById("btn-project-versions");
  const imagesBtn = document.getElementById("btn-project-images");
  if (state.project.id && state.project.version) {
    badge.textContent = `v${state.project.version}`;
    badge.classList.remove("hidden");
    versionsBtn.classList.remove("hidden");
  } else {
    badge.classList.add("hidden");
    versionsBtn.classList.add("hidden");
  }
  // Photos live on a page of their own, for saved projects.
  if (imagesBtn) {
    const template = document.getElementById("harness-app").dataset.imagesUrl || "";
    imagesBtn.classList.toggle("hidden", !(state.project.id && template));
    if (state.project.id) imagesBtn.href = template.replace("/0/", `/${state.project.id}/`);
  }
}

document.getElementById("btn-new-project").onclick = () => {
  state.project = { id: null, name: "Untitled Harness", instances: [], harnesses: [] };
  document.getElementById("project-name").value = state.project.name;
  document.getElementById("project-select").value = "";
  updateProjectVersionBadge();
  selectNone();
  centerView();
  setStatus("New project");
};

document.getElementById("project-name").oninput = (e) => {
  state.project.name = e.target.value;
};

document.getElementById("btn-save-project").onclick = async () => {
  state.project.name = document.getElementById("project-name").value || "Untitled Harness";
  const res = await api("/api/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(state.project),
  });
  const saved = await res.json();
  state.project = saved;
  await refreshProjectSelect();
  document.getElementById("project-select").value = saved.id;
  updateProjectVersionBadge();
  setStatus(`Project saved as v${saved.version}`);
};

document.getElementById("project-select").onchange = async (e) => {
  const id = e.target.value;
  if (!id) return;
  const res = await api(`/api/projects/${id}`);
  if (!res.ok) return;
  const project = await res.json();
  await ensureDevicesLoaded(project.instances.map(i => i.device_id));
  state.project = project;
  document.getElementById("project-name").value = project.name;
  updateProjectVersionBadge();
  selectNone();
  centerView();
  setStatus(`Loaded "${project.name}"`);
};

document.getElementById("btn-project-versions").onclick = () => {
  if (!state.project.id) return;
  openVersionsModal(state.project.id, state.project.version);
};

document.getElementById("btn-delete-project").onclick = async () => {
  if (!state.project.id) { setStatus("Nothing to delete"); return; }
  if (!confirm(`Delete project "${state.project.name}"? This deletes ALL of its versions and cannot be undone.`)) return;
  await api(`/api/projects/${state.project.id}`, { method: "DELETE" });
  state.project = { id: null, name: "Untitled Harness", instances: [], harnesses: [] };
  document.getElementById("project-name").value = state.project.name;
  await refreshProjectSelect();
  updateProjectVersionBadge();
  selectNone();
  render();
  setStatus("Project deleted");
};

// ---------- Users (no auth — a name registry for device ownership / verification) ----------

async function loadUsers() {
  const res = await api("/api/users");
  const data = await res.json();
  state.users = data.users || [];
}

function addUserRow(id, name) {
  const row = document.createElement("div");
  row.className = "rule-row";
  row.dataset.userId = id || genId();
  row.innerHTML = `
    <input type="text" class="user-name" placeholder="Name" value="${escapeHtml(name || "")}">
    <button type="button" class="rule-remove">✕</button>
  `;
  row.querySelector(".rule-remove").onclick = () => row.remove();
  document.getElementById("user-rows").appendChild(row);
}

function openUsersModal() {
  const container = document.getElementById("user-rows");
  container.innerHTML = "";
  if (state.users.length === 0) addUserRow(null, "");
  else for (const u of state.users) addUserRow(u.id, u.name);
  document.getElementById("users-modal").classList.remove("hidden");
}

document.getElementById("btn-users").onclick = () => window.open(OSMIA.usersUrl, "_blank");  // Osmia: users live in the Users module
document.getElementById("btn-add-user").onclick = () => addUserRow(null, "");
document.getElementById("btn-cancel-users").onclick = () => document.getElementById("users-modal").classList.add("hidden");

document.getElementById("btn-save-users").onclick = async () => {
  const rows = [...document.getElementById("user-rows").querySelectorAll(".rule-row")];
  const users = rows
    .map(row => ({ id: row.dataset.userId, name: row.querySelector(".user-name").value.trim() }))
    .filter(u => u.name);

  const res = await api("/api/users", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ users }),
  });
  const saved = await res.json();
  state.users = saved.users || [];
  document.getElementById("users-modal").classList.add("hidden");
  await loadDeviceLibrary();
  renderProps();
  setStatus("Users saved");
};

// ---------- Versions modal (projects) ----------
// Device versions live in the Devices module.

const versionsModal = document.getElementById("versions-modal");

async function openVersionsModal(id, currentVersion) {
  document.getElementById("versions-modal-title").textContent = "Project Versions";
  const listEl = document.getElementById("versions-list");
  listEl.innerHTML = "<p class=\"hint\">Loading…</p>";
  versionsModal.classList.remove("hidden");

  const res = await api(`/api/projects/${id}/versions`);
  const data = await res.json();
  const versions = (data.versions || []).slice().sort((a, b) => b - a);

  listEl.innerHTML = "";
  for (const v of versions) {
    const row = document.createElement("div");
    row.className = "version-row";
    const label = document.createElement("span");
    label.textContent = `v${v}`;
    if (v === currentVersion) {
      const tag = document.createElement("span");
      tag.className = "version-current";
      tag.textContent = "CURRENT";
      label.appendChild(tag);
      row.appendChild(label);
    } else {
      row.appendChild(label);
      const activateBtn = document.createElement("button");
      activateBtn.textContent = "Make Current";
      activateBtn.onclick = () => activateProjectVersion(id, v);
      row.appendChild(activateBtn);
    }
    listEl.appendChild(row);
  }
}

async function activateProjectVersion(id, version) {
  const res = await api(`/api/projects/${id}/versions/${version}/activate`, { method: "POST" });
  if (!res.ok) { setStatus("Could not activate that version"); return; }
  const data = await res.json();
  await ensureDevicesLoaded(data.instances.map(i => i.device_id));
  state.project = data;
  document.getElementById("project-name").value = data.name;
  updateProjectVersionBadge();
  selectNone();
  centerView();
  versionsModal.classList.add("hidden");
  setStatus(`Project switched to v${version}`);
}

document.getElementById("btn-close-versions").onclick = () => versionsModal.classList.add("hidden");

// ---------- Init ----------

(async function init() {
  resizeCanvas();
  await loadSignalRules();
  await loadUsers();
  await loadDeviceLibrary();
  await refreshProjectSelect();
  render();
  renderHarnessList();
  const wanted = new URLSearchParams(location.search).get("project");
  if (wanted) {
    const select = document.getElementById("project-select");
    select.value = wanted;
    if (select.value === wanted) select.onchange({ target: select });
  }
})();
