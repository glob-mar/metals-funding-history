async function deleteAsset(asset) {
  if (!confirm(`Удалить актив ${asset}? Собранная история в БД останется, но пропадёт из списка.`)) return
  try {
    const r = await fetch('/api/assets/' + asset, { method: 'DELETE' })
    const data = await r.json()
    if (!data.ok) {
      alert('Ошибка: ' + (data.error || 'неизвестная ошибка'))
      return
    }
    location.reload()
  } catch (e) {
    alert('Ошибка сети: ' + e.message)
  }
}

async function loadSyncStatus() {
  const el = document.getElementById('auto-sync-status')
  if (!el) return null
  try {
    const r = await fetch('/api/sync-status')
    const data = await r.json()
    if (!data.ok) {
      el.textContent = '⚠️ Не удалось получить статус автосбора'
      return null
    }
    const every = `Автосбор каждые ${data.interval_minutes} мин.`
    if (!data.started_at) {
      el.textContent = `🕐 ${every} Ещё не запускался (первый проход — вскоре после старта сервера).`
      return data
    }
    const started = new Date(data.started_at).toLocaleString('ru-RU')
    if (data.running) {
      el.textContent = `⏳ ${every} Идёт сбор, начат в ${started}...`
      return data
    }
    const results = data.results || {}
    const assets = Object.keys(results)
    const ok = assets.filter(a => results[a].ok).length
    const finished = data.finished_at ? new Date(data.finished_at).toLocaleString('ru-RU') : '—'
    el.textContent = `✅ ${every} Последний проход: ${finished} (успешно ${ok}/${assets.length} активов)`
    return data
  } catch (e) {
    el.textContent = '⚠️ Ошибка сети при получении статуса автосбора'
    return null
  }
}

let syncAllPollTimer = null

async function syncAll() {
  const btn = document.getElementById('sync-all-btn')
  try {
    const r = await fetch('/api/sync-all', { method: 'POST' })
    const data = await r.json()
    if (!data.ok && !data.started) {
      // 409 — сбор уже идёт кем-то другим, просто подключаемся к опросу статуса.
      if (r.status !== 409) {
        alert('Ошибка: ' + (data.error || 'неизвестная ошибка'))
        return
      }
    }
  } catch (e) {
    alert('Ошибка сети: ' + e.message)
    return
  }

  btn.disabled = true
  btn.textContent = '⏳ Собираю...'
  if (syncAllPollTimer) clearInterval(syncAllPollTimer)
  syncAllPollTimer = setInterval(async () => {
    const data = await loadSyncStatus()
    if (data && !data.running) {
      clearInterval(syncAllPollTimer)
      syncAllPollTimer = null
      btn.disabled = false
      btn.textContent = '▶ Собрать всё'
    }
  }, 3000)
}

// Кэш списков инструментов на стороне клиента — сами списки почти не меняются
// за сессию, повторный fetch при переключении биржи туда-обратно не нужен.
const instrumentCache = {}
let hlDexesCache = null

async function loadInstrumentList(exchange) {
  if (instrumentCache[exchange]) return instrumentCache[exchange]
  const r = await fetch(`/api/instruments/${exchange}`)
  const data = await r.json()
  const items = data.ok ? data.items : []
  instrumentCache[exchange] = items
  return items
}

async function loadHlDexes() {
  if (hlDexesCache) return hlDexesCache
  const r = await fetch('/api/instruments/hyperliquid/dexes')
  const data = await r.json()
  hlDexesCache = data.ok ? data.items : []
  return hlDexesCache
}

async function loadHlCoins(dex) {
  const cacheKey = `hyperliquid:${dex}`
  if (instrumentCache[cacheKey]) return instrumentCache[cacheKey]
  const r = await fetch(`/api/instruments/hyperliquid/coins?dex=${encodeURIComponent(dex)}`)
  const data = await r.json()
  const items = data.ok ? data.items : []
  instrumentCache[cacheKey] = items
  return items
}

