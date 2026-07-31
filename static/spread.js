// Спред и фандинг между биржами (Блок 41, Этап 1) — дельта-нейтральная связка
// «шорт на бирже с высоким фандингом + лонг на бирже с низким» по одному и тому
// же активу.
//
// Своего API у страницы нет: берём тот же /api/leaderboard, что и лидерборд —
// там уже лежит по каждой паре (биржа, инструмент) компактный СУТОЧНЫЙ ряд
// фандинга (d0 + dv), ликвидность (OI/оборот) и цена на момент пересчёта. Всё
// парное считается здесь, на клиенте: пар максимум 3 на актив, активов ~90 —
// это сотни строк, серверу тут делать нечего.
(function () {
  const TOP_N = 100
  const DAY_MS = 86400000
  const CLS_LABEL = { metal: 'металл', stock: 'акция', commodity: 'сырьё' }
  const EX_LABEL = { hyperliquid: 'Hyperliquid', binance: 'Binance', okx: 'OKX' }
  const EX_SHORT = { hyperliquid: 'HL', binance: 'Binance', okx: 'OKX' }
  const EX_BADGE = { hyperliquid: 'hl', binance: 'bnb', okx: 'okx' }
  const EX_ORDER = ['binance', 'okx', 'hyperliquid']

  // Ориентировочные обычные (не VIP) ставки за ОДНО исполнение, в % от ноционала
  // ноги. Те же значения, что DEFAULT_FEES_PCT в analysis.js (Блок 13) — держим
  // их согласованными между экранами.
  const FEES = {
    binance:     { taker: 0.05,  maker: 0.02 },
    okx:         { taker: 0.05,  maker: 0.02 },
    hyperliquid: { taker: 0.035, maker: 0.01 },
  }

  // Один и тот же инструмент на разных биржах называется по-разному: металлы на
  // Binance/OKX — XAU/XAG/XPT/XPD, на Hyperliquid — GOLD/SILVER/PLATINUM/
  // PALLADIUM; нефть Brent — BZ против BRENTOIL. Без приведения к общему ключу
  // металлы (главный интерес) вообще не попадали в сравнение с Hyperliquid —
  // группировка по сырому base считала их разными активами. Карта повторяет
  // METAL_ALIASES из app/leaderboard.py — держим синхронно.
  // Намеренно НЕ сводим SP500↔SPY и JP225↔EWJ и т.п.: это разные инструменты
  // (индекс против ETF) с разным масштабом цены — колонка спреда цен была бы
  // бессмыслицей.
  const BASE_ALIASES = {
    GOLD: 'XAU', SILVER: 'XAG', PLATINUM: 'XPT', PALLADIUM: 'XPD', COPPER: 'XCU',
    BRENTOIL: 'BZ',
  }
  const canonBase = (b) => BASE_ALIASES[b] || b

  const state = {
    from: null, to: null, cls: 'all', pair: 'all', minVol: 0, minAge: 0,
    feeMode: 'taker', feeCustom: null, profitableOnly: false,
    sort: { key: 'net', dir: 'desc' }, data: null,
  }
  let pollTimer = null
  const $ = (id) => document.getElementById(id)

  // ── форматирование ─────────────────────────────────────────────────────
  function fmtPct(v, d = 2) {
    if (v === null || v === undefined) return '—'
    return `${v > 0 ? '+' : ''}${v.toFixed(d)}%`
  }
  function signClass(v) { return v > 0 ? 'pos' : v < 0 ? 'neg' : 'neutral' }
  function fmtUsd(v) {
    if (v === null || v === undefined) return '—'
    const a = Math.abs(v)
    if (a >= 1e9) return '$' + (v / 1e9).toFixed(2) + 'B'
    if (a >= 1e6) return '$' + (v / 1e6).toFixed(1) + 'M'
    if (a >= 1e3) return '$' + (v / 1e3).toFixed(0) + 'K'
    return '$' + v.toFixed(0)
  }
  function fmtUpdated(ts) {
    if (!ts) return 'данные ещё не считались'
    const d = new Date(ts)
    const p = (n) => String(n).padStart(2, '0')
    return `данные от ${p(d.getDate())}.${p(d.getMonth() + 1)} ${p(d.getHours())}:${p(d.getMinutes())}`
  }
  function dateStr(ms) { return new Date(ms).toISOString().slice(0, 10) }
  function dayEpoch(ds) { return Math.floor(Date.parse(ds + 'T00:00:00Z') / DAY_MS) }
  // epoch-день 0 — это 1970-01-01, четверг, отсюда сдвиг +3 (0 = понедельник).
  function isWeekendDay(day) { return ((day + 3) % 7) >= 5 }

  // ── расчёт по паре бирж ────────────────────────────────────────────────
  // Разница фандинга считается ТОЛЬКО по дням, где есть данные у ОБЕИХ бирж:
  // охват истории у них разный (Binance/HL глубже, OKX короче), и если брать
  // суммы за разные наборы дней, разница получится не про фандинг, а про
  // покрытие данных.
  function pairStats(rowA, rowB, fromDay, toDay) {
    const idx = (row, day) => {
      if (row.d0 == null || !row.dv) return null
      const i = day - row.d0
      if (i < 0 || i >= row.dv.length) return null
      const v = row.dv[i]
      return (v === null || v === undefined) ? null : v
    }
    const daily = []
    for (let day = fromDay; day <= toDay; day++) {
      const va = idx(rowA, day), vb = idx(rowB, day)
      if (va === null || vb === null) continue
      daily.push({ day, diff: va - vb })
    }
    if (!daily.length) return null

    let sum = 0
    for (const d of daily) sum += d.diff
    const firstDay = daily[0].day, lastDay = daily[daily.length - 1].day
    const spanDays = Math.max(1, lastDay - firstDay + 1)
    const covered = daily.length
    const windowDays = toDay - fromDay + 1

    // Сторона: шортим биржу, где фандинг за период оказался ВЫШЕ (там платят
    // шортам больше), лонгуем вторую. Заработок связки = |разница|.
    const shortEx = sum > 0 ? rowA.exchange : rowB.exchange
    const longEx  = sum > 0 ? rowB.exchange : rowA.exchange
    const earned = Math.abs(sum)

    // Стабильность — доля дней, где суточная разница была того же знака, что и
    // итог. Ровная разница держится вперёд лучше, чем набранная одним спайком.
    const s = sum > 0 ? 1 : sum < 0 ? -1 : 0
    let sameSign = 0
    if (s !== 0) for (const d of daily) if ((d.diff > 0 ? 1 : d.diff < 0 ? -1 : 0) === s) sameSign++
    const stability = s === 0 ? null : sameSign / covered

    // Выходные против буден — по среднесуточной |разнице|.
    let weSum = 0, weN = 0, wdSum = 0, wdN = 0
    for (const d of daily) {
      if (isWeekendDay(d.day)) { weSum += Math.abs(d.diff); weN++ }
      else { wdSum += Math.abs(d.diff); wdN++ }
    }
    const weMean = weN ? weSum / weN : null
    const wdMean = wdN ? wdSum / wdN : null
    const weekendRatio = (weMean !== null && wdMean) ? weMean / wdMean : null

    return {
      sum, earned, shortEx, longEx, covered, spanDays, stability, weekendRatio,
      apr: earned / spanDays * 365,
      coverage: windowDays > 0 ? covered / windowDays : null,
    }
  }

  // Издержки связки: 4 исполнения — вход в обе ноги и выход из обеих, в % от
  // ноционала ОДНОЙ ноги (заработок от фандинга считается в тех же единицах).
  function costPct(exA, exB) {
    const one = (ex) => {
      if (state.feeCustom !== null) return state.feeCustom
      return (FEES[ex] || FEES.binance)[state.feeMode]
    }
    return (one(exA) + one(exB)) * 2
  }

  // Текущее расхождение цен: насколько шортовая нога дороже лонговой (в %).
  // Плюс = шортим более дорогую биржу, схождение цен сработает в плюс.
  function priceSpreadPct(rowShort, rowLong) {
    const ps = rowShort.price, pl = rowLong.price
    if (!ps || !pl) return null
    return (ps - pl) / pl * 100
  }

  function getSortVal(d, key) {
    if (key === 'funding') return d.st.earned
    if (key === 'apr') return d.st.apr
    if (key === 'cost') return d.cost
    if (key === 'net') return d.net
    if (key === 'payback') return d.payback
    if (key === 'stability') return d.st.stability
    if (key === 'weekend') return d.st.weekendRatio
    if (key === 'pspread') return d.pspread
    if (key === 'vol') return d.minVol
    return null
  }

  // ── загрузка ───────────────────────────────────────────────────────────
  async function loadData() {
    try {
      const r = await fetch('/api/leaderboard')
      const j = await r.json()
      state.data = j.data
      state.updatedAt = j.updated_at
      render()
    } catch (e) {
      $('sp-status').textContent = '❌ Не удалось загрузить данные: ' + e.message
    }
  }

  // ── таблица ────────────────────────────────────────────────────────────
  const COLS = [
    { key: 'rank', label: '#', cls: 'lb-rank', sort: false },
    { key: 'inst', label: 'Актив', sort: false },
    { key: 'cls', label: 'Класс', sort: false },
    { key: 'dir', label: 'Связка', sort: false, tip: 'Что делать: шорт на первой бирже (там фандинг за период был выше — платят шортам), лонг на второй. Позиции равного размера, движение цены самого актива взаимно гасится.' },
    { key: 'funding', label: 'Разница фандинга', num: true, sort: true, tip: 'Сколько процентов накопила разница фандинга между двумя биржами за период — это и есть заработок связки до издержек. Считается только по дням, где есть данные у обеих бирж.' },
    { key: 'apr', label: '≈ APR', num: true, sort: true, tip: 'Та же разница в годовых: сумма делится на охваченный промежуток и умножается на 365.' },
    { key: 'cost', label: 'Издержки', num: true, sort: true, tip: 'Комиссия за 4 исполнения (вход в две ноги + выход из двух) в % от ноционала одной ноги. Проскальзывание сюда НЕ входит — на тонких инструментах оно может быть больше комиссии.' },
    { key: 'net', label: 'Нетто', num: true, sort: true, tip: 'Разница фандинга минус издержки. Это ответ на вопрос «остаётся ли что-то после спреда» за выбранный период.' },
    { key: 'payback', label: 'Окупаемость', num: true, sort: true, tip: 'Сколько дней надо продержать связку, чтобы разница фандинга покрыла издержки на вход и выход (при том же среднем темпе).' },
    { key: 'stability', label: 'Стабильн.', num: true, sort: true, tip: 'Доля дней, где разница была того же знака, что и итог. Ближе к 100% — направление держалось ровно; низкая — набрано спайками и часто переворачивалось.' },
    { key: 'weekend', label: 'Выходные', num: true, sort: true, tip: 'Во сколько раз среднесуточная разница фандинга на выходных больше, чем в будни. Больше 1 — эффект действительно концентрируется в выходные.' },
    { key: 'pspread', label: 'Вход по спреду', num: true, sort: true, tip: 'Насколько шортовая нога сейчас дороже лонговой. Плюс (✓) — шортим более дорогую биржу, схождение цен добавит прибыли. Минус (✗) — вход против схождения. Цены на момент последнего пересчёта данных.' },
    { key: 'vol', label: 'Слабая нога', num: true, sort: true, tip: 'Меньший из суточных оборотов двух бирж — по этой ноге и будет проскальзывание.' },
  ]

  function render() {
    const table = $('sp-table')
    if (!state.data || !state.data.rows || !state.data.rows.length) {
      table.innerHTML = ''
      $('sp-summary').innerHTML = ''
      $('sp-status').textContent = 'Данные ещё не считались — нажми «Обновить данные» (займёт ~1-2 минуты).'
      return
    }
    if (!state.from || !state.to) { $('sp-status').textContent = '⚠️ Укажи обе даты периода (с и по).'; return }
    const fromDay = dayEpoch(state.from), toDay = dayEpoch(state.to)
    if (toDay < fromDay) { $('sp-status').textContent = '⚠️ Дата «по» раньше даты «с» — поправь период.'; return }

    // группируем строки по активу: канонический base -> { биржа: строка }
    const byBase = {}
    const baseFirstDay = {}
    for (const row of state.data.rows) {
      const cb = canonBase(row.base)
      if (!byBase[cb]) byBase[cb] = {}
      byBase[cb][row.exchange] = row
      if (row.d0 == null) continue
      if (!(cb in baseFirstDay) || row.d0 < baseFirstDay[cb]) baseFirstDay[cb] = row.d0
    }
    const todayDay = Math.floor(Date.now() / DAY_MS)

    let decorated = []
    let noPrice = 0
    for (const base of Object.keys(byBase)) {
      const exs = byBase[base]
      const present = EX_ORDER.filter(e => exs[e])
      if (present.length < 2) continue
      const age = (base in baseFirstDay) ? (todayDay - baseFirstDay[base]) : null
      if (state.minAge > 0 && !(age >= state.minAge)) continue

      for (let i = 0; i < present.length; i++) {
        for (let k = i + 1; k < present.length; k++) {
          const a = present[i], b = present[k]
          const pairKey = [a, b].sort().join('|')
          if (state.pair !== 'all' && state.pair !== pairKey) continue
          const rowA = exs[a], rowB = exs[b]
          if (state.cls !== 'all' && rowA.cls !== state.cls) continue

          const st = pairStats(rowA, rowB, fromDay, toDay)
          if (!st) continue
          const rowShort = exs[st.shortEx], rowLong = exs[st.longEx]
          const minVol = (rowA.vol == null || rowB.vol == null) ? null : Math.min(rowA.vol, rowB.vol)
          if (state.minVol > 0 && !(minVol >= state.minVol)) continue

          const cost = costPct(a, b)
          const net = st.earned - cost
          const perDay = st.earned / st.spanDays
          const payback = perDay > 0 ? cost / perDay : null
          const pspread = priceSpreadPct(rowShort, rowLong)
          if (pspread === null) noPrice++
          if (state.profitableOnly && !(net > 0)) continue

          decorated.push({ base, cls: rowA.cls, age, st, rowShort, rowLong, cost, net, payback, pspread, minVol })
        }
      }
    }

    const key = state.sort.key
    const dir = state.sort.dir === 'asc' ? 'asc' : 'desc'
    decorated.sort((x, y) => {
      const vx = getSortVal(x, key), vy = getSortVal(y, key)
      const nx = vx === null || vx === undefined, ny = vy === null || vy === undefined
      if (nx && ny) return 0
      if (nx) return 1
      if (ny) return -1
      return dir === 'asc' ? vx - vy : vy - vx
    })
    const shown = decorated.slice(0, TOP_N)

    const arrow = (k) => state.sort.key !== k ? '' : (state.sort.dir === 'asc' ? ' ▲' : ' ▼')
    // Подсказки заголовков — нативным title, а не .info-tip::after: таблица
    // лежит в контейнере с overflow, который обрезал бы всплывающее окно
    // (тот же случай, что уже чинили в лидерборде).
    const head = '<thead><tr>' + COLS.map(c => {
      const cls = [c.cls, c.num ? 'num' : '', c.sort ? 'lb-sortable' : ''].filter(Boolean).join(' ')
      const mark = c.tip ? ' <span class="lb-tip-mark">ⓘ</span>' : ''
      const titleAttr = c.tip ? ` title="${c.tip.replace(/"/g, '&quot;')}"` : ''
      const sortAttr = c.sort ? ` data-sort="${c.key}"` : ''
      return `<th class="${cls}"${sortAttr}${titleAttr}>${c.label}${mark}${c.sort ? arrow(c.key) : ''}</th>`
    }).join('') + '</tr></thead>'

    const labels = (typeof ASSET_LABELS !== 'undefined') ? ASSET_LABELS : {}
    const body = shown.map((d, i) => {
      const st = d.st
      const inDash = Object.prototype.hasOwnProperty.call(labels, d.base)
      const ageTitle = d.age == null ? 'история недоступна' : `торгуется ~${d.age} дн. (по доступной истории, макс. ~190)`
      // Если тикер на биржах называется по-разному (XAU против GOLD) — показываем
      // это явно, чтобы не гадать, что с чем сравнивается.
      const alias = d.rowShort.base === d.rowLong.base ? '' :
        ` <span class="sp-alias" title="${EX_LABEL[st.shortEx]}: ${d.rowShort.base} · ${EX_LABEL[st.longEx]}: ${d.rowLong.base}">≡ ${d.rowShort.base === d.base ? d.rowLong.base : d.rowShort.base}</span>`
      const dirCell =
        `<span class="badge ${EX_BADGE[st.shortEx]}" title="здесь шорт">S ${EX_SHORT[st.shortEx]}</span>` +
        `<span class="sp-arrow">→</span>` +
        `<span class="badge ${EX_BADGE[st.longEx]}" title="здесь лонг">L ${EX_SHORT[st.longEx]}</span>`
      const stab = st.stability === null ? '—' : `${Math.round(st.stability * 100)}%`
      const wk = st.weekendRatio === null ? '—' : `×${st.weekendRatio.toFixed(1)}`
      const payback = d.payback === null ? '—'
        : (d.payback > 999 ? '>999 дн' : `${Math.round(d.payback)} дн`)
      const paybackCls = d.payback === null ? 'neutral' : (d.payback <= st.spanDays ? 'pos' : 'neg')
      let pspreadCell = '<span class="lb-muted">—</span>'
      if (d.pspread !== null) {
        const ok = d.pspread >= 0
        pspreadCell = `<span class="${ok ? 'pos' : 'neg'}" title="${ok
          ? 'шортовая нога дороже — схождение цен в плюс'
          : 'шортовая нога дешевле — вход против схождения'}">${ok ? '✓' : '✗'} ${fmtPct(d.pspread, 2)}</span>`
      }
      const cover = st.coverage === null ? '' : ` · покрытие ${Math.round(st.coverage * 100)}%`
      return `<tr>
        <td class="lb-rank">${i + 1}</td>
        <td><b title="${ageTitle}">${d.base}</b>${alias}${inDash ? ' <span class="lb-have" title="есть в дашборде">✓</span>' : ''}</td>
        <td><span class="lb-cls-badge lb-cls-${d.cls}">${CLS_LABEL[d.cls] || d.cls}</span></td>
        <td class="sp-dir">${dirCell}</td>
        <td class="num pos" title="${st.covered} дн. общих данных${cover}">${fmtPct(st.earned)}</td>
        <td class="num pos">${fmtPct(st.apr)}</td>
        <td class="num neg">−${d.cost.toFixed(2)}%</td>
        <td class="num ${signClass(d.net)}"><b>${fmtPct(d.net)}</b></td>
        <td class="num ${paybackCls}">${payback}</td>
        <td class="num">${stab}</td>
        <td class="num">${wk}</td>
        <td class="num">${pspreadCell}</td>
        <td class="num" style="color:var(--text-secondary)">${fmtUsd(d.minVol)}</td>
      </tr>`
    }).join('')

    // сводка — сразу отвечает на «есть ли вообще что ловить»
    const positive = decorated.filter(d => d.net > 0)
    const nets = decorated.map(d => d.net).sort((a, b) => a - b)
    const median = nets.length ? nets[Math.floor(nets.length / 2)] : null
    const best = decorated.reduce((m, d) => (m === null || d.net > m.net) ? d : m, null)
    const days = toDay - fromDay + 1
    $('sp-summary').innerHTML = `
      <div class="sp-stat"><div class="sp-stat-label">Всего пар</div><div class="sp-stat-value">${decorated.length}</div></div>
      <div class="sp-stat"><div class="sp-stat-label">Нетто &gt; 0</div><div class="sp-stat-value ${positive.length ? 'pos' : 'neutral'}">${positive.length}</div></div>
      <div class="sp-stat"><div class="sp-stat-label">Медианное нетто</div><div class="sp-stat-value ${signClass(median)}">${fmtPct(median)}</div></div>
      <div class="sp-stat"><div class="sp-stat-label">Лучшая пара</div><div class="sp-stat-value">${best ? `${best.base} <span class="sp-stat-sub">${fmtPct(best.net)}</span>` : '—'}</div></div>`

    $('sp-status').textContent =
      `Показаны ${shown.length} из ${decorated.length} пар за ${state.from} → ${state.to} (${days} дн.), ` +
      `${fmtUpdated(state.updatedAt)}. Издержки: ${state.feeCustom !== null ? 'своя ставка ' + state.feeCustom + '%' : (state.feeMode === 'taker' ? 'тейкер' : 'мейкер')}, ` +
      `4 исполнения.` + (noPrice ? ` У ${noPrice} пар нет цены в кеше (соберётся при следующем «Обновить данные») — колонка входа пустая.` : '')

    table.innerHTML = head + `<tbody>${body}</tbody>`
    table.querySelectorAll('th.lb-sortable').forEach(th => th.addEventListener('click', () => {
      const k = th.dataset.sort
      if (state.sort.key === k) {
        state.sort.dir = state.sort.dir === 'asc' ? 'desc' : 'asc'
      } else {
        state.sort.key = k
        // окупаемость и издержки интереснее по возрастанию (чем меньше, тем лучше)
        state.sort.dir = (k === 'payback' || k === 'cost') ? 'asc' : 'desc'
      }
      render()
    }))
  }

  // ── пересчёт (тот же фоновый проход, что у лидерборда) ─────────────────
  async function refreshNow() {
    const btn = $('sp-refresh-btn')
    btn.disabled = true
    try {
      await fetch('/api/leaderboard/refresh', { method: 'POST' })
      pollStatus()
    } catch (e) {
      $('sp-status').textContent = '❌ Не удалось запустить пересчёт: ' + e.message
      btn.disabled = false
    }
  }

  async function pollStatus() {
    try {
      const r = await fetch('/api/leaderboard/status')
      const s = await r.json()
      if (s.running) {
        const pct = s.total ? Math.round((s.progress / s.total) * 100) : 0
        $('sp-status').textContent = `⏳ Пересчёт... ${s.progress}/${s.total} (${pct}%) — можно не ждать, обновится само.`
        pollTimer = setTimeout(pollStatus, 2000)
      } else {
        clearTimeout(pollTimer)
        $('sp-refresh-btn').disabled = false
        if (s.error) $('sp-status').textContent = '❌ Пересчёт завершился с ошибкой: ' + s.error
        else await loadData()
      }
    } catch (e) {
      pollTimer = setTimeout(pollStatus, 3000)
    }
  }

  // ── контролы ───────────────────────────────────────────────────────────
  function markActivePreset(days) {
    document.querySelectorAll('#sp-period .pill').forEach(b => b.classList.toggle('active', +b.dataset.days === days))
  }
  function setPreset(days) {
    const now = Date.now()
    state.to = dateStr(now)
    state.from = dateStr(now - days * DAY_MS)
    $('sp-from').value = state.from
    $('sp-to').value = state.to
    markActivePreset(days)
    render()
  }
  function wirePills(containerId, datasetKey, stateKey, transform) {
    const el = $(containerId)
    if (!el) return
    el.querySelectorAll('.pill').forEach(btn => {
      btn.addEventListener('click', () => {
        state[stateKey] = transform ? transform(btn.dataset[datasetKey]) : btn.dataset[datasetKey]
        el.querySelectorAll('.pill').forEach(b => b.classList.toggle('active', b === btn))
        render()
      })
    })
  }

  document.addEventListener('DOMContentLoaded', () => {
    if (!$('sp-table')) return
    const now = Date.now()
    state.to = dateStr(now)
    state.from = dateStr(now - 60 * DAY_MS)
    $('sp-from').value = state.from
    $('sp-to').value = state.to
    $('sp-from').max = state.to
    $('sp-to').max = state.to
    markActivePreset(60)

    document.querySelectorAll('#sp-period .pill').forEach(btn =>
      btn.addEventListener('click', () => setPreset(+btn.dataset.days)))
    $('sp-from').addEventListener('change', () => { state.from = $('sp-from').value; markActivePreset(-1); render() })
    $('sp-to').addEventListener('change', () => { state.to = $('sp-to').value; markActivePreset(-1); render() })

    wirePills('sp-class', 'class', 'cls')
    wirePills('sp-pair', 'pair', 'pair')
    wirePills('sp-minvol', 'minvol', 'minVol', (v) => +v)
    wirePills('sp-profitable', 'profitable', 'profitableOnly', (v) => v === '1')

    // Пресеты комиссии и своя ставка — взаимоисключающие: ручной ввод снимает
    // подсветку пресетов (тот же приём, что в P&L-симуляторе, Блок 13).
    $('sp-fee').querySelectorAll('.pill').forEach(btn => {
      btn.addEventListener('click', () => {
        state.feeMode = btn.dataset.fee
        state.feeCustom = null
        $('sp-fee-custom').value = ''
        $('sp-fee').querySelectorAll('.pill').forEach(b => b.classList.toggle('active', b === btn))
        render()
      })
    })
    $('sp-fee-custom').addEventListener('input', () => {
      const raw = $('sp-fee-custom').value.trim()
      const v = parseFloat(raw)
      state.feeCustom = (raw === '' || isNaN(v) || v < 0) ? null : v
      const custom = state.feeCustom !== null
      $('sp-fee').querySelectorAll('.pill').forEach(b =>
        b.classList.toggle('active', !custom && b.dataset.fee === state.feeMode))
      render()
    })

    $('sp-min-age').addEventListener('input', () => {
      state.minAge = Math.max(0, parseInt($('sp-min-age').value, 10) || 0)
      render()
    })
    $('sp-refresh-btn').addEventListener('click', refreshNow)

    loadData()
    fetch('/api/leaderboard/status').then(r => r.json()).then(s => { if (s.running) pollStatus() })
  })
})();
