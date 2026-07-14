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

document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('asset-form')
  if (!form) return
  form.addEventListener('submit', async (e) => {
    e.preventDefault()
    const statusEl = document.getElementById('asset-form-status')
    const payload = {
      key: document.getElementById('af-key').value,
      label: document.getElementById('af-label').value,
      okx: document.getElementById('af-okx').value,
      binance: document.getElementById('af-binance').value,
      hyperliquid_dex: document.getElementById('af-hl-dex').value,
      hyperliquid_coin: document.getElementById('af-hl-coin').value,
    }
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