function fillDatalist(datalistEl, items) {
  datalistEl.innerHTML = items.map(v => `<option value="${v}"></option>`).join('')
}

const assetDraft = { okx: null, binance: null, hyperliquid_dex: null, hyperliquid_coin: null }

function renderTickerChips() {
  const el = document.getElementById('af-ticker-chips')
  if (!el) return
  const chips = []
  if (assetDraft.okx) chips.push({ label: 'OKX', value: assetDraft.okx, clear: () => { assetDraft.okx = null } })
  if (assetDraft.binance) chips.push({ label: 'Binance', value: assetDraft.binance, clear: () => { assetDraft.binance = null } })
  if (assetDraft.hyperliquid_coin) chips.push({
    label: 'Hyperliquid', value: assetDraft.hyperliquid_coin,
    clear: () => { assetDraft.hyperliquid_dex = null; assetDraft.hyperliquid_coin = null },
  })
  if (!chips.length) {
    el.innerHTML = '<span class="ticker-chip-empty">Пока не добавлено ни одного тикера</span>'
    return
  }
  el.innerHTML = chips.map((c, i) => `
    <span class="ticker-chip">${c.label}: <code>${c.value}</code> <button type="button" data-idx="${i}">✕</button></span>
  `).join('')
  el.querySelectorAll('button[data-idx]').forEach((btn) => {
    const i = parseInt(btn.dataset.idx, 10)
    btn.addEventListener('click', () => { chips[i].clear(); renderTickerChips() })
  })
}

