const EXCHANGE_LABELS = { okx: 'OKX', binance: 'Binance', hyperliquid: 'Hyperliquid' }

const state = { exchange: 'binance', asset: 'XAU' }
let fundingChart = null
let priceChart = null
let heatmapChart = null
let liveData = null
let lastLiveAsset = null

function fmtPct(v, digits = 2) {
  if (v === null || v === undefined) return '—'
  const sign = v > 0 ? '+' : ''
  return `${sign}${v.toFixed(digits)}%`
}

function signClass(v) {
  if (v > 0) return 'pos'
  if (v < 0) return 'neg'
  return 'neutral'
}

function selectExchange(ex) {
  state.exchange = ex
  document.querySelectorAll('#exchange-picker .pill').forEach(b => b.classList.toggle('active', b.dataset.exchange === ex))
  load()
}

function selectAsset(asset) {
  state.asset = asset
  document.querySelectorAll('#asset-picker .pill').forEach(b => b.classList.toggle('active', b.dataset.asset === asset))
  load()
}

function setInitialSelection(ex, asset) {
  state.exchange = ex
  state.asset = asset
  document.querySelectorAll('#exchange-picker .pill').forEach(b => b.classList.toggle('active', b.dataset.exchange === ex))
  document.querySelectorAll('#asset-picker .pill').forEach(b => b.classList.toggle('active', b.dataset.asset === asset))
  load()
}

async function load() {
  const { exchange, asset } = state
  const loading = document.getElementById('a-loading')
  const card = document.getElementById('a-card')
  loading.style.display = 'block'
  loading.textContent = '⏳ Загружаю...'
  card.style.display = 'none'

  try {
    const r = await fetch(`/api/analysis/${exchange}/${asset}`)
    const data = await r.json()
    if (!data.ok) {
      loading.textContent = '❌ ' + (data.error || 'неизвестная ошибка')
      return
    }
    if (data.message) {
      loading.textContent = 'ℹ️ ' + data.message
      return
    }
    loading.style.display = 'none'
    card.style.display = 'block'
    render(data)
    if (asset !== lastLiveAsset) {
      loadLive(asset)
    } else {
      renderLive()
    }
  } catch (e) {
    loading.textContent = '❌ Ошибка сети: ' + e.message
  }
}

async function loadLive(asset) {
  try {
    const r = await fetch(`/api/live/${asset}`)
    const data = await r.json()
    if (data.ok) {
      liveData = data
      lastLiveAsset = asset
    }
  } catch (e) {
    // Живой виджет — не критичная часть экрана, молча пропускаем сетевой сбой.
  }
  renderLive()
}

function renderLive() {
  const tag = document.getElementById('a-live-tag')
  if (!liveData || lastLiveAsset !== state.asset) {
    tag.style.display = 'none'
    return
  }
  const live = liveData.exchanges[state.exchange]
  if (!live) {
    tag.style.display = 'none'
    return
  }
  tag.style.display = 'inline-block'
  const rateEl = document.getElementById('a-live-rate')
  rateEl.textContent = fmtPct(live.funding_rate_pct, 4)
  rateEl.className = 'num ' + signClass(live.funding_rate_pct)
  const basisEl = document.getElementById('a-live-basis')
  basisEl.textContent = live.basis_pct === null ? '—' : fmtPct(live.basis_pct, 3)
  basisEl.className = 'num ' + signClass(live.basis_pct)
  updateCountdown()
}

