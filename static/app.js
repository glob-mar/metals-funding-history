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

const assetDraft = { okx: null, binance: null, hyperliquid_dex: null, hyperliquid_coin: null, vantage: null }

// Ключ и название актива больше не вводятся руками — выводятся из первого
// добавленного тикера (пользователь путался, зачем вообще эти два поля,
// когда всё уже понятно из выбранной биржи и инструмента).
function deriveDraftKey() {
  if (assetDraft.okx) return assetDraft.okx.replace(/-USDT-SWAP$/, '')
  if (assetDraft.binance) return assetDraft.binance.replace(/USDT$/, '')
  if (assetDraft.hyperliquid_coin) {
    const i = assetDraft.hyperliquid_coin.indexOf(':')
    return i >= 0 ? assetDraft.hyperliquid_coin.slice(i + 1) : assetDraft.hyperliquid_coin
  }
  if (assetDraft.vantage) return assetDraft.vantage.replace(/USD$/, '')
  return null
}

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
  if (assetDraft.vantage) chips.push({ label: 'Vantage', value: assetDraft.vantage, clear: () => { assetDraft.vantage = null } })

  const keyEl = document.getElementById('af-derived-key')
  if (keyEl) {
    const key = deriveDraftKey()
    keyEl.textContent = key ? `Актив: ${key}` : 'Актив появится здесь после первого добавленного тикера.'
  }

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
  const hlDexSelect = document.getElementById('af-hl-dex')
  const instrumentInput = document.getElementById('af-instrument')
  const instrumentDatalist = document.getElementById('af-instrument-list')

  async function onHlDexChange() {
    instrumentInput.disabled = false
    instrumentInput.value = ''
    instrumentInput.placeholder = 'начни вводить монету...'
    const coins = await loadHlCoins(hlDexSelect.value)
    fillDatalist(instrumentDatalist, coins)
  }
  hlDexSelect.addEventListener('change', onHlDexChange)

  async function onExchangeChange() {
    instrumentInput.value = ''
    if (exchangeSelect.value === 'hyperliquid') {
      hlDexWrap.style.display = ''
      instrumentInput.disabled = true
      instrumentInput.placeholder = 'сначала выбери dex'
      fillDatalist(instrumentDatalist, [])
      const dexes = await loadHlDexes()
      hlDexSelect.innerHTML = dexes.map(d => `<option value="${d.value}">${d.label}</option>`).join('')
      // Dex по умолчанию (нативный) сразу подгружает список монет — не
      // заставляем пользователя ещё и явно трогать это поле, если ему
      // нужен именно нативный dex (самый частый случай, напр. ETH/BTC).
      await onHlDexChange()
    } else {
      hlDexWrap.style.display = 'none'
      instrumentInput.disabled = false
      instrumentInput.placeholder = exchangeSelect.value === 'vantage'
        ? 'начни вводить тикер (список со времени последнего запуска скрипта в терминале)...'
        : 'начни вводить тикер...'
      const items = await loadInstrumentList(exchangeSelect.value)
      fillDatalist(instrumentDatalist, items)
    }
  }
  exchangeSelect.addEventListener('change', onExchangeChange)

  document.getElementById('af-add-ticker-btn').addEventListener('click', () => {
    const ex = exchangeSelect.value
    const value = instrumentInput.value.trim()
    if (!value) { alert('Выбери инструмент из списка'); return }
    if (ex === 'hyperliquid') {
      assetDraft.hyperliquid_dex = hlDexSelect.value
      assetDraft.hyperliquid_coin = value
    } else {
      assetDraft[ex] = value
    }
    renderTickerChips()
    instrumentInput.value = ''
  })

  // Общая логика сохранения (Блок 33) — раньше жила только в обработчике
  // af-save-btn и всегда перезагружала страницу. Вынесена отдельно, чтобы
  // «Сохранить и добавить ещё» могла переиспользовать ту же проверку/запрос,
  // но без перезагрузки — иначе выбор второго тикера с той же биржи до
  // сохранения затирал первый в assetDraft (там один слот на биржу), и
  // добавить несколько разных активов подряд можно было только по одному,
  // каждый раз пересобирая форму с нуля после reload.
  async function saveDraftAsset(statusEl) {
    const key = deriveDraftKey()
    if (!key) {
      statusEl.textContent = '❌ Добавь хотя бы один тикер (кнопка «+ Добавить тикер»)'
      return null
    }
    const payload = { key, label: key, ...assetDraft }
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
        return null
      }
      return data.asset
    } catch (e) {
      statusEl.textContent = '❌ Ошибка сети: ' + e.message
      return null
    }
  }

  function resetDraftForm() {
    assetDraft.okx = null; assetDraft.binance = null
    assetDraft.hyperliquid_dex = null; assetDraft.hyperliquid_coin = null
    assetDraft.vantage = null
    instrumentInput.value = ''
    renderTickerChips()
  }

  document.getElementById('af-save-btn').addEventListener('click', async () => {
    const statusEl = document.getElementById('asset-form-status')
    const asset = await saveDraftAsset(statusEl)
    if (!asset) return
    statusEl.textContent = `✅ Актив ${asset} добавлен, перезагружаю страницу...`
    setTimeout(() => location.reload(), 700)
  })

  document.getElementById('af-save-continue-btn').addEventListener('click', async () => {
    const statusEl = document.getElementById('asset-form-status')
    const asset = await saveDraftAsset(statusEl)
    if (!asset) return
    statusEl.textContent = ''
    const logEl = document.getElementById('af-session-log')
    const reloadRow = logEl.querySelector('.af-log-reload')
    if (reloadRow) reloadRow.remove()
    logEl.insertAdjacentHTML('beforeend', `<div class="af-log-row ok">✅ ${asset} добавлен</div>`)
    logEl.insertAdjacentHTML('beforeend',
      '<div class="af-log-row af-log-reload">Добавленные тут активы появятся в списке выше и в пикерах после обновления страницы — <a href="#" id="af-reload-link">обновить сейчас</a>, когда закончишь добавлять.</div>')
    document.getElementById('af-reload-link').addEventListener('click', (e) => { e.preventDefault(); location.reload() })
    resetDraftForm()
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
