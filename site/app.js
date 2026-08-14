(() => {
  const PERIODS = [
    { id: "1m", label: "近1月", days: 31 },
    { id: "3m", label: "近3月", days: 93 },
    { id: "ytd", label: "今年", days: null },
    { id: "1y", label: "近1年", days: 365 },
    { id: "all", label: "全部", days: null },
  ];
  const OVERLAY_COLORS = {
    "cyb-clock": "#fb923c",
    sh000001: "#60a5fa",
    sz399001: "#c084fc",
    sz399006: "#fb7185",
    sh000300: "#38bdf8",
    sz159915: "#a3e635",
  };

  const state = {
    catalog: null,
    page: null,
    pageId: "cyb",
    key: "cyb-clock",
    period: "all",
    mode: "nav",
    overlaysByPage: {
      cyb: new Set(["sz159915"]),
      rotation: new Set(["cyb-clock", "sh000001"]),
    },
    benchmarkByPage: {
      cyb: "sz159915",
      rotation: "cyb-clock",
    },
    chart: null,
    holdingsDate: null,
    flowWindow: "1d",
  };

  const $ = (id) => document.getElementById(id);

  function overlays() {
    const pageId = state.pageId === "rotation" ? "rotation" : "cyb";
    if (!state.overlaysByPage[pageId]) state.overlaysByPage[pageId] = new Set();
    return state.overlaysByPage[pageId];
  }

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
  function fmtYi(x) {
    if (x === null || x === undefined || Number.isNaN(x)) return "—";
    return fmtNum(x, 2) + " 亿";
  }

  function flowWindows(page) {
    return (page && page.windows && page.windows.length)
      ? page.windows
      : [
          { id: "1d", label: "当日", net_field: "net_amount", pct_field: "pct_change" },
          { id: "3d", label: "3日", net_field: "net_3d", pct_field: "pct_3d" },
          { id: "5d", label: "5日", net_field: "net_5d", pct_field: "pct_5d" },
          { id: "10d", label: "10日", net_field: "net_10d", pct_field: "pct_10d" },
          { id: "20d", label: "20日", net_field: "net_20d", pct_field: "pct_20d" },
        ];
  }

  function flowWindowMeta(page, id) {
    return flowWindows(page).find((w) => w.id === id) || flowWindows(page)[0];
  }

  function flowWindowLabel(page, id) {
    return (flowWindowMeta(page, id) || {}).label || "当日";
  }

  function flowNet(page, id) {
    if (page.totals && page.totals[id] != null) return page.totals[id];
    const field = (flowWindowMeta(page, id) || {}).net_field || "net_amount";
    return (page.rows || []).reduce((sum, row) => sum + (Number(row[field]) || 0), 0);
  }

  function lastSeriesOnOrBefore(series, isoDate) {
    if (!series || !series.length) return null;
    if (!isoDate) return series[series.length - 1];
    let found = null;
    for (let i = 0; i < series.length; i += 1) {
      if (series[i].date <= isoDate) found = series[i];
      else break;
    }
    return found || series[0];
  }

  function lastChangeDate(series, isoDate, field) {
    const row = lastSeriesOnOrBefore(series, isoDate);
    if (!row) return null;
    let last = null;
    let seen = null;
    for (let i = 0; i < series.length; i += 1) {
      const cur = series[i];
      if (cur.date > row.date) break;
      if (field === "traded") {
        if (Number(cur.traded || 0) === 1) last = cur.date;
        continue;
      }
      const value = String(cur[field] || "");
      if (seen != null && value !== seen) last = cur.date;
      seen = value;
    }
    return last;
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

  function parseHash() {
    const raw = (location.hash || "#cyb").replace(/^#/, "");
    if (raw === "rotation") return { pageId: "rotation", key: "sector-rotation" };
    if (raw === "flow") return { pageId: "flow", key: "theme-flow" };
    if (raw === "cyb-committee") return { pageId: "cyb", key: "cyb-committee" };
    return { pageId: "cyb", key: "cyb-clock" };
  }

  function desiredHash() {
    if (state.pageId === "rotation") return "#rotation";
    if (state.pageId === "flow") return "#flow";
    if (state.key === "cyb-committee") return "#cyb-committee";
    return "#cyb";
  }

  function syncHash() {
    const next = desiredHash();
    if (location.hash !== next) history.replaceState(null, "", next);
  }

  function renderPages() {
    const nav = $("pages");
    nav.innerHTML = "";
    const pages = (state.catalog && state.catalog.pages) || [
      { id: "cyb", tab: "创业板指数策略" },
      { id: "rotation", tab: "板块轮动策略" },
      { id: "flow", tab: "板块资金流入" },
    ];
    pages.forEach((item) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = item.tab;
      btn.className = item.id === state.pageId ? "active" : "";
      btn.addEventListener("click", () => switchSection(item.id));
      nav.appendChild(btn);
    });
  }

  function renderTabs() {
    const nav = $("tabs");
    nav.innerHTML = "";
    if (state.pageId !== "cyb") {
      nav.hidden = true;
      return;
    }
    nav.hidden = false;
    state.catalog.indices.forEach((item) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = item.tab;
      btn.className = item.key === state.key ? "active" : "";
      btn.addEventListener("click", () => switchSection("cyb", item.key));
      nav.appendChild(btn);
    });
  }

  function setChrome() {
    const isCyb = state.pageId === "cyb";
    const isFlow = state.pageId === "flow";
    $("amv-open").hidden = !isCyb;
    $("footer-amv").hidden = !isCyb;
    $("strategy-view").hidden = isFlow;
    $("flow-view").hidden = !isFlow;
    $("mode-toggle").hidden = state.pageId !== "cyb";
    $("holdings-panel").hidden = isFlow;
    $("year-panel").hidden = isFlow;
    $("hero-point-label").textContent = isFlow
      ? flowWindowLabel(state.page, state.flowWindow) + "合计净流入"
      : "最新净值";
  }

  function renderCards(page) {
    if (state.pageId === "flow") {
      const totals = page.totals || {};
      const total = flowNet(page, state.flowWindow);
      const windowLabel = flowWindowLabel(page, state.flowWindow);
      const items = [
        { k: "数据日期", v: page.as_of || "—", c: "", s: "拉不到就沿用上次" },
        { k: windowLabel + "合计净流入", v: fmtYi(total), c: clsRet(total), s: page.unit || "亿元；板块会重叠" },
        { k: "覆盖板块", v: String(page.count || 0), c: "", s: "只看科技相关主题" },
        { k: "当日合计", v: fmtYi(totals["1d"] ?? page.total_net), c: clsRet(totals["1d"] ?? page.total_net), s: "即时净额" },
        { k: "3日合计", v: fmtYi(totals["3d"]), c: clsRet(totals["3d"]), s: "阶段累计" },
        { k: "5日合计", v: fmtYi(totals["5d"]), c: clsRet(totals["5d"]), s: "阶段累计" },
        { k: "10日合计", v: fmtYi(totals["10d"]), c: clsRet(totals["10d"]), s: "阶段累计" },
        { k: "20日合计", v: fmtYi(totals["20d"]), c: clsRet(totals["20d"]), s: "阶段累计" },
      ];
      $("metric-cards").innerHTML = items
        .map(
          (it) => `<article class="card"><div class="k">${it.k}</div><div class="v ${it.c}">${it.v}</div><div class="s">${it.s}</div></article>`
        )
        .join("");
      return;
    }
    const p = page.performance;
    const pos = page.position || {};
    const posClass = pos.units >= pos.n ? "up" : pos.units > 0 ? "" : "down";
    const items = [
      { k: "年化涨跌", v: fmtPct(p.annualized_return), c: clsRet(p.annualized_return), s: "成立以来" },
      { k: "最大回撤", v: fmtPct(p.max_drawdown.value), c: "down", s: `${p.max_drawdown.peak_date} → ${p.max_drawdown.trough_date}` },
      { k: "夏普比率", v: fmtNum(p.sharpe), c: "", s: "没扣无风险利率" },
      { k: "有仓天数", v: fmtPct(p.exposure, 1), c: "", s: "有仓交易日占比" },
      { k: "现在仓位", v: pos.label || "—", c: posClass, s: pos.note || "" },
      { k: "今年以来", v: fmtPct(p.ytd.return), c: clsRet(p.ytd.return), s: `${p.ytd.start_date} → ${p.ytd.end_date}` },
    ];
    if (state.pageId === "rotation" && p.vs_cyb_clock != null) {
      items.splice(5, 0, {
        k: "相对满仓",
        v: fmtPct(p.vs_cyb_clock),
        c: clsRet(p.vs_cyb_clock),
        s: "净值比创业板满仓多或少",
      });
    }
    $("metric-cards").innerHTML = items
      .map(
        (it) => `<article class="card"><div class="k">${it.k}</div><div class="v ${it.c}">${it.v}</div><div class="s">${it.s}</div></article>`
      )
      .join("");
  }

  function renderHero(page) {
    if (state.pageId === "flow") {
      $("hero-name").textContent = "科技主题资金流入";
      $("hero-sub").textContent = "资金快照，不是买卖建议。和上一页的 ETF 名单不是同一套口径。3日到20日是阶段累计。";
      $("latest-point").textContent = fmtYi(flowNet(page, state.flowWindow));
      $("point-asof").textContent = page.as_of ? `数据停在 ${page.as_of}` : "暂无数据";
      $("asof-chip").textContent = page.as_of ? `资金流 ${page.as_of}` : "资金流暂无";
      return;
    }
    const pos = page.position || {};
    const pillClass = pos.units >= pos.n ? "pos-on" : pos.units > 0 ? "pos-mid" : "pos-off";
    $("hero-name").innerHTML = `${page.index.index_name_cn} <span class="pos-pill ${pillClass}">${pos.label || ""}</span>`;
    $("hero-sub").textContent = page.index.headline || page.index.rule || "";
    $("latest-point").textContent = fmtNum(page.performance.latest_point, 2);
    $("point-asof").textContent = page.index.data_end_date;
    $("asof-chip").textContent = `策略有效日 ${page.index.data_end_date}`;
  }

  function renderOverlays(page) {
    const box = $("overlay-box");
    box.innerHTML = "";
    const selected = overlays();
    if (page.default_overlays && selected.size === 0) {
      page.default_overlays.forEach((key) => selected.add(key));
    }
    (page.overlays || []).forEach((ov) => {
      const lab = document.createElement("label");
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = selected.has(ov.key);
      cb.addEventListener("change", () => {
        if (cb.checked) selected.add(ov.key);
        else selected.delete(ov.key);
        drawChart();
      });
      lab.appendChild(cb);
      lab.appendChild(document.createTextNode(ov.code ? ` ${ov.name} ${ov.code}` : ` ${ov.name}`));
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
    if (state.pageId === "flow") return;
    const page = state.page;
    if (!page || !page.series) return;
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
    chart.subscribeClick((param) => {
      if (!param || param.time == null) return;
      const time = typeof param.time === "string" ? param.time : String(param.time);
      setHoldingsDate(time);
    });
    if (state.mode === "kline" && page.etf_ohlc && state.pageId === "cyb") {
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
      $("legend").textContent = "K 线是创业板 ETF（159915）前复权，红涨绿跌。只供观察，不是下单指令。";
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
    const names = [state.pageId === "rotation" ? "板块轮动" : "本页净值"];
    const selected = overlays();
    (page.overlays || []).forEach((ov) => {
      if (!selected.has(ov.key)) return;
      const rows = (page.overlay_series && page.overlay_series[ov.key]) || [];
      const line = chart.addLineSeries({
        color: OVERLAY_COLORS[ov.key] || "#94a3b8",
        lineWidth: 1,
      });
      line.setData(rebase(filterByPeriod(rows), "nav"));
      names.push(ov.name);
    });
    $("legend").textContent = "净值在当前窗口起点归一为 1000。对比线：" + names.join(" / ");
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
    if (!select) return;
    const pageId = state.pageId === "rotation" ? "rotation" : "cyb";
    let benchmark = state.benchmarkByPage[pageId] || page.default_benchmark;
    select.innerHTML = "";
    (page.overlays || []).forEach((ov) => {
      const opt = document.createElement("option");
      opt.value = ov.key;
      opt.textContent = ov.code ? `${ov.name} ${ov.code}` : ov.name;
      if (ov.key === benchmark) opt.selected = true;
      select.appendChild(opt);
    });
    if (!(page.overlays || []).some((ov) => ov.key === benchmark)) {
      benchmark = page.default_benchmark || ((page.overlays || [])[0] || {}).key;
      state.benchmarkByPage[pageId] = benchmark;
      select.value = benchmark || "";
    }
    const rows = (page.yearly_excess && page.yearly_excess[benchmark]) || [];
    $("year-body").innerHTML = rows
      .map((r) => {
        const year = r.partial ? r.year + "*" : String(r.year);
        return `<tr><td>${year}</td><td class="${clsRet(r.index_return)}">${fmtPct(r.index_return)}</td><td class="${clsRet(r.benchmark_return)}">${fmtPct(r.benchmark_return)}</td><td class="${clsRet(r.excess)}">${fmtPct(r.excess)}</td></tr>`;
      })
      .join("");
  }

  function setHoldingsDate(isoDate) {
    const page = state.page;
    if (!page || !page.series || !page.series.length) return;
    const row = lastSeriesOnOrBefore(page.series, isoDate);
    state.holdingsDate = row ? row.date : isoDate;
    const input = $("holdings-date");
    if (input && state.holdingsDate) input.value = state.holdingsDate;
    renderHoldings(page);
  }

  function etfMeta(page, code) {
    const uni = page.etf_universe || [];
    return uni.find((item) => item.code === code) || { code, name: code, theme: "", note: "" };
  }

  function renderHoldings(page) {
    const panel = $("holdings-panel");
    if (state.pageId === "flow") {
      panel.hidden = true;
      return;
    }
    panel.hidden = false;
    const series = page.series || [];
    const input = $("holdings-date");
    if (input && series.length) {
      input.min = series[0].date;
      input.max = series[series.length - 1].date;
      if (!state.holdingsDate) state.holdingsDate = series[series.length - 1].date;
      input.value = state.holdingsDate;
    }
    const row = lastSeriesOnOrBefore(series, state.holdingsDate);
    if (!row) {
      $("holdings-note").textContent = "这一天还没有记录。";
      $("holdings-body").innerHTML = `<tr><td colspan="4">没有持仓</td></tr>`;
      return;
    }
    const asOf = row.date;
    const latest = series.length ? series[series.length - 1].date : asOf;
    const isLatest = asOf === latest;
    if (page.page === "rotation" || (page.index && page.index.key === "sector-rotation")) {
      const codes = String(row.held || "").split(",").filter(Boolean);
      const last = lastChangeDate(series, asOf, "traded");
      const lastText = last ? `最近一次换仓 ${last}。` : "";
      if (!codes.length) {
        $("holdings-note").textContent =
          (isLatest ? "这一天空仓。" : `${asOf} 空仓。`) +
          (lastText || "创业板有仓才选板块。");
        $("holdings-body").innerHTML = `<tr><td colspan="4">没有持仓</td></tr>`;
        return;
      }
      const weight = 1 / codes.length;
      $("holdings-note").textContent =
        `${asOf} 拿着 ${codes.length} 只，等权。` + lastText + (isLatest ? "" : "点「回到最新」可看今天。");
      $("holdings-body").innerHTML = codes
        .map((code) => {
          const meta = etfMeta(page, code);
          return `<tr><td>${meta.name} ${code}</td><td>${meta.theme || "—"}</td><td>${fmtPct(weight, 0)}</td><td>${meta.note || "—"}</td></tr>`;
        })
        .join("");
      return;
    }
    const n = (page.position && page.position.n) || 5;
    const units = row.units != null ? Number(row.units) : Number(row.position || 0) * n;
    const weight = n ? units / n : Number(row.position || 0);
    const last = lastChangeDate(series, asOf, "units") || lastChangeDate(series, asOf, "position");
    const meta = page.holdings_meta || { code: "159915", name: "创业板ETF", theme: "创业板" };
    let label = "空仓";
    if (units <= 0) label = "空仓";
    else if (units >= n) label = "满仓";
    else label = `${Math.round(units * 10) / 10}/${n}`;
    let note = isLatest ? "按收盘估算。" : `这是 ${asOf} 收盘时的仓位。`;
    if (row.action === "schedule_entry") note = "当天收盘确认买入，第二天开盘才会成交。";
    if (row.action === "schedule_exit") note = "当天收盘确认卖出，第二天开盘才会成交。";
    if (last) note += ` 最近一次变仓 ${last}。`;
    $("holdings-note").textContent = `${asOf}：${label}。` + note;
    if (units <= 0) {
      $("holdings-body").innerHTML = `<tr><td colspan="4">没有持仓</td></tr>`;
      return;
    }
    $("holdings-body").innerHTML =
      `<tr><td>${meta.name} ${meta.code || "159915"}</td><td>${meta.theme || "创业板"}</td><td>${label}（${fmtPct(weight, 0)}）</td><td>${note}</td></tr>`;
  }

  function renderFlowWindowChips(page) {
    const box = $("flow-window-chips");
    if (!box) return;
    box.innerHTML = "";
    flowWindows(page).forEach((win) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = win.label;
      btn.className = win.id === state.flowWindow ? "active" : "";
      btn.addEventListener("click", () => {
        state.flowWindow = win.id;
        renderHero(page);
        renderCards(page);
        renderFlowWindowChips(page);
        renderFlow(page);
        setChrome();
      });
      box.appendChild(btn);
    });
  }

  function renderFlow(page) {
    const meta = flowWindowMeta(page, state.flowWindow);
    const field = (meta && meta.net_field) || "net_amount";
    const label = (meta && meta.label) || "当日";
    const rows = [...(page.rows || [])].sort(
      (a, b) => Math.abs(Number(b[field]) || 0) - Math.abs(Number(a[field]) || 0) || (Number(b[field]) || 0) - (Number(a[field]) || 0)
    );
    rows.sort((a, b) => (Number(b[field]) || 0) - (Number(a[field]) || 0));
    const title = $("flow-chart-title");
    if (title) title.textContent = label + "净流入排行";
    const maxAbs = Math.max(...rows.map((r) => Math.abs(Number(r[field]) || 0)), 0.01);
    $("flow-bars").innerHTML = rows
      .map((row) => {
        const net = Number(row[field]) || 0;
        const width = Math.max(2, (Math.abs(net) / maxAbs) * 100);
        const cls = net >= 0 ? "up" : "down";
        return `<div class="flow-bar-row"><div class="flow-bar-name">${row.board_name}</div><div class="flow-bar-track"><div class="flow-bar-fill ${cls}" style="width:${width}%"></div></div><div class="flow-bar-val ${cls}">${fmtYi(net)}</div></div>`;
      })
      .join("");
    const typeLabel = { concept: "概念", industry: "行业" };
    const yiCell = (value) => `<td class="${clsRet(value)}">${fmtYi(value)}</td>`;
    $("flow-body").innerHTML = rows
      .map((row) => {
        const type = typeLabel[row.board_type] || row.board_type || "";
        const leader = row.leader
          ? `${row.leader}${row.leader_pct_change != null ? " " + fmtNum(row.leader_pct_change, 2) + "%" : ""}`
          : "—";
        const pct = row.pct_change == null ? "—" : ((row.pct_change > 0 ? "+" : "") + fmtNum(row.pct_change, 2) + "%");
        return `<tr><td>${row.board_name}${type ? `（${type}）` : ""}</td><td>${row.theme || "—"}</td><td class="${clsRet(row.pct_change)}">${pct}</td>${yiCell(row.net_amount ?? row.net_1d)}${yiCell(row.net_3d)}${yiCell(row.net_5d)}${yiCell(row.net_10d)}${yiCell(row.net_20d)}<td class="${clsRet(row.leader_pct_change)}">${leader}</td></tr>`;
      })
      .join("");
  }

  function renderMethod(page) {
    const m = page.methodology || {};
    $("method-list").innerHTML = Object.entries(m)
      .map(([k, v]) => `<li><strong>${k}</strong>：${v}</li>`)
      .join("");
    $("risk-list").innerHTML = (page.risk_notes || []).map((x) => `<li>${x}</li>`).join("");
  }

  function agentUrl(pageId, key) {
    if (pageId === "rotation") return "data/agent/sector-rotation.json";
    if (pageId === "flow") return "data/agent/theme-fund-flow.json";
    const meta = state.catalog.indices.find((x) => x.key === key);
    return meta.agent_url;
  }

  async function switchSection(pageId, key) {
    state.pageId = pageId;
    if (pageId === "cyb") state.key = key || state.key || "cyb-clock";
    else if (pageId === "rotation") state.key = "sector-rotation";
    else state.key = "theme-flow";
    if (pageId !== "cyb") state.mode = "nav";
    const page = await loadJson(agentUrl(pageId, state.key));
    state.page = page;
    state.holdingsDate = (page.series && page.series.length) ? page.series[page.series.length - 1].date : null;
    if (page.default_window) state.flowWindow = page.default_window;
    if (page.default_overlays && pageId === "rotation") {
      const selected = overlays();
      if (![...selected].some((k) => (page.overlays || []).some((ov) => ov.key === k))) {
        state.overlaysByPage.rotation = new Set(page.default_overlays);
      }
    }
    syncHash();
    setChrome();
    renderPages();
    renderTabs();
    renderHero(page);
    renderCards(page);
    renderMethod(page);
    const catalogBanners = state.catalog.banners || [];
    if (pageId === "flow") {
      showBanners(page.banners || []);
      renderFlowWindowChips(page);
      renderFlow(page);
    } else {
      showBanners([...catalogBanners, ...(page.banners || [])]);
      renderOverlays(page);
      renderPeriodChips();
      renderYearTable(page);
      renderHoldings(page);
      drawChart();
    }
  }

  document.querySelectorAll(".mode-toggle button").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.mode = btn.dataset.mode;
      document.querySelectorAll(".mode-toggle button").forEach((b) => b.classList.toggle("active", b === btn));
      drawChart();
    });
  });
  $("bench-select").addEventListener("change", (e) => {
    const pageId = state.pageId === "rotation" ? "rotation" : "cyb";
    state.benchmarkByPage[pageId] = e.target.value;
    renderYearTable(state.page);
  });
  $("holdings-date").addEventListener("change", (e) => {
    setHoldingsDate(e.target.value);
  });
  $("holdings-latest").addEventListener("click", () => {
    const series = (state.page && state.page.series) || [];
    if (series.length) setHoldingsDate(series[series.length - 1].date);
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
          "。提交后还要在 GitHub 再点一次「提交新 issue」。"
        : "提交后还要在 GitHub 再点一次「提交新 issue」。";
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
            "系统会记下，但不当新信号，仓位和净值都不会变。\n仍要提交吗？"
        );
        if (!ok) return;
      }
      const repo = (state.catalog && state.catalog.github_repo) || "CindyNNN/amv-research-clock";
      const title = `0AMV ${date}`;
      const body = [
        `date: ${date}`,
        `close: ${close}`,
        "",
        "请用仓库所有者账号直接点「提交新 issue」，不要改格式。",
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
        ? "已打开 GitHub。请在新标签页点绿色「提交新 issue」，大约一两分钟后回来刷新本页。"
        : "弹窗被拦截。请点下面「弹窗被拦截时点这里」，再在 GitHub 点「提交新 issue」。";
    });
  }

  window.addEventListener("resize", fitChart);
  window.addEventListener("hashchange", () => {
    const next = parseHash();
    if (next.pageId === state.pageId && next.key === state.key) return;
    switchSection(next.pageId, next.key);
  });

  tickClock();
  setInterval(tickClock, 1000);
  bindAmvDialog();

  loadJson("data/agent/catalog.json")
    .then((catalog) => {
      state.catalog = catalog;
      document.title = catalog.site_name || document.title;
      const start = parseHash();
      return switchSection(start.pageId, start.key);
    })
    .catch((err) => {
      showBanners([
        "站点数据还没生成。请用本地网页打开：python -m http.server 8080 --directory site",
        String(err),
      ]);
      console.error(err);
    });
})();