document.addEventListener('DOMContentLoaded', () => {
  loadSyncStatus()

  const exchangeSelect = document.getElementById('af-exchange')
  if (!exchangeSelect) return

  const hlDexWrap = document.getElementById('af-hl-dex-wrap')
  const hlDexInput = document.getElementById('af-hl-dex')
  const hlDexDatalist = document.getElementById('af-hl-dex-list')
  const instrumentInput = document.getElementById('af-instrument')
  const instrumentDatalist = document.getElementById('af-instrument-list')

  async function onExchangeChange() {
    instrumentInput.value = ''
    hlDexInput.value = ''
    if (exchangeSelect.value === 'hyperliquid') {
      hlDexWrap.style.display = ''
      instrumentInput.disabled = true
      instrumentInput.placeholder = 'сначала выбери dex'
      fillDatalist(instrumentDatalist, [])
      const dexes = await loadHlDexes()
      hlDexDatalist.innerHTML = dexes.map(d => `<option value="${d.value}">${d.label}</option>`).join('')
    } else {
      hlDexWrap.style.display = 'none'
      instrumentInput.disabled = false
      instrumentInput.placeholder = 'начни вводить тикер...'
      const items = await loadInstrumentList(exchangeSelect.value)
      fillDatalist(instrumentDatalist, items)
    }
  }
  exchangeSelect.addEventListener('change', onExchangeChange)

  hlDexInput.addEventListener('input', async () => {
    const dexes = await loadHlDexes()
    const match = dexes.find(d => d.value === hlDexInput.value)
    if (!match) return
    instrumentInput.disabled = false
    instrumentInput.value = ''
    instrumentInput.placeholder = 'начни вводить монету...'
    const coins = await loadHlCoins(match.value)
    fillDatalist(instrumentDatalist, coins)
  })

  document.getElementById('af-add-ticker-btn').addEventListener('click', () => {
    const ex = exchangeSelect.value
    const value = instrumentInput.value.trim()
    if (!value) { alert('Выбери инструмент из списка'); return }
    if (ex === 'hyperliquid') {
      const dex = hlDexInput.value.trim()
      if (!dex) { alert('Сначала выбери dex'); return }
      assetDraft.hyperliquid_dex = dex
      assetDraft.hyperliquid_coin = value
    } else {
      assetDraft[ex] = value
    }
    renderTickerChips()
    instrumentInput.value = ''
  })

  document.getElementById('af-save-btn').addEventListener('click', async () => {
    const statusEl = document.getElementById('asset-form-status')
    const key = document.getElementById('af-key').value.trim()
    const label = document.getElementById('af-label').value.trim()
    if (!key || !label) {
      statusEl.textContent = '❌ Заполни ключ и название актива'
      return
    }
    if (!assetDraft.okx && !assetDraft.binance && !assetDraft.hyperliquid_coin) {
      statusEl.textContent = '❌ Добавь хотя бы один тикер (кнопка «+ Добавить тикер»)'
      return
    }
    const payload = { key, label, ...assetDraft }
    statusEl.textContent = '⏳ Проверяю тикеры на биржах...'
    try {
      const r = await fetch('/api/assets', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      const data = await r.json()
      if (!data.ok) {
        statusEl.textContent = '❌ ' + (data.error || 'неизвестная ошибка')
        return
      }
      statusEl.textContent = `✅ Актив ${data.asset} добавлен, перезагружаю страницу...`
      setTimeout(() => location.reload(), 700)
    } catch (e) {
      statusEl.textContent = '❌ Ошибка сети: ' + e.message
    }
  })

  renderTickerChips()
  onExchangeChange()
})

async function syncAsset(asset) {
  const el = document.getElementById('status-' + asset)
  el.textContent = '⏳ Собираю фандинг и цены с бирж... это может занять 20-40 секунд'

  try {
    const r = await fetch('/api/sync/' + asset, { method: 'POST' })
    const data = await r.json()
    if (!data.ok) {
      el.textContent = '❌ Ошибка: ' + (data.error || 'неизвестная ошибка')
      console.error(data.detail || data.error)
      return
    }
    el.textContent = `✅ Готово! Фандинг: ${data.received} (нов. ${data.new}), цены: ${data.price_received} (нов. ${data.price_new})`
  } catch (e) {
    el.textContent = '❌ Ошибка сети: ' + e.message
  }
}

async function viewAsset(asset) {
  const viewer = document.getElementById('viewer')
  const title  = document.getElementById('viewer-title')
  const tbody  = document.getElementById('trows')

  viewer.style.display = 'block'
  title.textContent = 'Загрузка...'
  tbody.innerHTML = '<tr><td colspan="7">⏳ Загружаю...</td></tr>'
  viewer.scrollIntoView({ behavior: 'smooth' })

  try {
    const r = await fetch('/api/history/' + asset)
    const data = await r.json()

    if (!data.ok) {
      title.textContent = 'Ошибка'
      tbody.innerHTML = `<tr><td colspan="7">❌ ${data.error}</td></tr>`
      return
    }

    title.textContent = asset + ' — ' + data.count + ' строк'

    if (!data.rows || !data.rows.length) {
      tbody.innerHTML = '<tr><td colspan="7">Данных нет. Сначала нажми «Собрать историю».</td></tr>'
      return
    }

    const rows = data.rows.slice(-500).reverse()
    tbody.innerHTML = rows.map(row => {
      const dt = new Date(row.funding_time).toLocaleString('ru-RU')
      const rate = (row.funding_rate * 100).toFixed(5) + '%'
      const color = row.exchange === 'binance' ? '#f0b90b'
                  : row.exchange === 'okx'     ? '#00b4d8'
                  :                              '#a78bfa'
      return `<tr>
        <td style="color:${color};font-weight:600">${row.exchange}</td>
        <td>${row.asset}</td>
        <td><code style="color:#3fb950">${row.symbol}</code></td>
        <td>${dt}</td>
        <td>${rate}</td>
        <td>${row.mark_price ?? '—'}</td>
        <td>${row.premium ?? '—'}</td>
      </tr>`
    }).join('')
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="7">❌ Ошибка: ${e.message}</td></tr>`
  }
}
