(() => {
  const PERIODS = [
    { id: "1m", label: "1M", days: 31 },
    { id: "3m", label: "3M", days: 93 },
    { id: "ytd", label: "YTD", days: null },
    { id: "1y", label: "1Y", days: 365 },
    { id: "all", label: "成立以来", days: null },
  ];
  const OVERLAY_COLORS = {
    sh000001: "#60a5fa",
    sz399001: "#c084fc",
    sz399006: "#fb7185",
    sh000300: "#38bdf8",
    sz159915: "#a3e635",
  };

  const state = {
    catalog: null,
    page: null,
    key: "cyb-clock",
    period: "all",
    mode: "nav",
    overlays: new Set(["sz159915"]),
    benchmark: "sz159915",
    chart: null,
  };

  const $ = (id) => document.getElementById(id);

  function fmtPct(x, digits = 2) {
    if (x === null || x === undefined || Number.isNaN(x)) return "—";
    const pct = (x * 100).toFixed(digits);
    return (x > 0 ? "+" : "") + pct + "%";
  }
  function clsRet(x) {
    if (x === null || x === undefined || Number.isNaN(x)) return "";
    return x > 0 ? "up" : x < 0 ? "down" : "";
  }
  function fmtNum(x, digits = 2) {
    if (x === null || x === undefined || Number.isNaN(x)) return "—";
    return Number(x).toLocaleString("zh-CN", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
  }

  function tickClock() {
    const now = new Date();
    const text = now.toLocaleString("zh-CN", {
      timeZone: "Asia/Shanghai",
      hour12: false,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      weekday: "short",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
    $("bj-clock").textContent = "北京时间 " + text;
  }

  async function loadJson(url) {
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) throw new Error("无法读取 " + url);
    return res.json();
  }

  function showBanners(list) {
    const box = $("banners");
    box.innerHTML = "";
    if (!list || !list.length) {
      box.hidden = true;
      return;
    }
    box.hidden = false;
    const uniq = [...new Set(list)];
    uniq.forEach((msg) => {
      const div = document.createElement("div");
      div.textContent = msg;
      box.appendChild(div);
    });
  }

  function renderTabs() {
    const nav = $("tabs");
    nav.innerHTML = "";
    state.catalog.indices.forEach((item) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = item.tab;
      btn.className = item.key === state.key ? "active" : "";
      btn.addEventListener("click", () => switchPage(item.key));
      nav.appendChild(btn);
    });
  }

  function renderCards(page) {
    const p = page.performance;
    const pos = page.position;
    const posClass = pos.units >= pos.n ? "up" : pos.units > 0 ? "" : "down";
    const items = [
      { k: "年化涨跌幅", v: fmtPct(p.annualized_return), c: clsRet(p.annualized_return), s: "成立以来" },
      { k: "最大回撤", v: fmtPct(p.max_drawdown.value), c: "down", s: `${p.max_drawdown.peak_date} → ${p.max_drawdown.trough_date}` },
      { k: "Sharpe", v: fmtNum(p.sharpe), c: "", s: "未扣无风险利率" },
      { k: "暴露度", v: fmtPct(p.exposure, 1), c: "", s: "有仓交易日占比" },
      { k: "当前仓位", v: pos.label, c: posClass, s: pos.note || `${pos.units}/${pos.n}` },
      { k: "今年以来", v: fmtPct(p.ytd.return), c: clsRet(p.ytd.return), s: `${p.ytd.start_date} → ${p.ytd.end_date}` },
    ];
    $("metric-cards").innerHTML = items
      .map(
        (it) => `<article class="card"><div class="k">${it.k}</div><div class="v ${it.c}">${it.v}</div><div class="s">${it.s}</div></article>`
      )
      .join("");
  }

  function renderHero(page) {
    const pos = page.position;
    const pillClass = pos.units >= pos.n ? "pos-on" : pos.units > 0 ? "pos-mid" : "pos-off";
    $("hero-name").innerHTML = `${page.index.index_name_cn} <span class="pos-pill ${pillClass}">${pos.label}</span>`;
    const role = page.index.observation_only
      ? "观察仓位，不替代满仓时钟"
      : "当前邮件时钟（满仓规则）";
    $("hero-sub").textContent = `${page.index.rule} · ${role} · 基点 ${page.index.base_point}`;
    $("latest-point").textContent = fmtNum(page.performance.latest_point, 2);
    $("point-asof").textContent = page.index.data_end_date;
    $("asof-chip").textContent = `策略有效日 ${page.index.data_end_date}`;
  }

  function renderOverlays(page) {
    const box = $("overlay-box");
    box.innerHTML = "";
    (page.overlays || []).forEach((ov) => {
      const lab = document.createElement("label");
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = state.overlays.has(ov.key);
      cb.addEventListener("change", () => {
        if (cb.checked) state.overlays.add(ov.key);
        else state.overlays.delete(ov.key);
        drawChart();
      });
      lab.appendChild(cb);
      lab.appendChild(document.createTextNode(` ${ov.name} ${ov.code}`));
      box.appendChild(lab);
    });
  }

  function renderPeriodChips() {
    const box = $("period-chips");
    box.innerHTML = "";
    PERIODS.forEach((p) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = p.label;
      btn.className = p.id === state.period ? "active" : "";
      btn.addEventListener("click", () => {
        state.period = p.id;
        renderPeriodChips();
        drawChart();
      });
      box.appendChild(btn);
    });
  }

  function filterByPeriod(series) {
    if (!series || !series.length) return [];
    const end = series[series.length - 1].date;
    const endDate = new Date(end + "T00:00:00");
    let start = series[0].date;
    if (state.period === "ytd") {
      start = `${endDate.getFullYear()}-01-01`;
    } else if (state.period !== "all") {
      const spec = PERIODS.find((p) => p.id === state.period);
      const d = new Date(endDate);
      d.setDate(d.getDate() - spec.days);
      start = d.toISOString().slice(0, 10);
    }
    return series.filter((row) => row.date >= start);
  }

  function rebase(rows, field) {
    const usable = rows.filter((r) => r[field] != null);
    if (!usable.length) return [];
    const base = usable[0][field];
    if (!base) return [];
    return usable.map((r) => ({ time: r.date, value: (r[field] / base) * 1000 }));
  }

  function drawChart() {
    const el = $("chart");
    if (state.chart) {
      state.chart.remove();
      state.chart = null;
    }
    const page = state.page;
    const chart = LightweightCharts.createChart(el, {
      layout: { background: { color: "#16161d" }, textColor: "#a1a1aa" },
      grid: { vertLines: { color: "#2a2a33" }, horzLines: { color: "#2a2a33" } },
      rightPriceScale: {
        borderColor: "#2a2a33",
        autoScale: true,
        scaleMargins: { top: 0.06, bottom: 0.06 },
      },
      timeScale: {
        borderColor: "#2a2a33",
        minBarSpacing: 0.05,
        rightOffset: 2,
        fixLeftEdge: state.period === "all",
        fixRightEdge: state.period === "all",
      },
      autoSize: true,
    });
    state.chart = chart;
    if (state.mode === "kline" && page.etf_ohlc) {
      const candles = filterByPeriod(page.etf_ohlc).map((r) => ({
        time: r.date,
        open: r.open,
        high: r.high,
        low: r.low,
        close: r.close,
      }));
      const cs = chart.addCandlestickSeries({
        upColor: "#ef4444",
        downColor: "#22c55e",
        borderUpColor: "#ef4444",
        borderDownColor: "#22c55e",
        wickUpColor: "#ef4444",
        wickDownColor: "#22c55e",
      });
      cs.setData(candles);
      $("legend").textContent = "K线为创业板 ETF 159915 前复权，红涨绿跌。仅用于观察，不构成交易指令。";
      fitChart();
      return;
    }
    const sliced = filterByPeriod(page.series);
    const area = chart.addAreaSeries({
      lineColor: "#e4b649",
      topColor: "rgba(228,182,73,0.28)",
      bottomColor: "rgba(228,182,73,0.02)",
      lineWidth: 2,
    });
    area.setData(rebase(sliced, "nav"));
    const names = ["本序列"];
    (page.overlays || []).forEach((ov) => {
      if (!state.overlays.has(ov.key)) return;
      const rows = (page.overlay_series && page.overlay_series[ov.key]) || [];
      const line = chart.addLineSeries({
        color: OVERLAY_COLORS[ov.key] || "#94a3b8",
        lineWidth: 1,
      });
      line.setData(rebase(filterByPeriod(rows), "nav"));
      names.push(ov.name);
    });
    $("legend").textContent = "净值在当前窗口起点归一为 1000。对比层：" + names.join(" / ");
    fitChart();
  }

  function fitChart() {
    if (!state.chart) return;
    requestAnimationFrame(() => {
      if (state.chart) state.chart.timeScale().fitContent();
    });
  }

  function renderYearTable(page) {
    const select = $("bench-select");
    select.innerHTML = "";
    (page.overlays || []).forEach((ov) => {
      const opt = document.createElement("option");
      opt.value = ov.key;
      opt.textContent = `${ov.name} ${ov.code}`;
      if (ov.key === state.benchmark) opt.selected = true;
      select.appendChild(opt);
    });
    const rows = (page.yearly_excess && page.yearly_excess[state.benchmark]) || [];
    $("year-body").innerHTML = rows
      .map((r) => {
        const year = r.partial ? r.year + "*" : String(r.year);
        return `<tr><td>${year}</td><td class="${clsRet(r.index_return)}">${fmtPct(r.index_return)}</td><td class="${clsRet(r.benchmark_return)}">${fmtPct(r.benchmark_return)}</td><td class="${clsRet(r.excess)}">${fmtPct(r.excess)}</td></tr>`;
      })
      .join("");
  }

  function renderMethod(page) {
    const labels = {
      underlying: "标的",
      rule: "规则",
      entry: "入场",
      exit: "离场",
      position: "仓位",
      cost: "费用",
      window: "窗口",
      note: "说明",
      interpretation: "口径",
    };
    const m = page.methodology || {};
    $("method-list").innerHTML = Object.entries(m)
      .map(([k, v]) => `<li><strong>${labels[k] || k}</strong>：${v}</li>`)
      .join("");
    $("risk-list").innerHTML = (page.risk_notes || []).map((x) => `<li>${x}</li>`).join("");
  }

  async function switchPage(key) {
    state.key = key;
    const meta = state.catalog.indices.find((x) => x.key === key);
    const page = await loadJson(meta.agent_url);
    state.page = page;
    if (!(page.overlays || []).some((ov) => ov.key === state.benchmark)) {
      state.benchmark = page.default_benchmark || "sz159915";
    }
    renderTabs();
    renderHero(page);
    renderCards(page);
    renderOverlays(page);
    renderPeriodChips();
    renderYearTable(page);
    renderMethod(page);
    showBanners([...(state.catalog.banners || []), ...(page.banners || [])]);
    drawChart();
  }

  document.querySelectorAll(".mode-toggle button").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.mode = btn.dataset.mode;
      document.querySelectorAll(".mode-toggle button").forEach((b) => b.classList.toggle("active", b === btn));
      drawChart();
    });
  });
  $("bench-select").addEventListener("change", (e) => {
    state.benchmark = e.target.value;
    renderYearTable(state.page);
  });

  function beijingDateISO() {
    return new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Shanghai" });
  }

  function bindAmvDialog() {
    const dialog = $("amv-dialog");
    const form = $("amv-form");
    if (!dialog || !form) return;
    $("amv-open").addEventListener("click", () => {
      $("amv-date").value = beijingDateISO();
      $("amv-close").value = "";
      const fresh = (state.catalog && state.catalog.freshness) || {};
      const lastClose = fresh.amv_trusted_close;
      $("amv-hint").textContent = fresh.amv_trusted_as_of
        ? `上一有效日 ${fresh.amv_trusted_as_of}` +
          (lastClose != null ? `，收盘 ${Number(lastClose).toLocaleString("zh-CN")}` : "") +
          "。提交后还要在 GitHub 再点一次 Create issue。"
        : "提交后还要在 GitHub 再点一次 Create issue。";
      $("amv-status").hidden = true;
      $("amv-github-link").hidden = true;
      dialog.showModal();
      $("amv-close").focus();
    });
    $("amv-cancel").addEventListener("click", () => dialog.close());
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const date = $("amv-date").value;
      const close = Number($("amv-close").value);
      if (!date || !(close > 0)) return;
      const fresh = (state.catalog && state.catalog.freshness) || {};
      const lastClose = Number(fresh.amv_trusted_close);
      if (lastClose > 0 && Math.abs(close - lastClose) < 1e-6) {
        const ok = window.confirm(
          `这个收盘价和上一有效日 ${fresh.amv_trusted_as_of} 完全相同。\n` +
            "系统会写入，但不当新信号，仓位和净值都不会变。\n仍要提交吗？"
        );
        if (!ok) return;
      }
      const repo = (state.catalog && state.catalog.github_repo) || "CindyNNN/amv-research-clock";
      const title = `0AMV ${date}`;
      const body = [
        `date: ${date}`,
        `close: ${close}`,
        "",
        "请用仓库所有者账号直接点 Create issue，不要改格式。",
        "研究辅助，不是投资建议。",
      ].join("\n");
      const url =
        `https://github.com/${repo}/issues/new?title=${encodeURIComponent(title)}` +
        `&body=${encodeURIComponent(body)}`;
      const link = $("amv-github-link");
      link.href = url;
      link.hidden = false;
      const popup = window.open(url, "_blank", "noopener");
      $("amv-status").hidden = false;
      $("amv-status").textContent = popup
        ? "已打开 GitHub。请在新标签页点绿色 Create issue，约 1–2 分钟后回来刷新本页。"
        : "弹窗被拦截。请点下面「弹窗被拦截时点这里」，再在 GitHub 点 Create issue。";
    });
  }

  window.addEventListener("resize", fitChart);

  tickClock();
  setInterval(tickClock, 1000);
  bindAmvDialog();

  loadJson("data/agent/catalog.json")
    .then((catalog) => {
      state.catalog = catalog;
      document.title = catalog.site_name || document.title;
      return switchPage(state.key);
    })
    .catch((err) => {
      showBanners([
        "站点数据尚未生成，或请用本地 HTTP 打开：python -m http.server 8080 --directory site",
        String(err),
      ]);
      console.error(err);
    });
})();
