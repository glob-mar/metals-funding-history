const EXCHANGE_LABELS = { okx: 'OKX', binance: 'Binance', hyperliquid: 'Hyperliquid' }

const state = { exchange: 'binance', asset: 'XAU', priceChartType: 'line' }
let currentPriceSeries = []
let fundingChart = null
let priceChart = null
let pnlChart = null
let liveData = null
let lastLiveAsset = null
let currentFundingSeries = []
const pnlState = {
  deposit: 10000, leverage: 1, twoLegs: true, reinvestPct: 0,
  side: 'short', feeType: 'taker', feePct: 0.05,
  startDate: null, rebalanceThresholdPct: 50,
}

// null = считаем с самого начала собранной истории. Диапазон дат в поле
// «Считать с» сбрасывается на полную историю при каждой смене биржи/актива —
// разные пары не гарантированно имеют одинаковый охват дат.
function syncPnlDateRange(series) {
  const input = document.getElementById('pnl-start-date')
  pnlState.startDate = null
  if (!series.length) { input.value = ''; input.min = ''; input.max = ''; return }
  const minDate = new Date(series[0].ts).toISOString().slice(0, 10)
  const maxDate = new Date(series[series.length - 1].ts).toISOString().slice(0, 10)
  input.min = minDate
  input.max = maxDate
  input.value = minDate
}

function filterFromDate(series, startDate) {
  if (!startDate) return series
  const startMs = new Date(startDate + 'T00:00:00Z').getTime()
  return series.filter(p => p.ts >= startMs)
}
// Ориентировочные ставки round-trip комиссии по обычному (не VIP) тарифу —
// могут отличаться от факта и меняться биржами со временем, поэтому дают
// только стартовую точку, поле «Комиссия за сделку» всегда редактируемо.
const DEFAULT_FEES_PCT = {
  binance:     { maker: 0.02, taker: 0.05 },
  okx:         { maker: 0.02, taker: 0.05 },
  hyperliquid: { maker: 0.01, taker: 0.035 },
}

function syncFeePreset() {
  if (pnlState.feeType === 'custom') return
  pnlState.feePct = DEFAULT_FEES_PCT[state.exchange][pnlState.feeType]
  document.getElementById('pnl-fee-pct').value = pnlState.feePct
}

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
  renderStatStrip(data.stats)
  renderMonthlyTable(data.monthly)
  renderFundingChart(data.funding_series)
  currentFundingSeries = data.funding_series
  currentPriceSeries = data.price_series
  syncFeePreset()
  syncPnlDateRange(data.funding_series)
  renderPnlChart()
  renderPriceChart(data.price_series)

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

const CORRELATION_TIP = 'Показывает, связаны ли между собой ставка фандинга и цена актива. Число от −1 до +1: ближе к +1 — когда цена растёт, ставка тоже растёт (и наоборот); ближе к −1 — двигаются в разные стороны; около 0 — зависимости нет. Если данных мало (меньше 3 общих дней), показываем «н/д».'

