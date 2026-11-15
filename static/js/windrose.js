// Wind rose: inline SVG drawn from /site/wind-rose (how often the wind blew
// from each of 16 directions, stacked by average speed, with calm readings
// in the centre). Speed classes, their labels and the calm threshold all
// come from the endpoint. Colours come from CSS (.rose-c0 ... .rose-c5,
// built from the theme tokens in site.css), so the rose follows the theme
// without any script.
//
// Used by the History page (history.js calls loadWindRose for the selected
// range) and, for the last 24 hours, the current page: any element with
// data-wind-rose="auto" loads itself and refreshes every few minutes.

const ROSE_SVG_NS = "http://www.w3.org/2000/svg";
const ROSE_SIZE = 400;
const ROSE_CENTER = ROSE_SIZE / 2;
const ROSE_OUTER = 158; // radius of the outermost ring
const ROSE_CALM = 24; // radius of the calm circle in the centre
const ROSE_GAP = 2; // surface gap between neighbouring segments, px
const ROSE_RAMP_STEPS = 6; // .rose-c0 ... .rose-c5 in site.css
const ROSE_AUTO_REFRESH_MS = 5 * 60 * 1000;

function roseSvg(tag, attrs = {}) {
  const el = document.createElementNS(ROSE_SVG_NS, tag);
  Object.entries(attrs).forEach(([key, value]) => el.setAttribute(key, value));
  return el;
}

function rosePoint(radius, degrees) {
  const rad = (degrees * Math.PI) / 180;
  return [ROSE_CENTER + radius * Math.sin(rad), ROSE_CENTER - radius * Math.cos(rad)];
}

// An annular wedge from inner to outer radius, centred on `bearing`, with a
// constant-width gap on each side (an angular inset that shrinks as the
// radius grows), so neighbouring sectors are separated by a straight gap.
function roseWedgePath(inner, outer, bearing, halfWidth) {
  const inset = (radius) => ((ROSE_GAP / 2 / Math.max(radius, 1)) * 180) / Math.PI;
  const innerHalf = Math.max(halfWidth - inset(inner), 0.5);
  const outerHalf = Math.max(halfWidth - inset(outer), 0.5);
  const [x1, y1] = rosePoint(outer, bearing - outerHalf);
  const [x2, y2] = rosePoint(outer, bearing + outerHalf);
  const [x3, y3] = rosePoint(inner, bearing + innerHalf);
  const [x4, y4] = rosePoint(inner, bearing - innerHalf);
  const f = (n) => n.toFixed(2);
  return (
    `M${f(x1)} ${f(y1)}A${outer} ${outer} 0 0 1 ${f(x2)} ${f(y2)}` +
    `L${f(x3)} ${f(y3)}A${inner} ${inner} 0 0 0 ${f(x4)} ${f(y4)}Z`
  );
}

// Ring spacing: the smallest "nice" step that covers the busiest sector in
// at most four rings.
function roseRingStep(maxShare) {
  const steps = [0.01, 0.02, 0.025, 0.05, 0.1, 0.15, 0.2, 0.25, 0.5];
  return steps.find((step) => maxShare / step <= 4) || 0.5;
}

// Ring steps are round numbers: "5%", "2.5%".
function formatRoseRing(share) {
  return `${Number((share * 100).toFixed(1))}%`;
}

function formatRoseShare(share) {
  if (share === 0) return "0%";
  const percent = share * 100;
  if (percent < 0.1) return "<0.1%";
  return `${percent.toFixed(percent < 10 ? 1 : 0)}%`;
}

// Ramp step for speed class i of n, spread over the CSS ramp so the first
// and last classes always take its two ends.
function roseClassStep(index, count) {
  if (count <= 1) return 0;
  return Math.round((index * (ROSE_RAMP_STEPS - 1)) / (count - 1));
}

function roseBearingRange(index, data) {
  const width = data.sector_degrees;
  const from = (index * width - width / 2 + 360) % 360;
  const to = index * width + width / 2;
  return `${Math.round(from * 10) / 10}°–${Math.round(to * 10) / 10}°`;
}

