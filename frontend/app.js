/* EvolveStudio frontend — tab routing, fetch calls, Compose/Run/Results. */
(function () {
  "use strict";

  const state = {
    screen: "compose",
    experiments: [],
    selectedSlug: null,
    expDetail: null, // {title, statement, path, files}
    runId: null,
    statusTimer: null,
    resultsTimer: null,
    selectedRunId: null,
  };

  // ---------- fetch helpers ----------

  async function api(path, opts) {
    const res = await fetch(path, opts);
    if (!res.ok) {
      let detail = res.statusText;
      try {
        detail = (await res.json()).detail || detail;
      } catch (e) {}
      throw new Error(`${res.status}: ${detail}`);
    }
    return res.json();
  }

  const getJSON = (p) => api(p);
  const postJSON = (p, body) =>
    api(p, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

  function slugify(s) {
    return (
      (s || "")
        .toLowerCase()
        .trim()
        .replace(/[^a-z0-9]+/g, "_")
        .replace(/^_+|_+$/g, "") || "experiment"
    );
  }

  function fmtElapsed(sec) {
    if (sec == null) return "—";
    if (sec < 60) return `${Math.round(sec)}s`;
    const m = Math.floor(sec / 60);
    const s = Math.round(sec % 60);
    return `${m}m ${s}s`;
  }

  // ---------- tab routing ----------

  function showScreen(name) {
    state.screen = name;
    document.querySelectorAll(".tab").forEach((t) =>
      t.classList.toggle("active", t.dataset.screen === name)
    );
    document.querySelectorAll(".screen").forEach((s) =>
      s.classList.toggle("active", s.id === `screen-${name}`)
    );
    if (name === "run") renderRun();
    if (name === "results") renderResults();
  }

  document.querySelectorAll(".tab").forEach((t) =>
    t.addEventListener("click", () => showScreen(t.dataset.screen))
  );

  // ---------- experiments (rail) ----------

  async function loadExperiments() {
    state.experiments = await getJSON("/api/experiments");
    const sel = document.getElementById("rail-exp-select");
    sel.innerHTML = "";
    if (!state.experiments.length) {
      const opt = document.createElement("option");
      opt.textContent = "(none yet)";
      opt.value = "";
      sel.appendChild(opt);
      state.selectedSlug = null;
    } else {
      state.experiments.forEach((e) => {
        const opt = document.createElement("option");
        opt.value = e.slug;
        opt.textContent = e.slug;
        sel.appendChild(opt);
      });
      if (!state.selectedSlug || !state.experiments.find((e) => e.slug === state.selectedSlug)) {
        state.selectedSlug = state.experiments[0].slug;
      }
      sel.value = state.selectedSlug;
    }
    updateRailMeta();
  }

  function updateRailMeta() {
    const meta = document.getElementById("rail-exp-meta");
    const e = state.experiments.find((x) => x.slug === state.selectedSlug);
    meta.textContent = e ? e.title : "";
  }

  document.getElementById("rail-exp-select").addEventListener("change", (ev) => {
    state.selectedSlug = ev.target.value || null;
    state.expDetail = null;
    updateRailMeta();
    if (state.screen === "run") renderRun();
    if (state.screen === "results") renderResults();
  });

  // ---------- Compose ----------

  const composeEls = {
    title: document.getElementById("c-title"),
    statement: document.getElementById("c-statement"),
    slug: document.getElementById("c-slug"),
    initial: document.getElementById("c-initial"),
    evaluator: document.getElementById("c-evaluator"),
    config: document.getElementById("c-config"),
    save: document.getElementById("c-save"),
  };

  composeEls.title.addEventListener("input", () => {
    composeEls.slug.textContent = `generated_experiments/${slugify(composeEls.title.value)}/`;
  });

  // demo chips
  document.querySelectorAll(".chip").forEach((chip) =>
    chip.addEventListener("click", async () => {
      try {
        const d = await getJSON(`/api/demos/${chip.dataset.demo}`);
        composeEls.title.value = d.title;
        composeEls.statement.value = d.statement;
        composeEls.initial.value = d.initial_program;
        composeEls.evaluator.value = d.evaluator;
        composeEls.config.value = d.config;
        composeEls.slug.textContent = `generated_experiments/${slugify(d.title)}/`;
      } catch (e) {
        alert("Failed to load demo: " + e.message);
      }
    })
  );

  // file sub-tabs
  document.querySelectorAll(".filetab").forEach((tab) =>
    tab.addEventListener("click", () => {
      document.querySelectorAll(".filetab").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      const which = tab.dataset.file;
      composeEls.initial.classList.toggle("hidden", which !== "initial");
      composeEls.evaluator.classList.toggle("hidden", which !== "evaluator");
      composeEls.config.classList.toggle("hidden", which !== "config");
    })
  );

  composeEls.save.addEventListener("click", async () => {
    const title = composeEls.title.value.trim();
    if (!title) return alert("Title is required.");
    if (!composeEls.initial.value || !composeEls.evaluator.value || !composeEls.config.value)
      return alert("All three files (initial_program, evaluator, config) are required.");
    composeEls.save.disabled = true;
    composeEls.save.textContent = "Saving…";
    try {
      const r = await postJSON("/api/experiments", {
        title,
        statement: composeEls.statement.value,
        initial_program: composeEls.initial.value,
        evaluator: composeEls.evaluator.value,
        config: composeEls.config.value,
      });
      state.selectedSlug = r.slug;
      await loadExperiments();
      document.getElementById("rail-exp-select").value = r.slug;
      showScreen("run");
    } catch (e) {
      alert("Save failed: " + e.message);
    } finally {
      composeEls.save.disabled = false;
      composeEls.save.textContent = "Save experiment";
    }
  });

  // ---------- Run ----------

  async function renderRun() {
    const noexp = document.getElementById("run-noexp");
    const body = document.getElementById("run-body");
    if (!state.selectedSlug) {
      noexp.style.display = "block";
      body.style.display = "none";
      return;
    }
    noexp.style.display = "none";
    body.style.display = "block";

    if (!state.expDetail || state.expDetail.slug !== state.selectedSlug) {
      try {
        state.expDetail = await getJSON(`/api/experiments/${state.selectedSlug}`);
      } catch (e) {
        state.expDetail = null;
      }
    }
    document.getElementById("r-slug").textContent = state.selectedSlug;
    document.getElementById("r-path").textContent = state.expDetail ? state.expDetail.path : "";
    if (!document.getElementById("r-python").value)
      document.getElementById("r-python").value = "/opt/anaconda3/envs/OpenEvolve/bin/python";
    updateCmdPreview();
  }

  function updateCmdPreview() {
    if (!state.expDetail) return;
    const expPath = state.expDetail.path;
    const projRoot = expPath.replace(/\/generated_experiments\/[^/]+$/, "");
    const py = document.getElementById("r-python").value || "python";
    const iters = document.getElementById("r-iterations").value || "10";
    const out = document.getElementById("r-output").value.trim() || `${expPath}/run_<timestamp>`;
    const cmd = [
      py,
      `${projRoot}/third_party/openevolve/openevolve-run.py`,
      `${expPath}/initial_program.py`,
      `${expPath}/evaluator.py`,
      "--config",
      `${expPath}/config.yaml`,
      "--output",
      out,
      "--iterations",
      iters,
    ].join(" ");
    document.getElementById("r-cmd").textContent = cmd;
  }

  ["r-iterations", "r-python", "r-output"].forEach((id) =>
    document.getElementById(id).addEventListener("input", updateCmdPreview)
  );

  document.getElementById("r-exec").addEventListener("click", async () => {
    const btn = document.getElementById("r-exec");
    btn.disabled = true;
    try {
      const r = await postJSON("/api/runs", {
        slug: state.selectedSlug,
        iterations: parseInt(document.getElementById("r-iterations").value, 10) || 10,
        python: document.getElementById("r-python").value,
        output_dir: document.getElementById("r-output").value.trim() || null,
      });
      state.runId = r.run_id;
      state.selectedRunId = r.run_id;
      const banner = document.getElementById("r-banner");
      banner.className = "banner success";
      banner.innerHTML = `Launched · <span class="mono">PID ${r.pid}</span> · run <span class="mono">${r.run_id}</span>`;
      document.getElementById("r-logwrap").style.display = "block";
      document.getElementById("r-stop").disabled = false;
      startStatusPolling();
    } catch (e) {
      alert("Launch failed: " + e.message);
      btn.disabled = false;
    }
  });

  document.getElementById("r-stop").addEventListener("click", async () => {
    if (!state.runId) return;
    document.getElementById("r-stop").disabled = true;
    try {
      await postJSON(`/api/runs/${state.runId}/stop`, {});
    } catch (e) {
      alert("Stop failed: " + e.message);
    }
  });

  // ---------- status polling (drives rail + run log tail) ----------

  function setRailStatus(stateName, text) {
    const dot = document.getElementById("rail-status-dot");
    dot.className = "status-dot" + (stateName ? " " + stateName : "");
    document.getElementById("rail-status-text").textContent = text;
  }

  function startStatusPolling() {
    if (state.statusTimer) clearInterval(state.statusTimer);
    const poll = async () => {
      if (!state.runId) return;
      let s;
      try {
        s = await getJSON(`/api/runs/${state.runId}/status`);
      } catch (e) {
        return;
      }
      // rail
      if (s.state === "running") {
        setRailStatus("running", `Running · iter ${s.iters_done}`);
      } else if (s.state === "done") {
        setRailStatus("done", `Done · iter ${s.iters_done}`);
      } else if (s.state === "error") {
        setRailStatus("error", "Error");
      } else {
        setRailStatus("", "Idle");
      }
      // run log tail
      const logtail = document.getElementById("r-logtail");
      if (logtail) logtail.textContent = s.log_tail || "(no output yet)";
      // stop polling + finalize when finished
      if (s.state !== "running") {
        document.getElementById("r-exec").disabled = false;
        document.getElementById("r-stop").disabled = true;
        clearInterval(state.statusTimer);
        state.statusTimer = null;
      }
    };
    poll();
    state.statusTimer = setInterval(poll, 2000);
  }

  // ---------- Results ----------

  async function renderResults() {
    const noexp = document.getElementById("res-noexp");
    const body = document.getElementById("res-body");
    if (!state.selectedSlug) {
      noexp.textContent = "Select an experiment first.";
      noexp.style.display = "block";
      body.style.display = "none";
      return;
    }
    let runs = [];
    try {
      runs = await getJSON(`/api/experiments/${state.selectedSlug}/runs`);
    } catch (e) {}
    if (!runs.length) {
      noexp.textContent = "No runs yet for this experiment. Launch one from the Run tab.";
      noexp.style.display = "block";
      body.style.display = "none";
      return;
    }
    noexp.style.display = "none";
    body.style.display = "block";

    const sel = document.getElementById("res-run-select");
    sel.innerHTML = "";
    runs.forEach((r) => {
      const opt = document.createElement("option");
      opt.value = r.run_id;
      opt.textContent = r.run_id;
      sel.appendChild(opt);
    });
    // default to current/selected run if present
    const want = state.selectedRunId && runs.find((r) => r.run_id === state.selectedRunId)
      ? state.selectedRunId
      : runs[0].run_id;
    sel.value = want;
    state.selectedRunId = want;

    sel.onchange = () => {
      state.selectedRunId = sel.value;
      if (window.Lineage) window.Lineage.resetSelection();
      loadResultsData();
    };

    if (window.Lineage) window.Lineage.resetSelection();
    loadResultsData();
  }

  async function loadResultsData() {
    const runId = state.selectedRunId;
    if (!runId) return;

    // metrics from status + lineage
    let status = {},
      lineage = { nodes: [], best_id: null };
    try {
      status = await getJSON(`/api/runs/${runId}/status`);
    } catch (e) {}
    try {
      lineage = await getJSON(`/api/runs/${runId}/lineage`);
    } catch (e) {}

    renderMetrics(status, lineage);

    if (window.Lineage) {
      window.Lineage.render(runId, lineage, onNodeClick);
      // auto-select + open the best node once
      if (lineage.best_id && !window.__resultsSelected) {
        onNodeClick(lineage.best_id);
        window.Lineage.select(lineage.best_id);
        window.__resultsSelected = true;
      }
    }

    // live refresh while running
    if (state.resultsTimer) {
      clearInterval(state.resultsTimer);
      state.resultsTimer = null;
    }
    if (status.state === "running") {
      state.resultsTimer = setInterval(() => {
        if (state.screen === "results") refreshResultsLive();
        else {
          clearInterval(state.resultsTimer);
          state.resultsTimer = null;
        }
      }, 3000);
    }
  }

  async function refreshResultsLive() {
    const runId = state.selectedRunId;
    if (!runId) return;
    let status = {},
      lineage = { nodes: [], best_id: null };
    try {
      status = await getJSON(`/api/runs/${runId}/status`);
    } catch (e) {}
    try {
      lineage = await getJSON(`/api/runs/${runId}/lineage`);
    } catch (e) {}
    renderMetrics(status, lineage);
    if (window.Lineage) window.Lineage.render(runId, lineage, onNodeClick);
    if (status.state !== "running" && state.resultsTimer) {
      clearInterval(state.resultsTimer);
      state.resultsTimer = null;
    }
  }

  function renderMetrics(status, lineage) {
    const nodes = (lineage && lineage.nodes) || [];
    const realNodes = nodes.filter((n) => n.id !== "__root__");
    const bestNode = lineage.best_id
      ? realNodes.find((n) => n.id === lineage.best_id)
      : null;
    const bestScore =
      bestNode && bestNode.score != null
        ? bestNode.score
        : status.best_score != null
        ? status.best_score
        : null;
    const scoreCol = window.Lineage ? window.Lineage.scoreColor(bestScore) : "#fff";
    const cards = [
      {
        label: "Best score",
        value: bestScore == null ? "—" : bestScore.toFixed(4),
        color: scoreCol,
        mono: true,
      },
      { label: "Candidates", value: realNodes.length },
      { label: "Iterations", value: status.iters_done != null ? status.iters_done : "—" },
      { label: "Wall time", value: fmtElapsed(status.elapsed), mono: true },
    ];
    document.getElementById("res-metrics").innerHTML = cards
      .map(
        (c) => `
      <div class="metric">
        <div class="m-label">${c.label}</div>
        <div class="m-value ${c.mono ? "mono" : ""}" ${c.color ? `style="color:${c.color}"` : ""}>${c.value}</div>
      </div>`
      )
      .join("");
  }

  async function onNodeClick(nodeId) {
    const runId = state.selectedRunId;
    if (!runId) return;
    try {
      const node = await getJSON(`/api/runs/${runId}/node/${nodeId}`);
      if (window.Lineage) window.Lineage.renderInspector(node);
    } catch (e) {
      // node may not be inspectable (e.g. synthetic root) — ignore quietly
    }
  }

  // ---------- init ----------

  (async function init() {
    try {
      await loadExperiments();
    } catch (e) {
      console.error("Failed to load experiments", e);
    }
    setRailStatus("", "Idle");
    showScreen("compose");
  })();
})();