function renderStatStrip(stats) {
  const corr = stats.correlation_price
  const cards = [
    { label: 'Накопленная доходность', value: fmtPct(stats.cumulative_return_pct), sign: stats.cumulative_return_pct },
    { label: 'Волатильность ставки', value: `${stats.volatility_pct.toFixed(4)}%`, sign: 0 },
    { label: '% положительных периодов', value: `${stats.pct_positive}%`, sign: stats.pct_positive - 50 },
    { label: 'Корреляция с ценой', value: corr === null ? 'н/д' : corr.toFixed(2), sign: corr || 0, tip: CORRELATION_TIP },
  ]

  document.getElementById('a-stat-strip').innerHTML = cards.map(c => `
    <div class="stat-card">
      <div class="stat-label">${c.label}${c.tip ? `<span class="info-tip" data-tip="${c.tip}">ⓘ</span>` : ''}</div>
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

// Конвенция знака funding rate — стандартная для перпетуалов на всех трёх
// биржах: положительная ставка = лонги платят шортам. Поэтому шорт получает
// +notional*rate за период, лонг — ровно наоборот.
//
// «2 ноги» — реальный сетап пользователя: фандинг-арбитраж, вторая
// (хеджирующая) нога стоит на форексе вне этого дашборда. Депозит делится
// пополам между ногами, поэтому ноционал, реально зарабатывающий фандинг,
// вдвое меньше депозита. Плечо умножает этот ноционал дальше.
//
// Реинвест — простое сложное начисление: доля reinvestPct% прибыли/убытка
// каждого периода добавляется обратно к капиталу (и дальше сама участвует
// в расчёте ноционала следующих периодов), остальное откладывается «в
// кэш» без начисления процента на процент. reinvestPct=0 — старое линейное
// поведение (капитал не меняется, это и есть режим по умолчанию).
function computeSimulation(series, { deposit, leverage, twoLegs, reinvestPct, side }) {
  const sign = side === 'short' ? 1 : -1
  const legFactor = twoLegs ? 0.5 : 1
  let capital = deposit
  let cashOut = 0
  return series.map(p => {
    const notional = capital * legFactor * leverage
    const periodPnl = sign * notional * (p.rate_pct / 100)
    const reinvested = periodPnl * (reinvestPct / 100)
    capital += reinvested
    cashOut += periodPnl - reinvested
    return [p.ts, capital + cashOut - deposit]
  })
}

function startingNotional() {
  return pnlState.deposit * (pnlState.twoLegs ? 0.5 : 1) * pnlState.leverage
}

// Та же логика по периодам, что и computeSimulation (капитал так же растёт от
// реинвеста от месяца к месяцу), но вместо одной итоговой кривой возвращает
// сумму P&L отдельно по каждому календарному месяцу (UTC) — до комиссии,
// комиссия разовая и по месяцам не размазывается.
function computeMonthlyPnl(series, { deposit, leverage, twoLegs, reinvestPct, side }) {
  const sign = side === 'short' ? 1 : -1
  const legFactor = twoLegs ? 0.5 : 1
  let capital = deposit
  const monthly = {}
  for (const p of series) {
    const notional = capital * legFactor * leverage
    const periodPnl = sign * notional * (p.rate_pct / 100)
    capital += periodPnl * (reinvestPct / 100)
    const d = new Date(p.ts)
    const key = `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`
    monthly[key] = (monthly[key] || 0) + periodPnl
  }
  return monthly
}

const MONTH_NAMES_SHORT = { '01': 'Янв', '02': 'Фев', '03': 'Мар', '04': 'Апр', '05': 'Май', '06': 'Июн', '07': 'Июл', '08': 'Авг', '09': 'Сен', '10': 'Окт', '11': 'Ноя', '12': 'Дек' }

function renderPnlMonthlyTable(monthlyPnl) {
  const el = document.getElementById('pnl-monthly-table')
  const keys = Object.keys(monthlyPnl).sort()
  if (!keys.length) { el.innerHTML = ''; return }
  let total = 0
  const rows = keys.map(k => {
    const [y, m] = k.split('-')
    const v = monthlyPnl[k]
    total += v
    return `<tr><td>${MONTH_NAMES_SHORT[m]} ${y}</td><td class="num ${signClass(v)}">${fmtMoney(v)}</td></tr>`
  }).join('')
  el.innerHTML = `<thead><tr><th>Месяц</th><th>P&L (до комиссии)</th></tr></thead><tbody>${rows}` +
    `<tr class="pnl-monthly-total"><td>Итого</td><td class="num ${signClass(total)}">${fmtMoney(total)}</td></tr></tbody>`
}

function pnlChartOption(series) {
  const data = computeSimulation(series, pnlState)
  const base = baseAxisStyle()
  const values = data.map(d => d[1])
  const dataMax = Math.max(...values, 0)
  const dataMin = Math.min(...values, 0)
  const pad = (dataMax - dataMin) * 0.1 || 1
  const axisMax = dataMax + pad
  const axisMin = dataMin - pad
  const lineColor = divergingGradient(axisMin, axisMax, '#3fb950', '#f85149')
  const areaColor = divergingGradient(axisMin, axisMax, 'rgba(63,185,80,.2)', 'rgba(248,81,73,.2)')

  return {
    backgroundColor: 'transparent',
    animation: false,
    grid: { left: 65, right: 20, top: 20, bottom: 20 },
    tooltip: { ...base.tooltip, valueFormatter: v => '$' + v.toFixed(2) },
    xAxis: base.xAxis,
    yAxis: {
      ...base.yAxis, scale: false, min: axisMin, max: axisMax,
      axisLabel: {
        ...base.yAxis.axisLabel, formatter: v => '$' + v.toFixed(0),
        showMinLabel: false, showMaxLabel: false,
      },
    },
    dataZoom: [{ type: 'inside' }],
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

function fmtMoney(v) {
  const sign = v >= 0 ? '+' : '−'
  return `${sign}$${Math.abs(v).toFixed(2)}`
}

// Оценка «сколько раз пришлось бы перекинуть маржу между ногами». Модель:
// капитал поровну на двух ногах (эта биржа + хедж на форексе вне дашборда),
// цена одна и та же для обеих (считаем хедж идеальным, без базисного риска) —
// значит одна нога зарабатывает на движении цены ровно то, что теряет другая.
// Как только просевшая нога падает ниже порога от своей стартовой доли, это
// точка, где в реальности потребовался бы перевод — после него обе ноги
// условно снова выравниваются 50/50, и отсчёт от текущей цены идёт заново.
// Не учитывает реинвест (Реинвест) — совмещать растущий от реинвеста
// ноционал с этой моделью маржи было бы отдельной, гораздо более сложной
// задачей, не нужной для оценки порядка величины.
function computeRebalances(priceSeries, { deposit, leverage, side, thresholdPct }) {
  if (!priceSeries.length) return 0
  const sign = side === 'short' ? -1 : 1
  const legCapital = deposit / 2
  const notional = legCapital * leverage
  const threshold = legCapital * (thresholdPct / 100)
  let entryPrice = priceSeries[0].close
  let count = 0
  for (const p of priceSeries) {
    const changePct = (p.close - entryPrice) / entryPrice
    const pnl = notional * changePct * sign
    if (legCapital + pnl < threshold || legCapital - pnl < threshold) {
      count++
      entryPrice = p.close
    }
  }
  return count
}

function renderPnlSummary() {
  const el = document.getElementById('pnl-summary')
  const periodEl = document.getElementById('pnl-period-note')
  const series = filterFromDate(currentFundingSeries, pnlState.startDate)
  if (!series.length) {
    el.innerHTML = ''
    periodEl.textContent = ''
    renderPnlMonthlyTable({})
    return
  }

  const start = new Date(series[0].ts).toISOString().slice(0, 10)
  const end = new Date(series[series.length - 1].ts).toISOString().slice(0, 10)
  periodEl.textContent = `Период расчёта: ${start} → ${end} (${series.length} периодов фандинга)`

  const data = computeSimulation(series, pnlState)
  const gross = data[data.length - 1][1]
  const notional = startingNotional()
  const feeCost = notional * (pnlState.feePct / 100) * 2
  const net = gross - feeCost

  const cards = [
    { label: 'Стартовый ноционал', value: `$${notional.toLocaleString('ru-RU', { maximumFractionDigits: 0 })}`, sign: 0 },
    { label: 'Фандинг (до комиссии)', value: fmtMoney(gross), sign: gross },
    { label: 'Комиссия вход+выход', value: `−$${feeCost.toFixed(2)}`, sign: 0 },
    { label: 'Чистый P&L', value: fmtMoney(net), sign: net },
  ]

  if (pnlState.twoLegs) {
    const priceSeries = filterFromDate(currentPriceSeries, pnlState.startDate)
    const rebalances = computeRebalances(priceSeries, {
      deposit: pnlState.deposit, leverage: pnlState.leverage, side: pnlState.side,
      thresholdPct: pnlState.rebalanceThresholdPct,
    })
    cards.push({
      label: 'Переводов между ногами',
      value: priceSeries.length ? String(rebalances) : 'нет цены',
      sign: 0,
      detail: `порог ${pnlState.rebalanceThresholdPct}% от ноги`,
    })
  }

  el.innerHTML = cards.map(c => `
    <div class="stat-card">
      <div class="stat-label">${c.label}</div>
      <div class="stat-value ${signClass(c.sign)} num">${c.value}</div>
      ${c.detail ? `<div class="stat-detail">${c.detail}</div>` : ''}
    </div>
  `).join('')

  renderPnlMonthlyTable(computeMonthlyPnl(series, pnlState))
}

function renderPnlChart() {
  const el = document.getElementById('pnl-chart')
  if (!pnlChart) pnlChart = echarts.init(el, null, { renderer: 'canvas' })
  const series = filterFromDate(currentFundingSeries, pnlState.startDate)
  if (!series.length) {
    pnlChart.clear()
    renderPnlSummary()
    return
  }
  pnlChart.clear()
  pnlChart.setOption(pnlChartOption(series))
  renderPnlSummary()
}

function lineChartOption(series, base) {
  const data = series.map(p => [p.ts, p.close])
  return {
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

// ECharts candlestick с value/time-осью ждёt данные как [x, open, close, low, high]
// (именно в этом порядке — open/close раньше low/high, не путать с OHLC).
function candlestickChartOption(series, base) {
  const data = series.map(p => [p.ts, p.open, p.close, p.low, p.high])
  return {
    grid: { left: 55, right: 20, top: 20, bottom: 20 },
    tooltip: {
      ...base.tooltip,
      formatter: params => {
        const p = params[0]
        const [, o, c, l, h] = p.value
        return `${new Date(p.value[0]).toLocaleString('ru-RU')}<br>` +
          `Откр: ${o}<br>Закр: ${c}<br>Мин: ${l}<br>Макс: ${h}`
      },
    },
    xAxis: base.xAxis,
    yAxis: base.yAxis,
    dataZoom: [{ type: 'inside' }],
    series: [{
      type: 'candlestick', data,
      itemStyle: {
        color: '#3fb950', color0: '#f85149',
        borderColor: '#3fb950', borderColor0: '#f85149',
      },
    }],
  }
}

function priceChartOption(series) {
  const base = baseAxisStyle()
  const common = { backgroundColor: 'transparent', animation: false }
  return state.priceChartType === 'candlestick'
    ? { ...common, ...candlestickChartOption(series, base) }
    : { ...common, ...lineChartOption(series, base) }
}

function renderPriceChart(series) {
  const el = document.getElementById('price-chart')
  const empty = document.getElementById('price-chart-empty')
  currentPriceSeries = series
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
document.querySelectorAll('#price-type-buttons button').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('#price-type-buttons button').forEach(b => b.classList.remove('active'))
    btn.classList.add('active')
    state.priceChartType = btn.dataset.type
    if (currentPriceSeries.length) renderPriceChart(currentPriceSeries)
  })
})
document.querySelectorAll('#pnl-side-picker .pill').forEach(btn => {
  btn.addEventListener('click', () => {
    pnlState.side = btn.dataset.side
    document.querySelectorAll('#pnl-side-picker .pill').forEach(b => b.classList.toggle('active', b === btn))
    renderPnlChart()
  })
})
document.getElementById('pnl-deposit').addEventListener('input', (e) => {
  const v = parseFloat(e.target.value)
  if (!isFinite(v) || v <= 0) return
  pnlState.deposit = v
  renderPnlChart()
})
document.getElementById('pnl-leverage').addEventListener('input', (e) => {
  const v = parseFloat(e.target.value)
  if (!isFinite(v) || v <= 0) return
  pnlState.leverage = v
  renderPnlChart()
})
document.getElementById('pnl-two-legs').addEventListener('change', (e) => {
  pnlState.twoLegs = e.target.checked
  document.getElementById('pnl-threshold-wrap').style.display = pnlState.twoLegs ? '' : 'none'
  renderPnlChart()
})
document.getElementById('pnl-rebalance-threshold').addEventListener('input', (e) => {
  const v = parseFloat(e.target.value)
  if (!isFinite(v) || v <= 0 || v >= 100) return
  pnlState.rebalanceThresholdPct = v
  renderPnlChart()
})
document.getElementById('pnl-reinvest-pct').addEventListener('input', (e) => {
  const v = parseFloat(e.target.value)
  if (!isFinite(v) || v < 0 || v > 100) return
  pnlState.reinvestPct = v
  renderPnlChart()
})
document.getElementById('pnl-start-date').addEventListener('change', (e) => {
  pnlState.startDate = e.target.value || null
  renderPnlChart()
})
document.querySelectorAll('#pnl-fee-picker .pill').forEach(btn => {
  btn.addEventListener('click', () => {
    pnlState.feeType = btn.dataset.fee
    document.querySelectorAll('#pnl-fee-picker .pill').forEach(b => b.classList.toggle('active', b === btn))
    syncFeePreset()
    renderPnlChart()
  })
})
document.getElementById('pnl-fee-pct').addEventListener('input', (e) => {
  const v = parseFloat(e.target.value)
  if (!isFinite(v) || v < 0) return
  pnlState.feePct = v
  pnlState.feeType = 'custom'
  document.querySelectorAll('#pnl-fee-picker .pill').forEach(b => b.classList.remove('active'))
  renderPnlChart()
})
window.addEventListener('resize', () => {
  if (fundingChart) fundingChart.resize()
  if (priceChart) priceChart.resize()
  if (pnlChart) pnlChart.resize()
})

setInterval(updateCountdown, 1000)
setInterval(() => loadLive(state.asset), 30000)

setInitialSelection(state.exchange, state.asset)