// Builds the whole rose (SVG, legend, summary, table) inside `host`.
function renderWindRose(host, data) {
  host.textContent = "";
  host.classList.add("windrose");

  const classes = data.speed_classes;
  const total = data.total;
  const sectorTotals = data.counts.map((row) => row.reduce((a, b) => a + b, 0));
  const share = (count) => (total ? count / total : 0);
  const maxShare = Math.max(0, ...sectorTotals.map(share));
  const ringStep = roseRingStep(maxShare || 0.05);
  const ringMax = Math.max(ringStep, Math.ceil(maxShare / ringStep - 1e-9) * ringStep);
  const radiusFor = (s) => ROSE_CALM + ((ROSE_OUTER - ROSE_CALM) * s) / ringMax;
  const halfWidth = data.sector_degrees / 2;

  const body = document.createElement("div");
  body.className = "windrose-body";
  host.appendChild(body);

  // --- plot ---------------------------------------------------------------
  const plot = document.createElement("div");
  plot.className = "windrose-plot";
  body.appendChild(plot);

  const svg = roseSvg("svg", {
    viewBox: `0 0 ${ROSE_SIZE} ${ROSE_SIZE}`,
    role: "group",
    "aria-label": total
      ? `Wind rose: ${total.toLocaleString()} readings, ${formatRoseShare(share(data.calm))} calm. ` +
        "The table below has every value."
      : "Wind rose: no wind readings in this range",
  });
  plot.appendChild(svg);

  const grid = roseSvg("g", { class: "rose-grid", "aria-hidden": "true" });
  svg.appendChild(grid);
  for (let ring = ringStep; ring <= ringMax + 1e-9; ring += ringStep) {
    grid.appendChild(roseSvg("circle", { cx: ROSE_CENTER, cy: ROSE_CENTER, r: radiusFor(ring) }));
  }
  for (let i = 0; i < 8; i++) {
    const [x1, y1] = rosePoint(ROSE_CALM, i * 45);
    const [x2, y2] = rosePoint(ROSE_OUTER, i * 45);
    grid.appendChild(roseSvg("line", { x1, y1, x2, y2 }));
  }

  // Compass labels: cardinal points stronger than the intercardinals.
  const labels = roseSvg("g", { class: "rose-labels", "aria-hidden": "true" });
  svg.appendChild(labels);
  ["N", "NE", "E", "SE", "S", "SW", "W", "NW"].forEach((name, i) => {
    const [x, y] = rosePoint(ROSE_OUTER + 20, i * 45);
    const text = roseSvg("text", { x, y, class: i % 2 === 0 ? "rose-cardinal" : "" });
    text.textContent = name;
    labels.appendChild(text);
  });

  // Ring labels, small, along the quietest direction so they crowd no
  // petal (ties go to the first, from N clockwise).
  const quietest = sectorTotals.indexOf(Math.min(...sectorTotals));
  const labelBearing = quietest * data.sector_degrees;
  const plotted = total > data.calm; // anything outside the calm circle
  for (let ring = ringStep; plotted && ring <= ringMax + 1e-9; ring += ringStep) {
    const [x, y] = rosePoint(radiusFor(ring), labelBearing);
    const text = roseSvg("text", { x, y, class: "rose-ring-label", "aria-hidden": "true" });
    text.textContent = formatRoseRing(ring);
    labels.appendChild(text);
  }

  const petals = roseSvg("g", { class: "rose-petals" });
  svg.appendChild(petals);
  const segmentsBySector = data.counts.map((row, sectorIndex) => {
    const bearing = sectorIndex * data.sector_degrees;
    const group = roseSvg("g", { class: "rose-sector" });
    petals.appendChild(group);
    let cumulative = 0;
    const segments = [];
    row.forEach((count, classIndex) => {
      if (!count) return;
      const from = cumulative;
      cumulative += share(count);
      const inner = radiusFor(from) + ROSE_GAP / 2 + (from === 0 ? ROSE_GAP / 2 : 0);
      const outer = radiusFor(cumulative) - ROSE_GAP / 2;
      segments.push({ classIndex, inner, outer, count });
      // A sliver too thin to see is still in the tooltip and the table.
      if (outer - inner < 0.6) return;
      group.appendChild(
        roseSvg("path", {
          d: roseWedgePath(inner, outer, bearing, halfWidth),
          class: `rose-segment rose-c${roseClassStep(classIndex, classes.length)}`,
        })
      );
    });
    return { group, segments, outer: radiusFor(cumulative) };
  });

  // Calm circle, with its share in the middle.
  const calm = roseSvg("g", { class: "rose-calm" });
  svg.appendChild(calm);
  calm.appendChild(roseSvg("circle", { cx: ROSE_CENTER, cy: ROSE_CENTER, r: ROSE_CALM - 1 }));
  const calmValue = roseSvg("text", { x: ROSE_CENTER, y: ROSE_CENTER - 5, class: "rose-calm-value" });
  calmValue.textContent = total ? formatRoseShare(share(data.calm)) : "–";
  calm.appendChild(calmValue);
  const calmLabel = roseSvg("text", { x: ROSE_CENTER, y: ROSE_CENTER + 9, class: "rose-calm-label" });
  calmLabel.textContent = "calm";
  calm.appendChild(calmLabel);

  // Hit areas: one whole wedge per direction, bigger than any segment in
  // it, on top of everything. Hover or focus shows that direction's
  // breakdown; the segment under the pointer is picked out in the list.
  const tip = document.createElement("div");
  tip.className = "windrose-tip";
  tip.hidden = true;
  tip.setAttribute("role", "status");
  plot.appendChild(tip);

  const hits = roseSvg("g", { class: "rose-hits" });
  svg.appendChild(hits);

  function showTip(sectorIndex, pointer) {
    const { group, segments, outer } = segmentsBySector[sectorIndex];
    petals.classList.add("has-active");
    segmentsBySector.forEach((s) => s.group.classList.toggle("is-active", s.group === group));

    let activeClass = null;
    if (pointer) {
      const hit = segments.find((s) => pointer.radius >= s.inner - 1.5 && pointer.radius <= s.outer + 1.5);
      if (hit) activeClass = hit.classIndex;
    }

    tip.textContent = "";
    const title = document.createElement("div");
    title.className = "windrose-tip-title";
    title.textContent = `From the ${data.sectors[sectorIndex]} `;
    const range = document.createElement("span");
    range.textContent = roseBearingRange(sectorIndex, data);
    title.appendChild(range);
    tip.appendChild(title);

    const totalLine = document.createElement("div");
    totalLine.className = "windrose-tip-total";
    const sectorCount = sectorTotals[sectorIndex];
    totalLine.textContent =
      `${formatRoseShare(share(sectorCount))} of readings · ${sectorCount.toLocaleString()}`;
    tip.appendChild(totalLine);

    if (segments.length) {
      const list = document.createElement("ul");
      [...segments].reverse().forEach((s) => {
        const item = document.createElement("li");
        if (s.classIndex === activeClass) item.className = "is-active";
        const key = document.createElement("span");
        key.className = `windrose-key rose-c${roseClassStep(s.classIndex, classes.length)}`;
        const value = document.createElement("strong");
        value.textContent = formatRoseShare(share(s.count));
        const label = document.createElement("span");
        label.textContent = classes[s.classIndex].label;
        item.append(key, value, label);
        list.appendChild(item);
      });
      tip.appendChild(list);
    }

    tip.hidden = false;
    // Beside the pointer (or the petal tip on keyboard focus), kept inside
    // the plot.
    const plotBox = plot.getBoundingClientRect();
    const svgBox = svg.getBoundingClientRect();
    const scale = svgBox.width / ROSE_SIZE;
    let x;
    let y;
    if (pointer) {
      x = pointer.clientX - plotBox.left;
      y = pointer.clientY - plotBox.top;
    } else {
      const [px, py] = rosePoint(Math.max(outer, ROSE_CALM + 20), sectorIndex * data.sector_degrees);
      x = svgBox.left - plotBox.left + px * scale;
      y = svgBox.top - plotBox.top + py * scale;
    }
    const tipBox = tip.getBoundingClientRect();
    let left = x + 14;
    if (left + tipBox.width > plotBox.width) left = x - 14 - tipBox.width;
    let top = y + 14;
    if (top + tipBox.height > plotBox.height) top = y - 14 - tipBox.height;
    tip.style.left = `${Math.max(0, left)}px`;
    tip.style.top = `${Math.max(0, top)}px`;
  }

  function hideTip() {
    tip.hidden = true;
    petals.classList.remove("has-active");
    segmentsBySector.forEach((s) => s.group.classList.remove("is-active"));
  }

  function pointerInfo(event) {
    const box = svg.getBoundingClientRect();
    const scale = ROSE_SIZE / box.width;
    const dx = (event.clientX - box.left) * scale - ROSE_CENTER;
    const dy = (event.clientY - box.top) * scale - ROSE_CENTER;
    return { clientX: event.clientX, clientY: event.clientY, radius: Math.hypot(dx, dy) };
  }

  if (plotted) {
    data.sectors.forEach((name, sectorIndex) => {
      const hit = roseSvg("path", {
        d: roseWedgePath(ROSE_CALM, ROSE_OUTER + 8, sectorIndex * data.sector_degrees, halfWidth + 0.5),
        class: "rose-hit",
        tabindex: "0",
        role: "img",
        "aria-label": `From the ${name}: ${formatRoseShare(share(sectorTotals[sectorIndex]))} of readings`,
      });
      // A tap shows the tip too, and it stays until the next tap elsewhere.
      hit.addEventListener("pointermove", (event) => showTip(sectorIndex, pointerInfo(event)));
      hit.addEventListener("pointerdown", (event) => showTip(sectorIndex, pointerInfo(event)));
      hit.addEventListener("pointerleave", (event) => {
        if (event.pointerType === "mouse") hideTip();
      });
      hit.addEventListener("focus", () => showTip(sectorIndex, null));
      hit.addEventListener("blur", hideTip);
      hits.appendChild(hit);
    });
    svg.addEventListener("pointerdown", (event) => {
      if (!event.target.classList.contains("rose-hit")) hideTip();
    });
  }

  // --- side panel: summary, legend, table ---------------------------------
  const side = document.createElement("div");
  side.className = "windrose-side";
  body.appendChild(side);

  const summary = document.createElement("p");
  summary.className = "windrose-summary";
  side.appendChild(summary);
  const unit = data.unit;
  const calmText = `calm (under ${data.calm_below} ${unit})`;
  if (!total) {
    summary.textContent = "No wind readings in this range.";
  } else if (total === data.calm) {
    summary.textContent = `The air was ${calmText} for every one of the ${total.toLocaleString()} readings.`;
  } else {
    // Name the busiest direction, and the runner-up when it's close.
    const ranked = sectorTotals
      .map((count, i) => ({ count, i }))
      .sort((a, b) => b.count - a.count);
    const [first, second] = ranked;
    const names =
      second && second.count >= first.count * 0.85
        ? `${data.sectors[first.i]} and ${data.sectors[second.i]}`
        : data.sectors[first.i];
    summary.textContent =
      `Mostly from the ${names}. ${formatRoseShare(share(data.calm))} of ` +
      `${total.toLocaleString()} readings were ${calmText}.`;
  }

  const legend = document.createElement("div");
  legend.className = "windrose-legend";
  const legendTitle = document.createElement("span");
  legendTitle.className = "windrose-legend-title";
  legendTitle.textContent = "Average wind speed";
  legend.appendChild(legendTitle);
  const legendList = document.createElement("ul");
  classes.forEach((c, i) => {
    const item = document.createElement("li");
    const key = document.createElement("span");
    key.className = `windrose-swatch rose-c${roseClassStep(i, classes.length)}`;
    const label = document.createElement("span");
    label.textContent = c.label;
    item.append(key, label);
    legendList.appendChild(item);
  });
  legend.appendChild(legendList);
  const legendNote = document.createElement("p");
  legendNote.className = "windrose-note";
  legendNote.textContent =
    "Petal length is the share of readings from that direction" +
    (plotted ? `; rings every ${formatRoseRing(ringStep)}. ` : ". ") +
    `The centre is the share that was ${calmText}.`;
  legend.appendChild(legendNote);
  side.appendChild(legend);

  // The table starts open where it fits beside the rose, closed on narrow
  // screens (a host can preset $roseTableOpen); after that it stays however
  // the reader left it.
  const table = buildWindRoseTable(data, sectorTotals, share);
  if (host.$roseTableOpen == null) host.$roseTableOpen = host.clientWidth >= 1000;
  table.open = host.$roseTableOpen && total > 0;
  table.addEventListener("toggle", () => {
    host.$roseTableOpen = table.open;
  });
  side.appendChild(table);
}

