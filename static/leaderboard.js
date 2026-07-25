// Топ по накопленному фандингу (Блок 39 + Блок 40) — отдельный блок-лидерборд.
// С Блока 40 сервер отдаёт по каждому инструменту КОМПАКТНЫЙ ДНЕВНОЙ РЯД
// фандинга (d0 + dv), а также ликвидность (OI/оборот) и данные Vantage (своп/
// спред/флаг). Сумму за ПРОИЗВОЛЬНЫЙ период [с..по], APR, стабильность,
// покрытие и нетто (за вычетом второй ноги) считает целиком клиент — без
// обращения к серверу. «Обновить сейчас» запускает фоновый пересчёт.
(function () {
  const TOP_N = 80
  const DAY_MS = 86400000
  const CLS_LABEL = { metal: 'металл', stock: 'акция', commodity: 'сырьё' }
  const EX_LABEL = { hyperliquid: 'Hyperliquid', binance: 'Binance', okx: 'OKX' }
  const EX_BADGE = { hyperliquid: 'hl', binance: 'bnb', okx: 'okx' }

  // sort.key: funding | apr | stability | oi | vol | net. dir=null у funding —
  // авто-направление по выбранной стороне (шорт → по убыванию, лонг → по возр.).
  const state = {
    from: null, to: null, cls: 'all', exchange: 'all', side: 'short',
    minVol: 0, sort: { key: 'funding', dir: null }, data: null,
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
    if (!ts) return 'ещё не обновлялось'
    const d = new Date(ts)
    const p = (n) => String(n).padStart(2, '0')
    return `обновлено ${p(d.getDate())}.${p(d.getMonth() + 1)} ${p(d.getHours())}:${p(d.getMinutes())}`
  }
  function dateStr(ms) { return new Date(ms).toISOString().slice(0, 10) }
  function dayEpoch(ds) { return Math.floor(Date.parse(ds + 'T00:00:00Z') / DAY_MS) }

  // ── расчёт по окну из дневного ряда ────────────────────────────────────
  // Сумма ставок за [fromDay..toDay], APR (годовая экстраполяция по реально
  // покрытому промежутку), покрытие (доля дней окна с данными) и стабильность
  // (доля покрытых дней с тем же знаком, что итог — ровный карри надёжнее
  // спайкового). null, если в окне у инструмента нет ни одной точки.
  function windowStats(row, fromDay, toDay) {
    const d0 = row.d0, dv = row.dv
    if (d0 == null || !dv || !dv.length) return null
    let sum = 0, covered = 0, firstDay = null, lastDay = null, sameSign = 0
    const signs = []
    for (let i = 0; i < dv.length; i++) {
      const day = d0 + i
      if (day < fromDay || day > toDay) continue
      const v = dv[i]
      if (v === null || v === undefined) continue
      sum += v; covered++
      if (firstDay === null) firstDay = day
      lastDay = day
      signs.push(v)
    }
    if (!covered) return null
    const spanDays = Math.max(1, lastDay - firstDay + 1)
    const apr = spanDays >= 1 ? sum / spanDays * 365 : null
    const s = sum > 0 ? 1 : sum < 0 ? -1 : 0
    if (s !== 0) for (const v of signs) if ((v > 0 ? 1 : v < 0 ? -1 : 0) === s) sameSign++
    const stability = s === 0 ? null : sameSign / covered
    const windowDays = toDay - fromDay + 1
    const coverage = windowDays > 0 ? covered / windowDays : null
    return { sum, apr, covered, spanDays, stability, coverage }
  }

  // Нетто за вычетом второй ноги (Vantage): фандинг, что реально захватит
  // выбранная сторона, плюс своп хедж-ноги (противоположная сторона) за срок,
  // минус спред round-trip. Возвращаем в ТОЙ ЖЕ знаковой конвенции, что и
  // колонка «Фандинг» (шорт-карри плюсовой, лонг-карри минусовой), чтобы не
  // путать. null, если нет данных Vantage или своп в пунктах без цены.
  function netStats(row, ws) {
    const v = row.vantage
    if (!v || !ws) return null
    const swapNight = state.side === 'short' ? v.swap_long_night : v.swap_short_night
    if (swapNight === null || swapNight === undefined) return null
    const spread = (v.spread_pct === null || v.spread_pct === undefined) ? 0 : v.spread_pct
    const earned = state.side === 'short' ? ws.sum : -ws.sum
    const netEarned = earned + swapNight * ws.spanDays - spread * 2
    return state.side === 'short' ? netEarned : -netEarned
  }

  function getSortVal(dec, key) {
    if (key === 'funding') return dec.ws.sum
    if (key === 'apr') return dec.ws.apr
    if (key === 'stability') return dec.ws.stability
    if (key === 'oi') return dec.row.oi
    if (key === 'vol') return dec.row.vol
    if (key === 'net') return dec.net
    return null
  }

  // ── загрузка/статус ────────────────────────────────────────────────────
  async function loadLeaderboard() {
    try {
      const r = await fetch('/api/leaderboard')
      const j = await r.json()
      state.data = j.data
      $('lb-updated').textContent = j.data ? fmtUpdated(j.updated_at) : ''
      render()
    } catch (e) {
      $('lb-status').textContent = '❌ Не удалось загрузить рейтинг: ' + e.message
    }
  }

  // ── рендер таблицы ─────────────────────────────────────────────────────
  const COLS = [
    { key: 'rank', label: '#', cls: 'lb-rank', sort: false },
    { key: 'inst', label: 'Инструмент', sort: false },
    { key: 'cls', label: 'Класс', sort: false },
    { key: 'exchange', label: 'Биржа', sort: false },
    { key: 'funding', label: 'Фандинг', num: true, sort: true },
    { key: 'apr', label: '≈ APR', num: true, sort: true },
    { key: 'stability', label: 'Стабильн.', num: true, sort: true, tip: 'Доля дней, где фандинг был того же знака, что и итог за период. Ближе к 100% — ровный карри, ниже — спайковый (риск разворотов).' },
    { key: 'net', label: 'Нетто', num: true, sort: true, tip: 'Фандинг за вычетом второй ноги на Vantage: своп хедж-позиции за период + спред (round-trip). Только для инструментов, что есть на Vantage.' },
    { key: 'oi', label: 'OI', num: true, sort: true, tip: 'Открытый интерес — сколько денег «стоит» в инструменте. Прокси того, насколько глубоко можно набрать позицию.' },
    { key: 'vol', label: 'Оборот 24ч', num: true, sort: true, tip: 'Суточный оборот в $ — насколько ликвидно входить/выходить.' },
    { key: 'add', label: '', sort: false },
  ]

  function render() {
    const table = $('lb-table')
    if (!state.data || !state.data.rows || !state.data.rows.length) {
      table.innerHTML = ''
      $('lb-status').textContent = 'Рейтинг ещё не считался — нажми «Обновить сейчас» (займёт ~1-2 минуты).'
      return
    }
    if (!state.from || !state.to) { $('lb-status').textContent = '⚠️ Укажи обе даты периода (с и по).'; return }
    const fromDay = dayEpoch(state.from), toDay = dayEpoch(state.to)
    if (toDay < fromDay) { $('lb-status').textContent = '⚠️ Дата «по» раньше даты «с» — поправь период.'; return }

    // декорируем строки расчётом за окно + фильтры
    let decorated = []
    for (const row of state.data.rows) {
      if (state.cls !== 'all' && row.cls !== state.cls) continue
      if (state.exchange !== 'all' && row.exchange !== state.exchange) continue
      const ws = windowStats(row, fromDay, toDay)
      if (!ws) continue
      if (state.minVol > 0 && !(row.vol >= state.minVol)) continue
      decorated.push({ row, ws, net: null })
    }
    for (const d of decorated) d.net = netStats(d.row, d.ws)

    // сортировка
    const key = state.sort.key
    if (key === 'funding' && state.sort.dir === null) {
      decorated.sort((a, b) => state.side === 'short' ? b.ws.sum - a.ws.sum : a.ws.sum - b.ws.sum)
    } else {
      const dir = state.sort.dir === 'asc' ? 'asc' : 'desc'
      decorated.sort((a, b) => {
        const va = getSortVal(a, key), vb = getSortVal(b, key)
        const na = va === null || va === undefined, nb = vb === null || vb === undefined
        if (na && nb) return 0
        if (na) return 1
        if (nb) return -1
        return dir === 'asc' ? va - vb : vb - va
      })
    }
    const shown = decorated.slice(0, TOP_N)

    const arrow = (k) => {
      if (k === 'funding' && state.sort.key === 'funding' && state.sort.dir === null)
        return state.side === 'short' ? ' ▼' : ' ▲'
      if (state.sort.key !== k) return ''
      return state.sort.dir === 'asc' ? ' ▲' : ' ▼'
    }
    const head = '<thead><tr>' + COLS.map(c => {
      const cls = [c.cls, c.num ? 'num' : '', c.sort ? 'lb-sortable' : ''].filter(Boolean).join(' ')
      const tip = c.tip ? `<span class="info-tip" data-tip="${c.tip}">ⓘ</span>` : ''
      const sortAttr = c.sort ? ` data-sort="${c.key}"` : ''
      return `<th class="${cls}"${sortAttr}>${c.label}${tip}${c.sort ? arrow(c.key) : ''}</th>`
    }).join('') + '</tr></thead>'

    const labels = (typeof ASSET_LABELS !== 'undefined') ? ASSET_LABELS : {}
    const body = shown.map((d, i) => {
      const r = d.row, ws = d.ws
      const have = Object.prototype.hasOwnProperty.call(labels, r.base)
      const addCell = have
        ? '<span class="lb-have">✓</span>'
        : `<button class="lb-add-btn" data-base="${r.base}">+</button>`
      const stab = ws.stability === null ? '—' : `${Math.round(ws.stability * 100)}%`
      const cover = ws.coverage === null ? '' : ` · покрытие ${Math.round(ws.coverage * 100)}%`
      // Vantage-ячейка: нетто, если считается; иначе просто флаг «есть на Vantage».
      let netCell = '<span class="lb-muted">—</span>'
      if (r.vantage) {
        const sym = r.vantage.symbol
        if (d.net !== null && d.net !== undefined) {
          netCell = `<span class="lb-net ${signClass(d.net)}" title="🛡 Vantage ${sym} · за вычетом свопа/спреда 2-й ноги">${fmtPct(d.net)}</span>`
        } else {
          netCell = `<span class="lb-vflag" title="🛡 Есть на Vantage (${sym}) — хеджируемо 2-й ногой">🛡</span>`
        }
      }
      return `<tr>
        <td class="lb-rank">${i + 1}</td>
        <td><b>${r.base}</b></td>
        <td><span class="lb-cls-badge lb-cls-${r.cls}">${CLS_LABEL[r.cls] || r.cls}</span></td>
        <td><span class="badge ${EX_BADGE[r.exchange]}">${EX_LABEL[r.exchange]}</span></td>
        <td class="num ${signClass(ws.sum)}" title="${ws.covered} дн. данных${cover}">${fmtPct(ws.sum)}</td>
        <td class="num ${signClass(ws.apr)}">${ws.apr === null ? '—' : fmtPct(ws.apr)}</td>
        <td class="num">${stab}</td>
        <td class="num">${netCell}</td>
        <td class="num" style="color:var(--text-secondary)">${fmtUsd(r.oi)}</td>
        <td class="num" style="color:var(--text-secondary)">${fmtUsd(r.vol)}</td>
        <td>${addCell}</td>
      </tr>`
    }).join('')

    const days = toDay - fromDay + 1
    $('lb-status').textContent = `Показаны топ-${Math.min(TOP_N, shown.length)} из ${decorated.length} (фандинг накоплен за ${state.from} → ${state.to}, ${days} дн.). ` +
      `«Фандинг» — суммарный % за период, ≈APR — та же величина в годовых.`

    table.innerHTML = head + `<tbody>${body}</tbody>`
    table.querySelectorAll('.lb-add-btn').forEach(btn => btn.addEventListener('click', () => addToDashboard(btn)))
    table.querySelectorAll('th.lb-sortable').forEach(th => th.addEventListener('click', () => {
      const k = th.dataset.sort
      if (state.sort.key === k) {
        // toggle; для funding третий клик возвращает авто-режим по стороне
        if (k === 'funding') {
          state.sort.dir = state.sort.dir === null ? 'asc' : (state.sort.dir === 'asc' ? 'desc' : null)
        } else {
          state.sort.dir = state.sort.dir === 'asc' ? 'desc' : 'asc'
        }
      } else {
        state.sort.key = k
        state.sort.dir = k === 'funding' ? null : 'desc'
      }
      render()
    }))
  }

  async function addToDashboard(btn) {
    const base = btn.dataset.base
    btn.disabled = true
    btn.textContent = '⏳'
    try {
      const r = await fetch('/api/leaderboard/add', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ base }),
      })
      const j = await r.json()
      if (j.ok) {
        btn.outerHTML = '<span class="lb-have" title="добавлен — обнови страницу">✓</span>'
      } else {
        btn.textContent = '+'
        btn.disabled = false
        $('lb-status').textContent = '❌ ' + (j.error || 'не удалось добавить')
      }
    } catch (e) {
      btn.textContent = '+'
      btn.disabled = false
      $('lb-status').textContent = '❌ Ошибка сети: ' + e.message
    }
  }

  // ── пересчёт ───────────────────────────────────────────────────────────
  async function refreshNow() {
    const btn = $('lb-refresh-btn')
    btn.disabled = true
    try {
      await fetch('/api/leaderboard/refresh', { method: 'POST' })
      pollStatus()
    } catch (e) {
      $('lb-status').textContent = '❌ Не удалось запустить пересчёт: ' + e.message
      btn.disabled = false
    }
  }

  async function pollStatus() {
    try {
      const r = await fetch('/api/leaderboard/status')
      const s = await r.json()
      if (s.running) {
        const pct = s.total ? Math.round((s.progress / s.total) * 100) : 0
        $('lb-status').textContent = `⏳ Пересчёт... ${s.progress}/${s.total} (${pct}%) — можно не ждать, обновится само.`
        pollTimer = setTimeout(pollStatus, 2000)
      } else {
        clearTimeout(pollTimer)
        $('lb-refresh-btn').disabled = false
        if (s.error) $('lb-status').textContent = '❌ Пересчёт завершился с ошибкой: ' + s.error
        else await loadLeaderboard()
      }
    } catch (e) {
      pollTimer = setTimeout(pollStatus, 3000)
    }
  }

  // ── контролы ───────────────────────────────────────────────────────────
  function setPreset(days) {
    const now = Date.now()
    state.to = dateStr(now)
    state.from = dateStr(now - days * DAY_MS)
    $('lb-from').value = state.from
    $('lb-to').value = state.to
    markActivePreset(days)
    render()
  }
  function markActivePreset(days) {
    document.querySelectorAll('#lb-period .pill').forEach(b => b.classList.toggle('active', +b.dataset.days === days))
  }

  function wirePills(containerId, datasetKey, stateKey, numeric) {
    const el = $(containerId)
    if (!el) return
    el.querySelectorAll('.pill').forEach(btn => {
      btn.addEventListener('click', () => {
        const raw = btn.dataset[datasetKey]
        state[stateKey] = numeric ? +raw : raw
        el.querySelectorAll('.pill').forEach(b => b.classList.toggle('active', b === btn))
        render()
      })
    })
  }

  document.addEventListener('DOMContentLoaded', () => {
    if (!$('lb-section')) return
    // дефолт: последние 60 дней
    const now = Date.now()
    state.to = dateStr(now)
    state.from = dateStr(now - 60 * DAY_MS)
    $('lb-from').value = state.from
    $('lb-to').value = state.to
    $('lb-from').max = state.to
    $('lb-to').max = state.to
    markActivePreset(60)

    document.querySelectorAll('#lb-period .pill').forEach(btn =>
      btn.addEventListener('click', () => setPreset(+btn.dataset.days)))
    $('lb-from').addEventListener('change', () => { state.from = $('lb-from').value; markActivePreset(-1); render() })
    $('lb-to').addEventListener('change', () => { state.to = $('lb-to').value; markActivePreset(-1); render() })

    wirePills('lb-class', 'class', 'cls', false)
    wirePills('lb-exchange', 'exchange', 'exchange', false)
    wirePills('lb-side', 'side', 'side', false)
    wirePills('lb-minvol', 'minvol', 'minVol', true)

    $('lb-refresh-btn').addEventListener('click', refreshNow)
    loadLeaderboard()
    fetch('/api/leaderboard/status').then(r => r.json()).then(s => { if (s.running) pollStatus() })
  })
})();