function updateCountdown() {
  const el = document.getElementById('a-live-countdown')
  if (!liveData || lastLiveAsset !== state.asset) return
  const live = liveData.exchanges[state.exchange]
  if (!live || !live.next_funding_time) {
    el.textContent = '—'
    return
  }
  const diff = live.next_funding_time - Date.now()
  if (diff <= 0) {
    el.textContent = '00:00:00'
    return
  }
  const h = Math.floor(diff / 3600000)
  const m = Math.floor((diff % 3600000) / 60000)
  const s = Math.floor((diff % 60000) / 1000)
  el.textContent = `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

function render(data) {
  // Отключаем от группы синхронизации перед полной перерисовкой (setOption
  // notMerge=true) — иначе ECharts может обратиться к устаревшему состоянию
  // другого графика во время обновления и упасть с "reading 'coord'".
  if (fundingChart) fundingChart.group = ''
  if (priceChart) priceChart.group = ''

  renderHeader(data)
  renderHero(data.stats)
  renderStatStrip(data.stats, data.cross_exchange)
  renderMonthlyTable(data.monthly)
  renderFundingChart(data.funding_series)
  renderPriceChart(data.price_series)
  renderHeatmapChart(data.heatmap)

  if (fundingChart && priceChart && data.price_series.length) {
    fundingChart.group = 'analysis-sync'
    priceChart.group = 'analysis-sync'
    echarts.connect('analysis-sync')
  }
}

function renderHeader(data) {
  const badge = document.getElementById('a-badge-exchange')
  badge.textContent = EXCHANGE_LABELS[data.exchange] || data.exchange
  badge.className = 'badge ' + (data.exchange === 'binance' ? 'bnb' : data.exchange === 'okx' ? 'okx' : 'hl')
  document.getElementById('a-asset-label').textContent = ASSET_LABELS[data.asset] || data.asset
  document.getElementById('a-interval').textContent = data.stats.funding_interval
}

function renderHero(stats) {
  const el = document.getElementById('a-hero-value')
  const v = stats.annualized_yield_pct
  el.textContent = fmtPct(v)
  el.className = 'a-hero-value num ' + signClass(v)
  document.getElementById('a-hero-sub').textContent =
    `Средняя ставка за период: ${fmtPct(stats.avg_rate_pct, 6)} · ${stats.periods} периодов · ${stats.date_start.slice(0, 10)} → ${stats.date_end.slice(0, 10)}`
}

function renderStatStrip(stats, crossExchange) {
  const entries = Object.entries(crossExchange).map(([ex, st]) => ({ ex, y: st.annualized_yield_pct }))
  const spread = entries.length > 1 ? Math.max(...entries.map(e => e.y)) - Math.min(...entries.map(e => e.y)) : 0
  const spreadDetail = entries.map(e => `${EXCHANGE_LABELS[e.ex]} ${fmtPct(e.y, 1)}`).join(' · ')

  const corr = stats.correlation_price
  const cards = [
    { label: 'Накопленная доходность', value: fmtPct(stats.cumulative_return_pct), sign: stats.cumulative_return_pct },
    { label: 'Волатильность ставки', value: `${stats.volatility_pct.toFixed(4)}%`, sign: 0 },
    { label: 'Спред между биржами', value: `${spread.toFixed(2)} п.п.`, sign: 0, detail: spreadDetail },
    { label: '% положительных периодов', value: `${stats.pct_positive}%`, sign: stats.pct_positive - 50 },
    { label: 'Корреляция с ценой', value: corr === null ? 'н/д' : corr.toFixed(2), sign: corr || 0 },
  ]

  document.getElementById('a-stat-strip').innerHTML = cards.map(c => `
    <div class="stat-card">
      <div class="stat-label">${c.label}</div>
      <div class="stat-value ${signClass(c.sign)} num">${c.value}</div>
      ${c.detail ? `<div class="stat-detail">${c.detail}</div>` : ''}
    </div>
  `).join('')
}

function renderMonthlyTable(monthly) {
  const monthNames = ['Янв', 'Фев', 'Мар', 'Апр', 'Май', 'Июн', 'Июл', 'Авг', 'Сен', 'Окт', 'Ноя', 'Дек']
  const years = {}
  monthly.forEach(m => {
    const [y, mo] = m.month.split('-')
    if (!years[y]) years[y] = Array(12).fill(null)
    years[y][parseInt(mo, 10) - 1] = m.return_pct
  })

  let html = '<thead><tr><th>Год</th>' + monthNames.map(n => `<th>${n}</th>`).join('') + '<th>Итог</th></tr></thead><tbody>'
  Object.keys(years).sort().forEach(y => {
    const row = years[y]
    const total = row.reduce((a, b) => a + (b || 0), 0)
    html += `<tr><td class="row-year">${y}</td>`
    html += row.map(v => v === null
      ? '<td class="num cell-empty">—</td>'
      : `<td class="num ${signClass(v)}-cell">${fmtPct(v)}</td>`
    ).join('')
    html += `<td class="num total-cell ${signClass(total)}-cell">${fmtPct(total)}</td></tr>`
  })
  html += '</tbody>'
  document.getElementById('monthly-table').innerHTML = html
}

function baseAxisStyle() {
  return {
    xAxis: {
      type: 'time',
      axisLine: { lineStyle: { color: '#30363d' } },
      axisLabel: { color: '#6e7681' },
      splitLine: { show: false },
    },
    yAxis: {
      type: 'value',
      scale: true,
      axisLabel: { color: '#6e7681' },
      splitLine: { lineStyle: { color: 'rgba(240,246,252,.06)' } },
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross', label: { backgroundColor: '#21262d', color: '#e6edf3' } },
      backgroundColor: '#161b22', borderColor: '#30363d', textStyle: { color: '#e6edf3' },
    },
  }
}

// ECharts' visualMap (piecewise, по знаку значения) ломается с "Cannot read
// properties of undefined (reading 'coord')" на line-серии независимо от
// версии библиотеки (проверено на 5.5.1 и 5.6.0, минимальный репродюсер).
// Вместо visualMap красим линию/заливку линейным градиентом с точкой
// перехода ровно на нуле — управляем диапазоном оси Y явно, чтобы точка
// перехода в градиенте совпадала с нулевой линией на графике.
function divergingGradient(min, max, colorAbove, colorBelow) {
  if (max <= 0) return colorBelow
  if (min >= 0) return colorAbove
  const stop = Math.min(Math.max(max / (max - min), 0.02), 0.98)
  return {
    type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
    colorStops: [
      { offset: 0, color: colorAbove },
      { offset: stop, color: colorAbove },
      { offset: stop, color: colorBelow },
      { offset: 1, color: colorBelow },
    ],
  }
}

function fundingChartOption(series) {
  const data = series.map(p => [p.ts, p.rate_pct])
  const base = baseAxisStyle()
  const rates = series.map(p => p.rate_pct)
  const dataMax = Math.max(...rates, 0)
  const dataMin = Math.min(...rates, 0)
  const pad = (dataMax - dataMin) * 0.1 || 1
  const axisMax = dataMax + pad
  const axisMin = dataMin - pad
  const lineColor = divergingGradient(axisMin, axisMax, '#3fb950', '#f85149')
  const areaColor = divergingGradient(axisMin, axisMax, 'rgba(63,185,80,.2)', 'rgba(248,81,73,.2)')

  return {
    backgroundColor: 'transparent',
    animation: false,
    grid: { left: 55, right: 20, top: 20, bottom: 45 },
    tooltip: { ...base.tooltip, valueFormatter: v => v.toFixed(5) + '%' },
    xAxis: base.xAxis,
    yAxis: {
      ...base.yAxis, scale: false, min: axisMin, max: axisMax,
      axisLabel: {
        ...base.yAxis.axisLabel, formatter: v => v.toFixed(2) + '%',
        showMinLabel: false, showMaxLabel: false,
      },
    },
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 16, bottom: 8, borderColor: '#30363d', fillerColor: 'rgba(88,166,255,.12)' }],
    series: [{
      type: 'line', data, showSymbol: false, lineStyle: { width: 1.3, color: lineColor },
      areaStyle: { color: areaColor },
      progressive: 0,
      markLine: {
        symbol: 'none', silent: true, animation: false,
        lineStyle: { color: '#484f58', type: 'dashed' },
        data: [{ yAxis: 0 }], label: { show: false },
      },
    }],
  }
}

function renderFundingChart(series) {
  const el = document.getElementById('funding-chart')
  if (!fundingChart) fundingChart = echarts.init(el, null, { renderer: 'canvas' })
  fundingChart.clear()
  fundingChart.setOption(fundingChartOption(series))
}

function priceChartOption(series) {
  const data = series.map(p => [p.ts, p.close])
  const base = baseAxisStyle()
  return {
    backgroundColor: 'transparent',
    animation: false,
    grid: { left: 55, right: 20, top: 20, bottom: 20 },
    tooltip: base.tooltip,
    xAxis: base.xAxis,
    yAxis: base.yAxis,
    dataZoom: [{ type: 'inside' }],
    series: [{
      type: 'line', data, showSymbol: false,
      lineStyle: { width: 1.3, color: '#d29922' },
      itemStyle: { color: '#d29922' },
      areaStyle: {
        color: {
          type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: 'rgba(210,153,34,.25)' },
            { offset: 1, color: 'rgba(210,153,34,0)' },
          ],
        },
      },
    }],
  }
}

function renderPriceChart(series) {
  const el = document.getElementById('price-chart')
  const empty = document.getElementById('price-chart-empty')
  if (!priceChart) priceChart = echarts.init(el, null, { renderer: 'canvas' })
  if (!series.length) {
    // Отключаем от группы синхронизации, пока график скрыт — иначе connect()
    // пытается синхронизировать курсор со свёрнутым (display:none) графиком.
    priceChart.group = ''
    priceChart.clear()
    el.style.display = 'none'
    empty.style.display = 'block'
    return
  }
  el.style.display = 'block'
  empty.style.display = 'none'
  priceChart.clear()
  priceChart.setOption(priceChartOption(series))
}

const HEATMAP_DAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']

function heatmapChartOption(heatmap) {
  const hours = [...new Set(heatmap.map(h => h.hour))].sort((a, b) => a - b)
  const data = heatmap.map(h => [hours.indexOf(h.hour), h.day, h.avg_rate_pct])
  const values = heatmap.map(h => h.avg_rate_pct)
  const maxAbs = Math.max(Math.abs(Math.min(...values, 0)), Math.abs(Math.max(...values, 0))) || 1
  return {
    backgroundColor: 'transparent',
    animation: false,
    grid: { left: 45, right: 20, top: 10, bottom: 20 },
    tooltip: {
      backgroundColor: '#161b22', borderColor: '#30363d', textStyle: { color: '#e6edf3' },
      formatter: p => `${HEATMAP_DAYS[p.value[1]]}, ${hours[p.value[0]]}:00 UTC<br>${fmtPct(p.value[2], 5)}`,
    },
    xAxis: {
      type: 'category', data: hours.map(h => `${h}:00`),
      axisLine: { lineStyle: { color: '#30363d' } }, axisLabel: { color: '#6e7681' }, splitArea: { show: false },
    },
    yAxis: {
      type: 'category', data: HEATMAP_DAYS,
      axisLine: { lineStyle: { color: '#30363d' } }, axisLabel: { color: '#6e7681' }, splitArea: { show: false },
    },
    visualMap: {
      type: 'continuous', show: false, min: -maxAbs, max: maxAbs,
      inRange: { color: ['#f85149', '#21262d', '#3fb950'] },
    },
    series: [{
      type: 'heatmap', data, itemStyle: { borderColor: '#0d1117', borderWidth: 2 },
    }],
  }
}

function renderHeatmapChart(heatmap) {
  const el = document.getElementById('heatmap-chart')
  if (!heatmapChart) heatmapChart = echarts.init(el, null, { renderer: 'canvas' })
  if (!heatmap.length) {
    heatmapChart.clear()
    return
  }
  heatmapChart.clear()
  heatmapChart.setOption(heatmapChartOption(heatmap))
}

function applyRange(range) {
  if (!fundingChart) return
  const now = Date.now()
  const rangeDays = { '1M': 30, '3M': 90, '6M': 182, '1Y': 365 }
  const action = range === 'ALL'
    ? { type: 'dataZoom', start: 0, end: 100 }
    : { type: 'dataZoom', startValue: now - rangeDays[range] * 86400000, endValue: now }
  // Диспатчим напрямую на оба графика (а не через слушатель события) —
  // иначе взаимная подписка на 'datazoom' между связанными графиками
  // уходит в рекурсию (Maximum call stack size exceeded).
  fundingChart.dispatchAction(action)
  if (priceChart) priceChart.dispatchAction(action)
}

document.querySelectorAll('#exchange-picker .pill').forEach(btn => {
  btn.addEventListener('click', () => selectExchange(btn.dataset.exchange))
})
document.querySelectorAll('#asset-picker .pill').forEach(btn => {
  btn.addEventListener('click', () => selectAsset(btn.dataset.asset))
})
document.querySelectorAll('#a-range-buttons button').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('#a-range-buttons button').forEach(b => b.classList.remove('active'))
    btn.classList.add('active')
    applyRange(btn.dataset.range)
  })
})
window.addEventListener('resize', () => {
  if (fundingChart) fundingChart.resize()
  if (priceChart) priceChart.resize()
  if (heatmapChart) heatmapChart.resize()
})

setInterval(updateCountdown, 1000)
setInterval(() => loadLive(state.asset), 30000)

setInitialSelection(state.exchange, state.asset)