let roseTableCount = 0;

function buildWindRoseTable(data, sectorTotals, share) {
  const details = document.createElement("details");
  details.className = "windrose-table";
  const summary = document.createElement("summary");
  summary.textContent = "Show as a table";
  details.appendChild(summary);

  // Described outside the table: a caption would scroll away with it.
  const caption = document.createElement("p");
  caption.className = "windrose-table-caption";
  caption.id = `windrose-caption-${++roseTableCount}`;
  caption.textContent = `Share of ${data.total.toLocaleString()} readings, by direction and average speed`;
  details.appendChild(caption);

  const scroll = document.createElement("div");
  scroll.className = "windrose-table-scroll";
  details.appendChild(scroll);

  const table = document.createElement("table");
  table.setAttribute("aria-describedby", caption.id);
  scroll.appendChild(table);

  const cell = (tag, text, className) => {
    const el = document.createElement(tag);
    el.textContent = text;
    if (className) el.className = className;
    if (tag === "th") el.scope = className === "row-head" ? "row" : "col";
    return el;
  };

  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  headRow.append(cell("th", "From"));
  data.speed_classes.forEach((c) => headRow.append(cell("th", c.label)));
  headRow.append(cell("th", "All speeds"), cell("th", "Readings"));
  head.appendChild(headRow);
  table.appendChild(head);

  const bodyEl = document.createElement("tbody");
  data.counts.forEach((row, i) => {
    const tr = document.createElement("tr");
    tr.append(cell("th", data.sectors[i], "row-head"));
    row.forEach((count) => tr.append(cell("td", count ? formatRoseShare(share(count)) : "–")));
    tr.append(
      cell("td", formatRoseShare(share(sectorTotals[i])), "total"),
      cell("td", sectorTotals[i].toLocaleString())
    );
    bodyEl.appendChild(tr);
  });
  const calmRow = document.createElement("tr");
  calmRow.append(cell("th", `Calm (under ${data.calm_below} ${data.unit})`, "row-head"));
  data.speed_classes.forEach(() => calmRow.append(cell("td", "")));
  calmRow.append(
    cell("td", formatRoseShare(share(data.calm)), "total"),
    cell("td", data.calm.toLocaleString())
  );
  bodyEl.appendChild(calmRow);
  table.appendChild(bodyEl);

  const foot = document.createElement("tfoot");
  const footRow = document.createElement("tr");
  footRow.append(cell("th", "All directions", "row-head"));
  data.speed_classes.forEach((_, classIndex) => {
    const count = data.counts.reduce((sum, row) => sum + row[classIndex], 0);
    footRow.append(cell("td", formatRoseShare(share(count))));
  });
  footRow.append(cell("td", data.total ? "100%" : "–", "total"), cell("td", data.total.toLocaleString()));
  foot.appendChild(footRow);
  table.appendChild(foot);

  return details;
}

