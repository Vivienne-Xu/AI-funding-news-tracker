/* One shared Chart.js setup for the monthly report (brief Section 10): thin bars with small rounded ends,
   light gridlines, custom HTML legends, no 3D, no pie charts. The email PNGs are screenshots of these same charts. */
(function () {
  var INK = "#0B1F33", BODY = "#3A4655", MUTED = "#7A8594", RULE = "#DDE2E8", ACCENT = "#1F5BD6", SOFT = "#A7B0BC";
  var REGION = { US: "#0B1F33", Europe: "#1F8A8A", Asia: "#C9A227", RoW: "#A7B0BC" };

  Chart.defaults.font.family = "Inter, Arial, Helvetica, sans-serif";
  Chart.defaults.font.size = 12;
  Chart.defaults.color = BODY;
  Chart.defaults.animation = false; // static output for PDF and PNG
  Chart.defaults.plugins.legend.display = false; // legends are drawn as HTML below

  function money(v) {
    if (v >= 1e9) return "$" + (v / 1e9).toFixed(1).replace(/\.0$/, "") + "B";
    if (v >= 1e6) return "$" + (v / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
    return "$" + Math.round(v / 1e3) + "K";
  }

  function legend(id, items) {
    var el = document.getElementById(id);
    if (!el) return;
    el.innerHTML = items.map(function (it) {
      return '<span class="m-key"><i style="background:' + it.color + '"></i>' + it.label + "</span>";
    }).join("");
  }

  function horizontalBars(canvasId, legendId, labels, series) {
    new Chart(document.getElementById(canvasId), {
      type: "bar",
      data: {
        labels: labels,
        datasets: series.map(function (s) {
          return { label: s.label, data: s.data, backgroundColor: s.color, borderRadius: 3, barThickness: 9, categoryPercentage: 0.8 };
        }),
      },
      options: {
        indexAxis: "y", responsive: true, maintainAspectRatio: false,
        scales: {
          x: { grid: { display: false }, border: { display: false }, ticks: { callback: function (v) { return v + "%"; } }, suggestedMax: 100 },
          y: { grid: { display: false }, border: { display: false } },
        },
        plugins: { tooltip: { callbacks: { label: function (c) { return c.dataset.label + ": " + c.parsed.x + "%"; } } } },
      },
    });
    legend(legendId, series.map(function (s) { return { label: s.label, color: s.color }; }));
  }

  function regionTrend(canvasId, legendId, rows) {
    var regions = ["US", "Europe", "Asia", "RoW"];
    new Chart(document.getElementById(canvasId), {
      type: "bar",
      data: {
        labels: rows.map(function (r) { return r.month; }),
        datasets: regions.map(function (name) {
          return { label: name, data: rows.map(function (r) { return r[name]; }), backgroundColor: REGION[name], borderRadius: 2, maxBarThickness: 22 };
        }),
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        scales: {
          x: { stacked: true, grid: { display: false }, border: { display: false } },
          y: { stacked: true, grid: { color: RULE }, border: { display: false }, ticks: { callback: function (v) { return money(v); } } },
        },
        plugins: { tooltip: { callbacks: { label: function (c) { return c.dataset.label + ": " + money(c.parsed.y); } } } },
      },
    });
    legend(legendId, regions.map(function (n) { return { label: n, color: REGION[n] }; }));
  }

  window.renderCharts = function (data) {
    horizontalBars("chart-layers", "legend-layers", data.layers.map(function (l) { return l.layer; }), [
      { label: "All deals", data: data.layers.map(function (l) { return l.all_pct; }), color: INK },
      { label: "Excluding the two largest rounds", data: data.layers.map(function (l) { return l.ex_top2_pct; }), color: SOFT },
    ]);
    regionTrend("chart-global", "legend-global", data.region_trend);
    horizontalBars("chart-stage", "legend-stage", data.stages.map(function (s) { return s.stage; }), [
      { label: "Share of capital", data: data.stages.map(function (s) { return s.capital_pct; }), color: INK },
      { label: "Share of deals", data: data.stages.map(function (s) { return s.deal_pct; }), color: ACCENT },
    ]);
    window.__chartsReady = true;
  };
})();
