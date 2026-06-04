/* D3 lineage graph + inspector. Exposes window.Lineage. */
(function () {
  "use strict";

  const SCORE_SCALE = d3
    .scaleLinear()
    .domain([0, 0.5, 1])
    .range(["#F26D6D", "#F2B84B", "#46D19E"])
    .clamp(true);

  const NODE_W = 124;
  const NODE_H = 46;
  const SYNTH = "__root__";

  let currentRunId = null;
  let selectedNodeId = null;

  function scoreColor(s) {
    return s == null ? "#3A4150" : SCORE_SCALE(s);
  }

  function shortId(id) {
    return id && id.length > 8 ? id.slice(0, 8) : id || "?";
  }

  // Build the set of node ids on the root -> best path.
  function bestPathIds(nodes, bestId) {
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const path = new Set();
    let cur = bestId;
    const guard = new Set();
    while (cur && byId.has(cur) && !guard.has(cur)) {
      guard.add(cur);
      path.add(cur);
      cur = byId.get(cur).parent;
    }
    return path;
  }

  function render(runId, data, onSelect) {
    currentRunId = runId;
    const svgEl = document.getElementById("lineage-svg");
    const emptyEl = document.getElementById("graph-empty");
    const svg = d3.select(svgEl);
    svg.selectAll("*").remove();

    const nodes = (data && data.nodes) || [];
    if (!nodes.length) {
      emptyEl.style.display = "flex";
      return;
    }
    emptyEl.style.display = "none";

    // Stratify (parent==null is the root). Defensive: bail to empty on failure.
    let root;
    try {
      root = d3
        .stratify()
        .id((d) => d.id)
        .parentId((d) => d.parent)(nodes);
    } catch (e) {
      emptyEl.textContent = "Lineage has an inconsistent structure; cannot render the tree.";
      emptyEl.style.display = "flex";
      return;
    }

    const rect = svgEl.getBoundingClientRect();
    const W = rect.width || 700;
    const H = rect.height || 540;

    const layout = d3.tree().nodeSize([NODE_W + 26, NODE_H + 46]);
    layout(root);

    const path = bestPathIds(nodes, data.best_id);

    const g = svg.append("g");

    // zoom / pan
    const zoom = d3
      .zoom()
      .scaleExtent([0.3, 2.5])
      .on("zoom", (ev) => g.attr("transform", ev.transform));
    svg.call(zoom);

    // Edges
    g.append("g")
      .selectAll("path.edge")
      .data(root.links())
      .join("path")
      .attr("class", "edge")
      .attr("stroke", (d) =>
        path.has(d.source.id) && path.has(d.target.id) ? "#46D19E" : "#2A303C"
      )
      .attr("stroke-width", (d) =>
        path.has(d.source.id) && path.has(d.target.id) ? 2 : 1
      )
      .attr(
        "d",
        d3
          .linkVertical()
          .x((d) => d.x)
          .y((d) => d.y)
      );

    // Nodes
    const node = g
      .append("g")
      .selectAll("g.node")
      .data(root.descendants())
      .join("g")
      .attr("class", "node")
      .attr("transform", (d) => `translate(${d.x},${d.y})`)
      .style("display", (d) => (d.id === SYNTH ? "none" : null));

    node
      .append("rect")
      .attr("class", "node-rect")
      .attr("x", -NODE_W / 2)
      .attr("y", -NODE_H / 2)
      .attr("width", NODE_W)
      .attr("height", NODE_H)
      .attr("rx", 9)
      .attr("stroke", (d) => scoreColor(d.data.score))
      .attr("data-id", (d) => d.id)
      .on("click", (ev, d) => {
        if (d.id === SYNTH) return;
        select(d.id);
        if (onSelect) onSelect(d.id);
      })
      .append("title")
      .text((d) => (d.data.score == null ? "no score" : `score ${d.data.score.toFixed(4)}`));

    node
      .append("text")
      .attr("class", "node-label")
      .attr("text-anchor", "middle")
      .attr("dy", -2)
      .text((d) => shortId(d.id));

    node
      .append("text")
      .attr("class", "node-score")
      .attr("text-anchor", "middle")
      .attr("dy", 12)
      .attr("fill", (d) => scoreColor(d.data.score))
      .text((d) => {
        const s = d.data.score;
        const gen = d.data.iter;
        const sStr = s == null ? "—" : s.toFixed(3);
        return gen == null ? `score ${sStr}` : `score ${sStr} · gen ${gen}`;
      });

    // Fit-to-view: center the tree.
    const xs = root.descendants().map((d) => d.x);
    const ys = root.descendants().map((d) => d.y);
    const minX = Math.min(...xs),
      maxX = Math.max(...xs);
    const minY = Math.min(...ys),
      maxY = Math.max(...ys);
    const tw = maxX - minX + NODE_W + 40;
    const th = maxY - minY + NODE_H + 40;
    const scale = Math.min(W / tw, H / th, 1.2);
    const tx = W / 2 - ((minX + maxX) / 2) * scale;
    const ty = H / 2 - ((minY + maxY) / 2) * scale + 20;
    svg.call(zoom.transform, d3.zoomIdentity.translate(tx, ty).scale(scale));

    // Re-apply selection ring if a node was selected before a refresh.
    if (selectedNodeId) markSelected(selectedNodeId);
  }

  function markSelected(nodeId) {
    d3.selectAll(".node-rect").classed("selected", false);
    d3.selectAll(`.node-rect[data-id='${cssEscape(nodeId)}']`).classed("selected", true);
  }

  function cssEscape(s) {
    return (s || "").replace(/'/g, "\\'");
  }

  function select(nodeId) {
    selectedNodeId = nodeId;
    markSelected(nodeId);
  }

  // ---- Inspector ----

  function renderDiff(diffText) {
    if (!diffText) return '<span class="diff-line-ctx">(no diff — this is a root program)</span>';
    return diffText
      .split("\n")
      .map((line) => {
        const esc = line
          .replace(/&/g, "&amp;")
          .replace(/</g, "&lt;")
          .replace(/>/g, "&gt;");
        if (line.startsWith("+++") || line.startsWith("---") || line.startsWith("@@"))
          return `<span class="diff-line-hdr">${esc}</span>`;
        if (line.startsWith("+")) return `<span class="diff-line-add">${esc}</span>`;
        if (line.startsWith("-")) return `<span class="diff-line-del">${esc}</span>`;
        return `<span class="diff-line-ctx">${esc}</span>`;
      })
      .join("");
  }

  function escapeHtml(s) {
    return (s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function renderInspector(node) {
    const el = document.getElementById("inspector");
    if (!node) {
      el.innerHTML = '<div class="insp-empty">Node not found.</div>';
      return;
    }
    const scoreStr = node.score == null ? "—" : node.score.toFixed(4);
    const scoreCol = scoreColor(node.score);
    el.innerHTML = `
      <div class="insp-head">
        <span class="insp-id">${shortId(node.id)}</span>
        <span class="pill">iter ${node.iter == null ? "root" : node.iter}</span>
      </div>
      <div class="insp-stats">
        <div class="insp-stat"><div class="s-label">score</div><div class="s-value" style="color:${scoreCol}">${scoreStr}</div></div>
        <div class="insp-stat"><div class="s-label">iter</div><div class="s-value">${node.iter == null ? "—" : node.iter}</div></div>
        <div class="insp-stat"><div class="s-label">parent</div><div class="s-value">${node.parent ? shortId(node.parent) : "—"}</div></div>
      </div>
      ${node.changes ? `<div class="insp-changes">${escapeHtml(node.changes)}</div>` : ""}
      <div class="code-tabs">
        <button class="code-tab active" data-codetab="diff">Diff vs parent</button>
        <button class="code-tab" data-codetab="code">Code</button>
      </div>
      <pre class="code-pane" id="insp-diff">${renderDiff(node.diff)}</pre>
      <pre class="code-pane hidden" id="insp-code">${escapeHtml(node.code)}</pre>
    `;
    el.querySelectorAll(".code-tab").forEach((tab) => {
      tab.addEventListener("click", () => {
        el.querySelectorAll(".code-tab").forEach((t) => t.classList.remove("active"));
        tab.classList.add("active");
        const which = tab.dataset.codetab;
        el.querySelector("#insp-diff").classList.toggle("hidden", which !== "diff");
        el.querySelector("#insp-code").classList.toggle("hidden", which !== "code");
      });
    });
  }

  function resetSelection() {
    selectedNodeId = null;
  }

  window.Lineage = { render, select, renderInspector, resetSelection, shortId, scoreColor };
})();