// Fetches and draws the rose for [start, end] into `host`. The previous
// rose stays (dimmed) while the next one loads, so the card never jumps.
// `onLoaded(data)` gets the response, e.g. to update a heading.
async function loadWindRose(host, start, end, onLoaded) {
  if (!host) return;
  const token = {};
  host.$roseRequest = token;
  host.classList.add("is-loading");
  try {
    const response = await fetch(`/site/wind-rose?start=${start}&end=${end}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    if (host.$roseRequest !== token) return; // a newer range was picked meanwhile
    renderWindRose(host, data);
    if (onLoaded) onLoaded(data);
  } catch (err) {
    if (host.$roseRequest !== token) return;
    host.textContent = "";
    const message = document.createElement("p");
    message.className = "windrose-summary";
    message.textContent = `Unable to load the wind rose (${err.message || "network error"}).`;
    host.appendChild(message);
  } finally {
    if (host.$roseRequest === token) host.classList.remove("is-loading");
  }
}

// Self-loading roses (the current page's last 24 hours).
document.querySelectorAll("[data-wind-rose='auto']").forEach((host) => {
  host.$roseTableOpen = false; // a glance page: table on request only
  const refresh = () => {
    const end = Math.floor(Date.now() / 1000);
    loadWindRose(host, end - 86400, end);
  };
  refresh();
  setInterval(refresh, ROSE_AUTO_REFRESH_MS);
});
