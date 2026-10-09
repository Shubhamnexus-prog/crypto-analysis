(() => {
  "use strict";
  const COINS = JSON.parse(document.getElementById("coins-data").textContent);
  const CFG = JSON.parse(document.getElementById("cfg").textContent);
  const state = { coin: "BTC", days: 90, sma: true, bb: false, corr: "returns", compare: null };
  const charts = {};
  const sparkCharts = {};
  const lastPrice = {};
  const $ = (s) => document.querySelector(s);

  Chart.defaults.color = "#8793b2";
  Chart.defaults.borderColor = "#232c47";
  Chart.defaults.font.family = "Inter, system-ui, sans-serif";
  Chart.defaults.animation.duration = 300;
  Chart.defaults.plugins.legend.labels.boxWidth = 10;
  Chart.defaults.maintainAspectRatio = false;

  // ---------- helpers
  const usd = (v) => v == null ? "–" : "$" + Number(v).toLocaleString(undefined, {
    minimumFractionDigits: v < 10 ? 4 : 2, maximumFractionDigits: v < 10 ? 4 : 2 });
  const pct = (v, d = 2) => v == null ? "–" : (v > 0 ? "+" : "") + Number(v).toFixed(d) + "%";
  const compact = (v) => Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 2 }).format(v);
  const cls = (v) => (v >= 0 ? "up" : "down");

  async function api(url) {
    const r = await fetch(url);
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || `Request failed (${r.status})`);
    return data;
  }
  function showError(msg) { const a = $("#alert"); a.textContent = msg; a.hidden = !msg; }
  function draw(id, config) {
    if (charts[id]) { charts[id].destroy(); }
    charts[id] = new Chart($("#" + id), config);
  }
  const lineDs = (label, data, color, extra = {}) => ({
    label, data, borderColor: color, backgroundColor: color + "22", borderWidth: 1.6,
    pointRadius: 0, tension: 0.15, spanGaps: true, ...extra });
  const baseOpts = (extra = {}) => ({
    interaction: { mode: "index", intersect: false },
    plugins: { legend: { display: true } },
    scales: { x: { ticks: { maxTicksLimit: 8, maxRotation: 0 }, grid: { display: false } }, y: {} },
    ...extra });

  // ---------- live ticker
  function renderTicker(coins) {
    const el = $("#ticker");
    if (!el.children.length) {
      el.innerHTML = coins.map((c) => `
        <div class="coin" data-sym="${c.symbol}">
          <div class="row"><div><span class="sym">${c.symbol}</span> <span class="nm">${c.name}</span></div>
          <span class="chg" data-chg></span></div>
          <div class="price" data-price></div><canvas></canvas></div>`).join("");
      el.querySelectorAll(".coin").forEach((n) => n.addEventListener("click", () => selectCoin(n.dataset.sym)));
    }
    coins.forEach((c) => {
      const node = el.querySelector(`[data-sym="${c.symbol}"]`);
      node.classList.toggle("active", c.symbol === state.coin);
      const priceEl = node.querySelector("[data-price]");
      priceEl.textContent = usd(c.price);
      const prev = lastPrice[c.symbol];
      if (prev != null && prev !== c.price) {
        priceEl.classList.remove("flash-up", "flash-down"); void priceEl.offsetWidth;
        priceEl.classList.add(c.price > prev ? "flash-up" : "flash-down");
      }
      lastPrice[c.symbol] = c.price;
      const chg = node.querySelector("[data-chg]");
      chg.textContent = pct(c.change_24h); chg.className = "chg " + cls(c.change_24h);
      const color = c.change_24h >= 0 ? "#22c55e" : "#ef4444";
      if (sparkCharts[c.symbol]) {
        const ch = sparkCharts[c.symbol];
        ch.data.labels = c.spark.map((_, i) => i); ch.data.datasets[0].data = c.spark;
        ch.data.datasets[0].borderColor = color; ch.update("none");
      } else {
        sparkCharts[c.symbol] = new Chart(node.querySelector("canvas"), {
          type: "line",
          data: { labels: c.spark.map((_, i) => i), datasets: [{ data: c.spark, borderColor: color, borderWidth: 1.5, pointRadius: 0, tension: .3 }] },
          options: { animation: false, plugins: { legend: { display: false }, tooltip: { enabled: false } },
                     scales: { x: { display: false }, y: { display: false } } } });
      }
    });
  }

  async function refreshLive() {
    try {
      const data = await api(CFG.urls.live);
      renderTicker(data.coins);
      $("#pulse").className = "pulse live";
      $("#updated").textContent = "Live · updated " + new Date(data.updated).toLocaleTimeString();
      showError("");
      refreshIntraday();
      loadPretrainedLive();
    } catch (e) {
      $("#pulse").className = "pulse err";
      $("#updated").textContent = "Offline · retrying…";
      showError("Live feed problem: " + e.message);
    }
  }

  async function refreshIntraday() {
    try {
      const d = await api(CFG.urls.intraday + state.coin + "/");
      $("#intradayTitle").textContent = `${COINS[state.coin].name} · 5-min candles`;
      draw("intradayChart", { type: "line",
        data: { labels: d.times, datasets: [lineDs(state.coin, d.close, COINS[state.coin].color, { fill: true })] },
        options: baseOpts({ plugins: { legend: { display: false } } }) });
    } catch (e) { /* ticker error banner already covers this */ }
  }

  // ---------- coin analysis
  async function loadCoin() {
    try {
      const d = await api(`${CFG.urls.indicators}${state.coin}/?days=${state.days}`);
      const color = COINS[state.coin].color;

      const ds = [lineDs("Close", d.close, color, { borderWidth: 2, fill: !state.sma && !state.bb })];
      if (state.sma) {
        ds.push(lineDs("SMA 20", d.sma20, "#38bdf8", { borderWidth: 1.2 }));
        ds.push(lineDs("SMA 50", d.sma50, "#f472b6", { borderWidth: 1.2 }));
      }
      if (state.bb) {
        ds.push(lineDs("BB upper", d.bb_upper, "#94a3b8", { borderDash: [4, 4], borderWidth: 1 }));
        ds.push(lineDs("BB lower", d.bb_lower, "#94a3b8", { borderDash: [4, 4], borderWidth: 1 }));
      }
      draw("priceChart", { type: "line", data: { labels: d.dates, datasets: ds }, options: baseOpts() });

      draw("volumeChart", { type: "bar",
        data: { labels: d.dates, datasets: [{ label: "Volume", data: d.volume, backgroundColor: color + "99" }] },
        options: baseOpts({ plugins: { legend: { display: false } },
          scales: { x: { ticks: { maxTicksLimit: 6 }, grid: { display: false } }, y: { ticks: { callback: compact } } } }) });

      const flat = (v) => d.dates.map(() => v);
      draw("rsiChart", { type: "line",
        data: { labels: d.dates, datasets: [
          lineDs("RSI", d.rsi, "#a78bfa", { borderWidth: 1.8 }),
          lineDs("Overbought", flat(70), "#ef4444", { borderDash: [5, 4], borderWidth: 1 }),
          lineDs("Oversold", flat(30), "#22c55e", { borderDash: [5, 4], borderWidth: 1 })] },
        options: baseOpts({ scales: { x: { ticks: { maxTicksLimit: 6 }, grid: { display: false } }, y: { min: 0, max: 100 } } }) });

      draw("ddChart", { type: "line",
        data: { labels: d.dates, datasets: [lineDs("Drawdown %", d.drawdown, "#ef4444", { fill: true })] },
        options: baseOpts({ plugins: { legend: { display: false } } }) });

      draw("histChart", { type: "bar",
        data: { labels: d.histogram.bins.map((b) => b.toFixed(1) + "%"), datasets: [{ label: "Days", data: d.histogram.counts, backgroundColor: color + "aa" }] },
        options: baseOpts({ plugins: { legend: { display: false } }, scales: { x: { ticks: { maxTicksLimit: 8 }, grid: { display: false } }, y: {} } }) });

      renderKpis(d.stats);
    } catch (e) { showError(e.message); }
  }

  function renderKpis(s) {
    if (!s || !s.price) { $("#kpis").innerHTML = ""; return; }
    const items = [
      ["Price", usd(s.price), ""], ["Return (range)", pct(s.total_return), cls(s.total_return)],
      ["Volatility (ann.)", s.volatility.toFixed(1) + "%", ""], ["Sharpe", s.sharpe == null ? "–" : s.sharpe.toFixed(2), cls(s.sharpe || 0)],
      ["Max drawdown", s.max_drawdown.toFixed(1) + "%", "down"], ["Best day", pct(s.best_day), "up"], ["Worst day", pct(s.worst_day), "down"]];
    $("#kpis").innerHTML = items.map(([l, v, c]) => `<div class="kpi"><div class="l">${l}</div><div class="v ${c}">${v}</div></div>`).join("");
  }

  // ---------- cross-coin comparison
  async function loadCompare() {
    try {
      const d = await api(`${CFG.urls.compare}?days=${state.days}`);
      state.compare = d;
      const colorOf = (s) => (COINS[s] || {}).color || "#888";
      draw("compareChart", { type: "line",
        data: { labels: d.dates, datasets: d.symbols.map((s) => lineDs(s, d.normalized[s], colorOf(s))) },
        options: baseOpts({ scales: { x: { ticks: { maxTicksLimit: 8 }, grid: { display: false } }, y: { ticks: { callback: (v) => v + "%" } } } }) });
      draw("volChart", { type: "line",
        data: { labels: d.dates, datasets: d.symbols.filter((s) => s !== "USDT").map((s) => lineDs(s, d.volatility[s], colorOf(s))) },
        options: baseOpts() });
      renderHeatmap(); renderStats(d.stats);
    } catch (e) { showError(e.message); }
  }

  function heat(v) {
    if (v == null) return "#1b2340";
    const a = Math.min(1, Math.abs(v));
    return v >= 0 ? `rgba(99,102,241,${0.15 + a * 0.75})` : `rgba(239,68,68,${0.15 + a * 0.75})`;
  }
  function renderHeatmap() {
    const d = state.compare; if (!d) return;
    const m = state.corr === "returns" ? d.corr_returns : d.corr_prices;
    const n = d.symbols.length, el = $("#heatmap");
    el.style.gridTemplateColumns = `46px repeat(${n}, 1fr)`;
    let html = `<div class="lbl"></div>` + d.symbols.map((s) => `<div class="lbl">${s}</div>`).join("");
    m.forEach((row, i) => {
      html += `<div class="lbl">${d.symbols[i]}</div>` + row.map((v) => `<div style="background:${heat(v)}">${v == null ? "–" : v.toFixed(2)}</div>`).join("");
    });
    el.innerHTML = html;
  }
  function renderStats(rows) {
    const head = ["Coin", "Price", "Return", "Volatility", "Sharpe", "Max DD", "Best day", "Worst day"];
    $("#statsTable").innerHTML = `<thead><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>` +
      rows.map((r) => `<tr><td><b>${r.symbol}</b></td><td>${usd(r.price)}</td><td class="${cls(r.total_return)}">${pct(r.total_return)}</td>
        <td>${r.volatility.toFixed(1)}%</td><td>${r.sharpe == null ? "–" : r.sharpe.toFixed(2)}</td>
        <td class="down">${r.max_drawdown.toFixed(1)}%</td><td class="up">${pct(r.best_day)}</td><td class="down">${pct(r.worst_day)}</td></tr>`).join("") + "</tbody>";
  }

  // ---------- ML
  async function loadPredict() {
    try {
      const d = await api(CFG.urls.predict);
      $("#mlLoading").hidden = true; $("#mlBody").hidden = false;
      const f = d.forecast;
      $("#forecast").innerHTML = `
        <div class="fbox"><div class="l">Last close (${f.as_of})</div><div class="v">${usd(f.last_close)}</div></div>
        <div class="fbox"><div class="l">Predicted next close</div><div class="v ${f.direction === "up" ? "up" : "down"}">${usd(f.predicted_close)}</div></div>
        <div class="fbox"><div class="l">Expected move</div><div class="v ${f.direction === "up" ? "up" : "down"}">${pct(f.predicted_return)}</div></div>
        <div class="fbox"><div class="l">Best model</div><div class="v" style="font-size:16px">${d.best_model}</div></div>`;

      const ts = d.test_series;
      draw("mlLine", { type: "line", data: { labels: ts.dates, datasets: [
        lineDs("Actual", ts.actual, "#e6ebf7", { borderWidth: 2 }),
        lineDs(d.best_model, ts.predicted, "#6366f1", { borderWidth: 1.8 }),
        lineDs("Naive (yesterday)", ts.naive, "#64748b", { borderDash: [4, 4], borderWidth: 1 })] }, options: baseOpts() });

      const sorted = [...d.metrics].sort((a, b) => a.rmse - b.rmse);
      draw("mlBar", { type: "bar", data: { labels: sorted.map((m) => m.model),
        datasets: [{ data: sorted.map((m) => m.rmse),
          backgroundColor: sorted.map((m) => m.model === d.best_model ? "#6366f1" : m.model === "Naive baseline" ? "#64748b" : "#334170") }] },
        options: baseOpts({ indexAxis: "y", plugins: { legend: { display: false } }, scales: { x: {}, y: { grid: { display: false } } } }) });

      const head = ["Model", "RMSE ($)", "MAE ($)", "MAPE %", "R²", "Direction %"];
      $("#mlTable").innerHTML = `<thead><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>` +
        sorted.map((m) => `<tr class="${m.model === d.best_model ? "best" : m.model === "Naive baseline" ? "base" : ""}">
          <td>${m.model}</td><td>${m.rmse.toFixed(0)}</td><td>${m.mae.toFixed(0)}</td><td>${m.mape.toFixed(2)}</td><td>${m.r2.toFixed(3)}</td><td>${m.dir_acc == null ? "–" : m.dir_acc.toFixed(1)}</td></tr>`).join("") + "</tbody>";

      draw("mlImp", { type: "bar", data: { labels: d.importances.map((i) => i.feature),
        datasets: [{ data: d.importances.map((i) => i.importance), backgroundColor: "#f7931a" }] },
        options: baseOpts({ indexAxis: "y", plugins: { legend: { display: false } }, scales: { x: {}, y: { grid: { display: false }, ticks: { font: { size: 10 } } } } }) });

      $("#mlNote").textContent = `Trained on ${d.train_rows} days, tested on ${d.test_rows} days (from ${d.split_date}). ${d.disclaimer}`;
    } catch (e) {
      $("#mlLoading").textContent = "Forecast unavailable: " + e.message;
    }
  }


  // ---------- pretrained model (notebook .pkl)
  const pm = { log: [], filled: false, live: null, chart: null };
  const pmIn = { USDT_Close: "#pmUsdtClose", USDT_Volume: "#pmUsdtVolume", BNB_Close: "#pmBnbClose", BNB_Volume: "#pmBnbVolume" };

  function pmFill(inputs) {
    Object.entries(pmIn).forEach(([k, sel]) => { $(sel).value = inputs[k]; });
  }
  function pmWarn(list) {
    const el = $("#pmWarn");
    el.hidden = !list || !list.length;
    el.textContent = (list || []).join(" ");
  }

  async function loadPretrainedLive() {
    try {
      const d = await api(CFG.urls.pmLive);
      pm.live = d;
      $("#pmCards").innerHTML = `
        <div class="fbox"><div class="l">Live BTC (actual)</div><div class="v">${usd(d.actual_btc)}</div></div>
        <div class="fbox"><div class="l">Model prediction</div><div class="v" style="color:#818cf8">${usd(d.predicted_btc)}</div></div>
        <div class="fbox"><div class="l">Difference</div><div class="v ${cls(d.diff)}">${d.diff >= 0 ? "+" : "-"}${usd(Math.abs(d.diff))} <span style="font-size:13px">(${pct(d.diff_pct)})</span></div></div>
        <div class="fbox"><div class="l">Signal</div><div class="v" style="font-size:16px">${d.diff > 0 ? "Model says BTC looks undervalued" : "Model says BTC looks overvalued"}</div></div>`;
      pmWarn(d.warnings);
      if (!pm.filled) { pmFill(d.inputs); pm.filled = true; }

      pm.log.push({ t: new Date().toLocaleTimeString(), a: d.actual_btc, p: d.predicted_btc });
      if (pm.log.length > 60) pm.log.shift();
      if (!pm.chart) {
        pm.chart = new Chart($("#pmLive"), { type: "line",
          data: { labels: [], datasets: [lineDs("Actual BTC", [], "#e6ebf7", { borderWidth: 2 }), lineDs("Model prediction", [], "#6366f1", { borderWidth: 2 })] },
          options: baseOpts() });
      }
      pm.chart.data.labels = pm.log.map((r) => r.t);
      pm.chart.data.datasets[0].data = pm.log.map((r) => r.a);
      pm.chart.data.datasets[1].data = pm.log.map((r) => r.p);
      pm.chart.update("none");
    } catch (e) {
      $("#pmCards").innerHTML = `<div class="muted">Model prediction unavailable: ${e.message}</div>`;
    }
  }

  async function runPmPredict() {
    const q = new URLSearchParams({
      usdt_close: $("#pmUsdtClose").value, usdt_volume: $("#pmUsdtVolume").value,
      bnb_close: $("#pmBnbClose").value, bnb_volume: $("#pmBnbVolume").value });
    const out = $("#pmResult");
    try {
      const d = await api(CFG.urls.pmPredict + "?" + q);
      const sc = Object.entries(d.scaled).map(([k, v]) => `${k.replace("_", " ")}: ${v}`).join(" · ");
      out.innerHTML = `<div class="big">${usd(d.predicted_btc)}</div><div class="sc">Scaled inputs (0-1): ${sc}</div>` +
        (d.warnings.length ? `<div class="alert warn">${d.warnings.join(" ")}</div>` : "");
    } catch (e) {
      out.innerHTML = `<div class="alert">${e.message}</div>`;
    }
  }

  async function loadPmBacktest() {
    try {
      const d = await api(`${CFG.urls.pmBacktest}?days=${Math.max(state.days, 90)}`);
      draw("pmBacktest", { type: "line",
        data: { labels: d.dates, datasets: [lineDs("Actual BTC", d.actual, "#e6ebf7", { borderWidth: 2 }), lineDs("Model prediction", d.predicted, "#6366f1")] },
        options: baseOpts() });
      const m = d.metrics_window, r = d.metrics_recent_90d;
      $("#pmMetrics").textContent = `· window: R² ${m.r2}, MAPE ${m.mape}%, RMSE $${m.rmse.toLocaleString()} · last 90d: R² ${r.r2}, MAPE ${r.mape}%`;
      $("#pmNote").textContent = `${d.note} Inputs inside training range: ${d.in_range_pct}% of days.`;
    } catch (e) { $("#pmNote").textContent = "Backtest unavailable: " + e.message; }
  }

  // ---------- UI wiring
  function selectCoin(sym) {
    state.coin = sym;
    document.querySelectorAll("#coinTabs button").forEach((b) => b.classList.toggle("active", b.dataset.sym === sym));
    document.querySelectorAll(".coin").forEach((n) => n.classList.toggle("active", n.dataset.sym === sym));
    loadCoin(); refreshIntraday();
  }
  $("#coinTabs").innerHTML = Object.keys(COINS).map((s) => `<button data-sym="${s}" class="${s === state.coin ? "active" : ""}">${s}</button>`).join("");
  $("#coinTabs").addEventListener("click", (e) => { if (e.target.dataset.sym) selectCoin(e.target.dataset.sym); });
  $("#rangeTabs").addEventListener("click", (e) => {
    if (!e.target.dataset.days) return;
    state.days = +e.target.dataset.days;
    document.querySelectorAll("#rangeTabs button").forEach((b) => b.classList.toggle("active", b === e.target));
    loadCoin(); loadCompare(); loadPmBacktest();
  });
  $("#pmPredict").addEventListener("click", runPmPredict);
  $("#pmUseLive").addEventListener("click", () => { if (pm.live) { pmFill(pm.live.inputs); runPmPredict(); } });
  $("#toggleSma").addEventListener("change", (e) => { state.sma = e.target.checked; loadCoin(); });
  $("#toggleBb").addEventListener("change", (e) => { state.bb = e.target.checked; loadCoin(); });
  $("#corrTabs").addEventListener("click", (e) => {
    if (!e.target.dataset.mode) return;
    state.corr = e.target.dataset.mode;
    document.querySelectorAll("#corrTabs button").forEach((b) => b.classList.toggle("active", b === e.target));
    renderHeatmap();
  });

  // ---------- boot
  refreshLive();
  loadCoin();
  loadCompare();
  loadPredict();
  loadPmBacktest();
  setInterval(refreshLive, CFG.refresh);
})();
