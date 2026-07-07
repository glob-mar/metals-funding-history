from datetime import datetime, timezone
import httpx
from .config import ASSETS

BINANCE_URL = 'https://fapi.binance.com/fapi/v1/fundingRate'
OKX_URL     = 'https://www.okx.com/api/v5/public/funding-rate-history'
HL_URL      = 'https://api.hyperliquid.xyz/info'


def _year_start_ms() -> int:
    return int(datetime(datetime.now().year, 1, 1,
                        tzinfo=timezone.utc).timestamp() * 1000)


def _now_ms() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


async def _binance(client: httpx.AsyncClient, asset: str) -> list[dict]:
    symbol = ASSETS[asset]['binance']
    start, end = _year_start_ms(), _now_ms()
    rows = []
    while start < end:
        r = await client.get(BINANCE_URL, params={
            'symbol': symbol, 'startTime': start,
            'endTime': end, 'limit': 1000
        })
        r.raise_for_status()
        data = r.json()
        if not data:
            break
        for d in data:
            rows.append({
                'exchange': 'binance', 'asset': asset, 'symbol': symbol,
                'funding_time': int(d['fundingTime']),
                'funding_rate': float(d['fundingRate']),
                'mark_price': float(d['markPrice']) if d.get('markPrice') else None,
                'premium': None,
            })
        nxt = int(data[-1]['fundingTime']) + 1
        if nxt <= start:
            break
        start = nxt
    return rows


async def _okx(client: httpx.AsyncClient, asset: str) -> list[dict]:
    inst = ASSETS[asset]['okx']
    after = None
    rows = []
    limit_ts = _year_start_ms()
    while True:
        params = {'instId': inst, 'limit': 100}
        if after:
            params['after'] = after
        r = await client.get(OKX_URL, params=params)
        r.raise_for_status()
        data = r.json().get('data', [])
        if not data:
            break
        stop = False
        for d in data:
            ft = int(d['fundingTime'])
            if ft < limit_ts:
                stop = True
                break
            rows.append({
                'exchange': 'okx', 'asset': asset, 'symbol': inst,
                'funding_time': ft,
                'funding_rate': float(d['fundingRate']),
                'mark_price': None,
                'premium': float(d['realizedRate']) if d.get('realizedRate') not in (None, '') else None,
            })
        if stop or len(data) < 100:
            break
        after = data[-1]['fundingTime']
    rows.sort(key=lambda x: x['funding_time'])
    return rows


async def _hyperliquid(client: httpx.AsyncClient, asset: str) -> list[dict]:
    coin = ASSETS[asset]['hyperliquid']
    r = await client.post(HL_URL, json={
        'type': 'fundingHistory',
        'coin': coin,
        'startTime': _year_start_ms(),
        'endTime': _now_ms(),
    })
    r.raise_for_status()
    rows = []
    for d in r.json():
        rows.append({
            'exchange': 'hyperliquid', 'asset': asset, 'symbol': coin,
            'funding_time': int(d['time']),
            'funding_rate': float(d['fundingRate']),
            'mark_price': None,
            'premium': float(d['premium']) if d.get('premium') not in (None, '') else None,
        })
    return rows


async def collect(asset: str) -> list[dict]:
    timeout = httpx.Timeout(30.0)
    headers = {'User-Agent': 'metals-funding-history/1.0'}
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as c:
        rows = []
        rows += await _binance(c, asset)
        try:
            rows += await _okx(c, asset)
        except Exception as e:
            print(f'OKX error ({asset}): {e}')
        try:
            rows += await _hyperliquid(c, asset)
        except Exception as e:
            print(f'Hyperliquid error ({asset}): {e}')
        return rows
