// Топ по накопленному фандингу (Блок 39) — отдельный блок-лидерборд. Данные
// (готовый рейтинг по всем периодам сразу) приходят из /api/leaderboard;
// фильтры класс/биржа/сторона и переключение периода — целиком на клиенте,
// без обращения к серверу. «Обновить сейчас» запускает фоновый пересчёт и
// опрашивает статус до готовности.
(function () {
  const TOP_N = 60
  const PERIOD_LABEL = { '1m': '1 мес', '2m': '2 мес', '3m': '3 мес', '6m': '6 мес' }
  const CLS_LABEL = { metal: 'металл', stock: 'акция', commodity: 'сырьё' }
  const EX_LABEL = { hyperliquid: 'Hyperliquid', binance: 'Binance', okx: 'OKX' }
  const EX_BADGE = { hyperliquid: 'hl', binance: 'bnb', okx: 'okx' }

  const state = { period: '2m', cls: 'all', exchange: 'all', side: 'short', data: null }
  let pollTimer = null

  const $ = (id) => document.getElementById(id)

  function fmtPct(v) {
    if (v === null || v === undefined) return '—'
    const sign = v > 0 ? '+' : ''
    return `${sign}${v.toFixed(2)}%`
  }
  function signClass(v) { return v > 0 ? 'pos' : v < 0 ? 'neg' : 'neutral' }

  function fmtUpdated(ts) {
    if (!ts) return 'ещё не обновлялось'
    const d = new Date(ts)
    const dd = String(d.getDate()).padStart(2, '0')
    const mm = String(d.getMonth() + 1).padStart(2, '0')
    const hh = String(d.getHours()).padStart(2, '0')
    const mi = String(d.getMinutes()).padStart(2, '0')
    return `обновлено ${dd}.${mm} ${hh}:${mi}`
  }

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

  function render() {
    const table = $('lb-table')
    if (!state.data || !state.data.rows || !state.data.rows.length) {
      table.innerHTML = ''
      $('lb-status').textContent = 'Рейтинг ещё не считался — нажми «Обновить сейчас» (займёт ~1-2 минуты).'
      return
    }
    const rows = state.data.rows.filter(r => {
      if (state.cls !== 'all' && r.cls !== state.cls) return false
      if (state.exchange !== 'all' && r.exchange !== state.exchange) return false
      const p = r.periods[state.period]
      return p && p.n > 0
    })
    rows.sort((a, b) => {
      const av = a.periods[state.period].sum_pct, bv = b.periods[state.period].sum_pct
      return state.side === 'short' ? bv - av : av - bv
    })
    const shown = rows.slice(0, TOP_N)

    $('lb-status').textContent = `Показаны топ-${Math.min(TOP_N, shown.length)} из ${rows.length} (фандинг накоплен за ${PERIOD_LABEL[state.period]}). ` +
      `«Фандинг» — суммарный процент за период; ≈APR — та же величина в годовых.`

    const head = `<thead><tr>
      <th class="lb-rank">#</th><th>Инструмент</th><th>Класс</th><th>Биржа</th>
      <th class="num">Фандинг за ${PERIOD_LABEL[state.period]}</th><th class="num">≈ APR</th>
      <th class="num">точек</th><th></th>
    </tr></thead>`

    const body = shown.map((r, i) => {
      const p = r.periods[state.period]
      const have = Object.prototype.hasOwnProperty.call(window.ASSET_LABELS || {}, r.base)
      const addCell = have
        ? '<span class="lb-have">✓ в дашборде</span>'
        : `<button class="lb-add-btn" data-base="${r.base}">+ в дашборд</button>`
      return `<tr>
        <td class="lb-rank">${i + 1}</td>
        <td><b>${r.base}</b></td>
        <td><span class="lb-cls-badge lb-cls-${r.cls}">${CLS_LABEL[r.cls] || r.cls}</span></td>
        <td><span class="badge ${EX_BADGE[r.exchange]}">${EX_LABEL[r.exchange]}</span></td>
        <td class="num ${signClass(p.sum_pct)}">${fmtPct(p.sum_pct)}</td>
        <td class="num ${signClass(p.apr)}">${p.apr === null ? '—' : fmtPct(p.apr)}</td>
        <td class="num" style="color:var(--text-tertiary)">${p.n}</td>
        <td>${addCell}</td>
      </tr>`
    }).join('')

    table.innerHTML = head + `<tbody>${body}</tbody>`
    table.querySelectorAll('.lb-add-btn').forEach(btn => {
      btn.addEventListener('click', () => addToDashboard(btn))
    })
  }

  async function addToDashboard(btn) {
    const base = btn.dataset.base
    btn.disabled = true
    btn.textContent = '⏳...'
    try {
      const r = await fetch('/api/leaderboard/add', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ base }),
      })
      const j = await r.json()
      if (j.ok) {
        btn.outerHTML = '<span class="lb-have">✓ добавлен (обнови страницу)</span>'
      } else {
        btn.textContent = '+ в дашборд'
        btn.disabled = false
        $('lb-status').textContent = '❌ ' + (j.error || 'не удалось добавить')
      }
    } catch (e) {
      btn.textContent = '+ в дашборд'
      btn.disabled = false
      $('lb-status').textContent = '❌ Ошибка сети: ' + e.message
    }
  }

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
        if (s.error) {
          $('lb-status').textContent = '❌ Пересчёт завершился с ошибкой: ' + s.error
        } else {
          await loadLeaderboard()
        }
      }
    } catch (e) {
      pollTimer = setTimeout(pollStatus, 3000)
    }
  }

  function wirePills(containerId, datasetKey, stateKey) {
    const el = $(containerId)
    if (!el) return
    el.querySelectorAll('.pill').forEach(btn => {
      btn.addEventListener('click', () => {
        state[stateKey] = btn.dataset[datasetKey]
        el.querySelectorAll('.pill').forEach(b => b.classList.toggle('active', b === btn))
        render()
      })
    })
  }

  document.addEventListener('DOMContentLoaded', () => {
    if (!$('lb-section')) return
    wirePills('lb-period', 'period', 'period')
    wirePills('lb-class', 'class', 'cls')
    wirePills('lb-exchange', 'exchange', 'exchange')
    wirePills('lb-side', 'side', 'side')
    $('lb-refresh-btn').addEventListener('click', refreshNow)
    loadLeaderboard()
    // Если пересчёт уже идёт (запущен из другой вкладки) — подхватываем прогресс.
    fetch('/api/leaderboard/status').then(r => r.json()).then(s => { if (s.running) pollStatus() })
  })
})();
