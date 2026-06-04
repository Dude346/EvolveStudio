/* EvolveStudio frontend — chat-like generation, run, results. */
(function () {
  "use strict";

  const state = {
    screen: "compose",
    experiments: [],
    activeSlug: null, // experiment to run
    expDetail: null,
    genStatement: "", // the problem text that produced the current harness
    runId: null,
    statusTimer: null,
    resultsTimer: null,
    resultsSlug: null,
    resultsRunId: null,
    models: [],
    defaultModel: null,
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

  // Like postJSON, but retries once on a transient network error ("Load
  // failed" / "Failed to fetch"). Safe only for idempotent calls (save).
  async function postJSONRetry(p, body) {
    try {
      return await postJSON(p, body);
    } catch (e) {
      const msg = (e && e.message) || "";
      if (/load failed|failed to fetch|networkerror/i.test(msg)) {
        await new Promise((r) => setTimeout(r, 250));
        return await postJSON(p, body);
      }
      throw e;
    }
  }

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
    return `${Math.floor(sec / 60)}m ${Math.round(sec % 60)}s`;
  }

  // ---------- top-bar status ----------

  function setTopStatus(stateName, text) {
    document.getElementById("top-status-dot").className =
      "status-dot" + (stateName ? " " + stateName : "");
    document.getElementById("top-status-text").textContent = text;
  }

  // ---------- routing ----------

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

  // ---------- experiments ----------

  async function loadExperiments() {
    state.experiments = await getJSON("/api/experiments");
    if (!state.activeSlug && state.experiments.length) {
      state.activeSlug = state.experiments[0].slug;
    }
    fillExpSelect("run-exp-select", state.activeSlug);
  }

  function fillExpSelect(id, selected) {
    const sel = document.getElementById(id);
    if (!sel) return;
    sel.innerHTML = "";
    state.experiments.forEach((e) => {
      const o = document.createElement("option");
      o.value = e.slug;
      o.textContent = e.slug;
      sel.appendChild(o);
    });
    if (selected) sel.value = selected;
  }

  // ---------- model picker (top-bar dropdown) ----------

  async function loadModels() {
    let r;
    try {
      r = await getJSON("/api/models");
    } catch (e) {
      r = { models: [], default: null };
    }
    state.models = r.models || [];
    state.defaultModel = r.default;
    if (!state.models.length && state.defaultModel) state.models = [state.defaultModel];
    if (!state.activeModel || !state.models.includes(state.activeModel)) {
      state.activeModel =
        state.defaultModel && state.models.includes(state.defaultModel)
          ? state.defaultModel
          : state.models[0] || null;
    }
    renderModelSelect();
  }

  function renderModelSelect() {
    const sel = document.getElementById("model-select");
    sel.innerHTML = "";
    if (!state.models.length) {
      const o = document.createElement("option");
      o.value = "";
      o.textContent = "Ollama not reachable";
      sel.appendChild(o);
      sel.disabled = true;
      return;
    }
    sel.disabled = false;
    state.models.forEach((m) => {
      const o = document.createElement("option");
      o.value = m;
      o.textContent = m;
      sel.appendChild(o);
    });
    if (state.activeModel) sel.value = state.activeModel;
  }

  document.getElementById("model-select").addEventListener("change", (e) => {
    state.activeModel = e.target.value;
  });
  document.getElementById("model-refresh").addEventListener("click", loadModels);

  // ============================================================
  // Compose — chat-like generation
  // ============================================================

  const genEls = {
    input: document.getElementById("gen-input"),
    btn: document.getElementById("gen-btn"),
    progress: document.getElementById("gen-progress"),
    statusText: document.getElementById("gen-status-text"),
    stream: document.getElementById("gen-stream"),
    result: document.getElementById("gen-result"),
    title: document.getElementById("g-title"),
    slug: document.getElementById("g-slug"),
    meta: document.getElementById("g-meta"),
    initial: document.getElementById("g-initial"),
    evaluator: document.getElementById("g-evaluator"),
    config: document.getElementById("g-config"),
    regen: document.getElementById("g-regen"),
    saverun: document.getElementById("g-saverun"),
  };

  // file sub-tabs in the generated result
  document.querySelectorAll("#g-filetabs .filetab").forEach((tab) =>
    tab.addEventListener("click", () => {
      document.querySelectorAll("#g-filetabs .filetab").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      const w = tab.dataset.file;
      genEls.initial.classList.toggle("hidden", w !== "initial");
      genEls.evaluator.classList.toggle("hidden", w !== "evaluator");
      genEls.config.classList.toggle("hidden", w !== "config");
    })
  );

  async function generateHarness(statement) {
    state.genStatement = statement;
    genEls.btn.disabled = true;
    genEls.result.style.display = "none";
    genEls.progress.style.display = "block";
    genEls.stream.textContent = "";
    genEls.statusText.textContent = "Reading the problem and writing tests…";

    const model = state.activeModel || undefined;
    let res;
    try {
      res = await fetch("/api/generate/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ statement, model }),
      });
    } catch (e) {
      return failGen("Could not reach the server: " + e.message);
    }
    if (!res.ok || !res.body) {
      return failGen("Generation request failed (" + res.status + ").");
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const chunk = buf.slice(0, idx).trim();
          buf = buf.slice(idx + 2);
          if (!chunk.startsWith("data:")) continue;
          let ev;
          try {
            ev = JSON.parse(chunk.slice(5).trim());
          } catch (e) {
            continue;
          }
          handleGenEvent(ev);
        }
      }
    } catch (e) {
      return failGen("Stream interrupted: " + e.message);
    } finally {
      // Release the streamed connection so the browser doesn't reuse it for
      // the next request (Safari otherwise fails the Save with "Load failed").
      try {
        await reader.cancel();
      } catch (e) {}
    }
    genEls.btn.disabled = false;
  }

  function failGen(msg) {
    genEls.progress.style.display = "none";
    genEls.btn.disabled = false;
    alert(msg);
  }

  function handleGenEvent(ev) {
    if (ev.type === "token") {
      genEls.stream.textContent += ev.text;
      genEls.stream.scrollTop = genEls.stream.scrollHeight;
    } else if (ev.type === "result") {
      genEls.progress.style.display = "none";
      genEls.result.style.display = "block";
      genEls.title.value = ev.spec.title || "Generated problem";
      genEls.slug.textContent = `generated_experiments/${ev.slug}/`;
      const n = (ev.spec.test_cases || []).length;
      genEls.meta.innerHTML = `<span class="ok">✓</span> harness generated · function <span class="mono">${ev.spec.function_name}</span> · ${n} test cases`;
      genEls.initial.value = ev.files.initial_program;
      genEls.evaluator.value = ev.files.evaluator;
      genEls.config.value = ev.files.config;
      state.activeSlug = ev.slug;
      genEls.btn.disabled = false;
    } else if (ev.type === "error") {
      failGen("Generation failed: " + ev.message + "\n\nTry rephrasing the problem.");
    }
  }

  genEls.btn.addEventListener("click", () => {
    const s = genEls.input.value.trim();
    if (!s) return alert("Paste a problem first.");
    generateHarness(s);
  });
  genEls.regen.addEventListener("click", () => {
    if (state.genStatement) generateHarness(state.genStatement);
  });

  genEls.saverun.addEventListener("click", async () => {
    genEls.saverun.disabled = true;
    genEls.saverun.textContent = "Saving…";
    try {
      const r = await postJSONRetry("/api/experiments", {
        title: genEls.title.value.trim() || "Generated problem",
        statement: state.genStatement,
        initial_program: genEls.initial.value,
        evaluator: genEls.evaluator.value,
        config: genEls.config.value,
      });
      state.activeSlug = r.slug;
      state.expDetail = null;
      await loadExperiments();
      showScreen("run");
    } catch (e) {
      alert("Save failed: " + e.message);
    } finally {
      genEls.saverun.disabled = false;
      genEls.saverun.textContent = "Save & go to Run";
    }
  });

  // ============================================================
  // Run
  // ============================================================

  async function renderRun() {
    const noexp = document.getElementById("run-noexp");
    const body = document.getElementById("run-body");
    if (!state.activeSlug) {
      noexp.style.display = "block";
      body.style.display = "none";
      return;
    }
    noexp.style.display = "none";
    body.style.display = "block";
    fillExpSelect("run-exp-select", state.activeSlug);

    if (!state.expDetail || state.expDetail.slug !== state.activeSlug) {
      try {
        state.expDetail = await getJSON(`/api/experiments/${state.activeSlug}`);
      } catch (e) {
        state.expDetail = null;
      }
    }
    document.getElementById("r-slug").textContent = state.activeSlug;
    document.getElementById("r-path").textContent = state.expDetail ? state.expDetail.path : "";
    const py = document.getElementById("r-python");
    if (!py.value) py.value = "/opt/anaconda3/envs/OpenEvolve/bin/python";
    updateCmdPreview();
  }

  document.getElementById("run-exp-select").addEventListener("change", (e) => {
    state.activeSlug = e.target.value;
    state.expDetail = null;
    renderRun();
  });

  function updateCmdPreview() {
    if (!state.expDetail) return;
    const expPath = state.expDetail.path;
    const projRoot = expPath.replace(/\/generated_experiments\/[^/]+$/, "");
    const py = document.getElementById("r-python").value || "python";
    const iters = document.getElementById("r-iterations").value || "20";
    const target = document.getElementById("r-target").value || "1.0";
    const cmd = [
      py,
      `${projRoot}/third_party/openevolve/openevolve-run.py`,
      `${expPath}/initial_program.py`,
      `${expPath}/evaluator.py`,
      "--config",
      `${expPath}/config.yaml`,
      "--output",
      `${expPath}/run_<timestamp>`,
      "--iterations",
      iters,
      "--target-score",
      target,
    ].join(" ");
    document.getElementById("r-cmd").textContent = cmd;
  }
  ["r-iterations", "r-python", "r-target"].forEach((id) =>
    document.getElementById(id).addEventListener("input", updateCmdPreview)
  );

  document.getElementById("r-exec").addEventListener("click", async () => {
    const btn = document.getElementById("r-exec");
    btn.disabled = true;
    try {
      const r = await postJSON("/api/runs", {
        slug: state.activeSlug,
        iterations: parseInt(document.getElementById("r-iterations").value, 10) || 20,
        python: document.getElementById("r-python").value,
        target_score: parseFloat(document.getElementById("r-target").value) || 1.0,
        model: state.activeModel || undefined,
      });
      state.runId = r.run_id;
      state.resultsRunId = r.run_id;
      state.resultsSlug = state.activeSlug;
      const banner = document.getElementById("r-banner");
      banner.className = "banner success";
      banner.innerHTML = `Launched · <span class="mono">PID ${r.pid}</span> · model <span class="mono">${state.activeModel || "default"}</span> · stops at score ${document.getElementById("r-target").value}`;
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
      if (s.state === "running") setTopStatus("running", `Running · iter ${s.iters_done}`);
      else if (s.state === "done") setTopStatus("done", `Done · iter ${s.iters_done}`);
      else if (s.state === "error") setTopStatus("error", "Error");
      else setTopStatus("", "Idle");

      const lt = document.getElementById("r-logtail");
      if (lt) lt.textContent = s.log_tail || "(no output yet)";

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

  // ============================================================
  // Results
  // ============================================================

  async function renderResults() {
    const noexp = document.getElementById("res-noexp");
    const body = document.getElementById("res-body");
    const slug = state.resultsSlug || state.activeSlug;
    if (!slug) {
      noexp.style.display = "block";
      body.style.display = "none";
      return;
    }
    state.resultsSlug = slug;
    fillExpSelect("res-exp-select", slug);

    let runs = [];
    try {
      runs = await getJSON(`/api/experiments/${slug}/runs`);
    } catch (e) {}
    if (!runs.length) {
      noexp.textContent = "No runs yet for this experiment.";
      noexp.style.display = "block";
      body.style.display = "none";
      return;
    }
    noexp.style.display = "none";
    body.style.display = "block";

    const runSel = document.getElementById("res-run-select");
    runSel.innerHTML = "";
    runs.forEach((r) => {
      const o = document.createElement("option");
      o.value = r.run_id;
      o.textContent = r.run_id;
      runSel.appendChild(o);
    });
    const want =
      state.resultsRunId && runs.find((r) => r.run_id === state.resultsRunId)
        ? state.resultsRunId
        : runs[0].run_id;
    runSel.value = want;
    state.resultsRunId = want;

    if (window.Lineage) window.Lineage.resetSelection();
    window.__resultsSelected = false;
    loadResultsData();
  }

  document.getElementById("res-exp-select").addEventListener("change", (e) => {
    state.resultsSlug = e.target.value;
    state.resultsRunId = null;
    renderResults();
  });
  document.getElementById("res-run-select").addEventListener("change", (e) => {
    state.resultsRunId = e.target.value;
    if (window.Lineage) window.Lineage.resetSelection();
    window.__resultsSelected = false;
    loadResultsData();
  });

  async function loadResultsData() {
    const runId = state.resultsRunId;
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
    if (window.Lineage) {
      window.Lineage.render(runId, lineage, onNodeClick);
      if (lineage.best_id && !window.__resultsSelected) {
        onNodeClick(lineage.best_id);
        window.Lineage.select(lineage.best_id);
        window.__resultsSelected = true;
      }
    }
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
    const runId = state.resultsRunId;
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
    const real = nodes.filter((n) => n.id !== "__root__");
    const bestNode = lineage.best_id ? real.find((n) => n.id === lineage.best_id) : null;
    const bestScore =
      bestNode && bestNode.score != null
        ? bestNode.score
        : status.best_score != null
        ? status.best_score
        : null;
    const scoreCol = window.Lineage ? window.Lineage.scoreColor(bestScore) : "#fff";
    const cards = [
      { label: "Best score", value: bestScore == null ? "—" : bestScore.toFixed(4), color: scoreCol, mono: true },
      { label: "Candidates", value: real.length },
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
    const runId = state.resultsRunId;
    if (!runId) return;
    try {
      const node = await getJSON(`/api/runs/${runId}/node/${nodeId}`);
      if (window.Lineage) window.Lineage.renderInspector(node);
    } catch (e) {}
  }

  // ---------- init ----------

  (async function init() {
    try {
      await Promise.all([loadExperiments(), loadModels()]);
    } catch (e) {
      console.error(e);
    }
    setTopStatus("", "Idle");
    showScreen("compose");
  })();
})();
