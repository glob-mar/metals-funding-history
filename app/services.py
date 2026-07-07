from datetime import datetime, timezone
import httpx
from .config import ASSETS

OKX_URL = 'https://www.okx.com/api/v5/public/funding-rate-history'


def _year_start_ms() -> int:
    return int(datetime(datetime.now().year, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def _now_ms() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


async def _okx(client: httpx.AsyncClient, asset: str) -> list[dict]:
    """
    Собирает всю историю funding rate на OKX за текущий год.
    OKX возвращает данные от новых к старым (параметр after = перейти дальше в прошлое).
    """
    inst = ASSETS[asset]['okx']
    rows = []
    limit_ts = _year_start_ms()
    # Начинаем с текущего момента, пагинируем назад до начала года
    after = None
    page = 0
    while True:
        params = {'instId': inst, 'limit': 100}
        if after:
            params['after'] = str(after)
        r = await client.get(OKX_URL, params=params, timeout=20.0)
        r.raise_for_status()
        data = r.json().get('data', [])
        if not data:
            break
        page += 1
        stop = False
        for d in data:
            ft = int(d['fundingTime'])
            if ft < limit_ts:
                stop = True
                break
            rows.append({
                'exchange': 'okx',
                'asset': asset,
                'symbol': inst,
                'funding_time': ft,
                'funding_rate': float(d['fundingRate']),
                'mark_price': None,
                'premium': float(d['realizedRate']) if d.get('realizedRate') not in (None, '') else None,
            })
        print(f'OKX {inst} page {page}: {len(data)} records, total so far: {len(rows)}')
        if stop or len(data) < 100:
            break
        # after = минимальный fundingTime из текущей страницы (чтобы идти глубже в прошлое)
        after = min(int(d['fundingTime']) for d in data)

    rows.sort(key=lambda x: x['funding_time'])
    print(f'OKX {inst} total: {len(rows)} rows across {page} pages')
    return rows


async def collect(asset: str) -> list[dict]:
    timeout = httpx.Timeout(60.0)
    headers = {'User-Agent': 'metals-funding-history/1.0'}
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as c:
        rows = []
        try:
            rows += await _okx(c, asset)
        except Exception as e:
            print(f'OKX error ({asset}): {e}')
        return rows
