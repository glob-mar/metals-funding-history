from datetime import datetime, timezone
import httpx
from .config import ASSETS

OKX_URL = 'https://www.okx.com/api/v5/public/funding-rate-history'
HL_URL  = 'https://api.hyperliquid.xyz/info'

HL_CHUNK_MS = 7 * 24 * 3600 * 1000  # 7 дней в миллисекундах


def _year_start_ms() -> int:
    return int(datetime(datetime.now().year, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def _now_ms() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


async def _okx(client: httpx.AsyncClient, asset: str) -> list[dict]:
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
    Собирает funding rate с Hyperliquid HIP-3 чунками по 7 дней,
    чтобы обойти лимит на количество записей в одном запросе.
    """
    dex  = ASSETS[asset]['hyperliquid_dex']
    coin = ASSETS[asset]['hyperliquid_coin']
    rows = []
    seen = set()

    start = _year_start_ms()
    end   = _now_ms()
    chunk_start = start
    chunk_num = 0

    while chunk_start < end:
        chunk_end = min(chunk_start + HL_CHUNK_MS, end)
        chunk_num += 1
        try:
            r = await client.post(HL_URL,
                json={
                    'type': 'fundingHistory',
                    'coin': coin,
                    'dex': dex,
                    'startTime': chunk_start,
                    'endTime': chunk_end,
                },
                timeout=20.0)
            if r.status_code != 200:
                print(f'Hyperliquid chunk {chunk_num}: HTTP {r.status_code}')
                chunk_start = chunk_end + 1
                continue
            data = r.json()
            if not isinstance(data, list):
                chunk_start = chunk_end + 1
                continue
            new_in_chunk = 0
            for d in data:
                key = int(d['time'])
                if key in seen:
                    continue
                seen.add(key)
                new_in_chunk += 1
                rows.append({
                    'exchange': 'hyperliquid',
                    'asset': asset,
                    'symbol': coin,
                    'funding_time': key,
                    'funding_rate': float(d['fundingRate']),
                    'mark_price': None,
                    'premium': float(d['premium']) if d.get('premium') not in (None, '') else None,
                })
            print(f'Hyperliquid {coin} chunk {chunk_num}: +{new_in_chunk}, total: {len(rows)}')
        except Exception as e:
            print(f'Hyperliquid {coin} chunk {chunk_num}: {e}')
        chunk_start = chunk_end + 1

    rows.sort(key=lambda x: x['funding_time'])
    print(f'Hyperliquid {coin}: DONE, {len(rows)} rows total')
    return rows


async def collect(asset: str) -> list[dict]:
    timeout = httpx.Timeout(120.0)
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
