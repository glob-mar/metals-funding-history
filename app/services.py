from datetime import datetime, timezone
import httpx
from .config import ASSETS

OKX_URL = 'https://www.okx.com/api/v5/public/funding-rate-history'
HL_URL  = 'https://api.hyperliquid.xyz/info'


def _year_start_ms() -> int:
    return int(datetime(datetime.now().year, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def _now_ms() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


async def _okx(client: httpx.AsyncClient, asset: str) -> list[dict]:
    """Собирает всю историю funding rate на OKX за текущий год через пагинацию."""
    inst = ASSETS[asset]['okx']
    rows = []
    limit_ts = _year_start_ms()
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
        print(f'OKX {inst} page {page}: {len(data)} records, total: {len(rows)}')
        if stop or len(data) < 100:
            break
        after = min(int(d['fundingTime']) for d in data)
    rows.sort(key=lambda x: x['funding_time'])
    return rows


async def _hyperliquid(client: httpx.AsyncClient, asset: str) -> list[dict]:
    """
    Собирает funding rate с Hyperliquid HIP-3 (TradeXYZ).
    HIP-3 рынки требуют параметр dex=xyz и тикер в формате xyz:GOLD.
    """
    dex  = ASSETS[asset]['hyperliquid_dex']
    coin = ASSETS[asset]['hyperliquid_coin']
    start = _year_start_ms()
    end   = _now_ms()
    rows  = []

    # Сначала проверяем что рынок существует через meta
    try:
        rm = await client.post(HL_URL,
            json={'type': 'meta', 'dex': dex},
            timeout=15.0)
        if rm.status_code == 200:
            universe = rm.json().get('universe', [])
            # coin например  'xyz:GOLD', в universe name может быть без префикса
            names = [u.get('name', '') for u in universe]
            coin_short = coin.split(':')[-1]  # GOLD
            found = next((n for n in names if n.upper() == coin.upper() or n.upper() == coin_short.upper()), None)
            if not found:
                print(f'Hyperliquid: тикер {coin} не найден в dex={dex}, доступные: {names[:10]}')
                # Пробуем всё равно с тем что есть
            else:
                coin = found  # используем точное название из API
                print(f'Hyperliquid: тикер найден: {coin}')
    except Exception as e:
        print(f'Hyperliquid meta error: {e}')

    try:
        r = await client.post(HL_URL,
            json={
                'type': 'fundingHistory',
                'coin': coin,
                'dex': dex,
                'startTime': start,
                'endTime': end,
            },
            timeout=30.0)
        if r.status_code != 200:
            print(f'Hyperliquid {coin}: HTTP {r.status_code} {r.text[:300]}')
            return rows
        data = r.json()
        if not isinstance(data, list) or not data:
            print(f'Hyperliquid {coin}: пустой ответ ({data})')
            return rows
        for d in data:
            rows.append({
                'exchange': 'hyperliquid',
                'asset': asset,
                'symbol': coin,
                'funding_time': int(d['time']),
                'funding_rate': float(d['fundingRate']),
                'mark_price': None,
                'premium': float(d['premium']) if d.get('premium') not in (None, '') else None,
            })
        print(f'Hyperliquid {coin}: OK, {len(rows)} rows')
    except Exception as e:
        print(f'Hyperliquid {coin}: {e}')
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
        try:
            rows += await _hyperliquid(c, asset)
        except Exception as e:
            print(f'Hyperliquid error ({asset}): {e}')
        return rows
