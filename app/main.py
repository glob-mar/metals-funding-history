from io import StringIO
import csv
import traceback
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from .config import ASSETS
from .db import init_db, upsert_rows, get_history
from .services import collect


def ms_to_dt(ms: int) -> str:
    """Unix ms → 'YYYY-MM-DD HH:MM:SS UTC'"""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title='Metals Funding History', lifespan=lifespan)
app.mount('/static', StaticFiles(directory='static'), name='static')
templates = Jinja2Templates(directory='templates')


@app.get('/', response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        'index.html', {'request': request, 'assets': ASSETS}
    )


@app.post('/api/sync/{asset}')
async def sync(asset: str):
    asset = asset.upper()
    if asset not in ASSETS:
        return JSONResponse({'ok': False, 'error': 'Неизвестный актив'}, status_code=404)
    try:
        rows = await collect(asset)
        inserted = await upsert_rows(rows)
        return JSONResponse({'ok': True, 'asset': asset, 'received': len(rows), 'new': inserted})
    except Exception as e:
        tb = traceback.format_exc()
        print(tb)
        return JSONResponse({'ok': False, 'error': str(e), 'detail': tb}, status_code=500)


@app.get('/api/history/{asset}')
async def history(asset: str):
    asset = asset.upper()
    if asset not in ASSETS:
        return JSONResponse({'ok': False, 'error': 'Неизвестный актив'}, status_code=404)
    try:
        rows = await get_history(asset)
        return JSONResponse({'ok': True, 'asset': asset, 'count': len(rows), 'rows': rows})
    except Exception as e:
        tb = traceback.format_exc()
        print(tb)
        return JSONResponse({'ok': False, 'error': str(e)}, status_code=500)


@app.get('/api/export/{asset}.csv')
async def export(asset: str):
    asset = asset.upper()
    if asset not in ASSETS:
        return JSONResponse({'ok': False, 'error': 'Неизвестный актив'}, status_code=404)
    try:
        rows = await get_history(asset)
        buf = StringIO()
        fieldnames = [
            'exchange', 'asset', 'symbol',
            'datetime_utc',       # читаемая дата
            'funding_time_ms',    # рав unix ms (для сортировки)
            'funding_rate',       # десятичное (напр. 0.00000625)
            'funding_rate_pct',   # в процентах (напр. 0.000625%)
            'mark_price',
            'premium',
        ]
        w = csv.DictWriter(buf, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            fr = row['funding_rate'] if row['funding_rate'] is not None else 0.0
            w.writerow({
                'exchange':         row['exchange'],
                'asset':            row['asset'],
                'symbol':           row['symbol'],
                'datetime_utc':     ms_to_dt(row['funding_time']),
                'funding_time_ms':  row['funding_time'],
                'funding_rate':     f"{fr:.10f}",
                'funding_rate_pct': f"{fr * 100:.8f}%",
                'mark_price':       row['mark_price'] if row['mark_price'] is not None else '',
                'premium':          f"{row['premium']:.10f}" if row['premium'] is not None else '',
            })
        buf.seek(0)
        fname = f'{asset.lower()}_funding_history.csv'
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type='text/csv; charset=utf-8',
            headers={'Content-Disposition': f'attachment; filename={fname}'}
        )
    except Exception as e:
        return JSONResponse({'ok': False, 'error': str(e)}, status_code=500)


@app.get('/api/debug/{asset}')
async def debug(asset: str):
    asset = asset.upper()
    if asset not in ASSETS:
        return JSONResponse({'error': 'Неизвестный актив'}, status_code=404)
    import httpx as hx
    results = {}
    now = datetime.now(tz=timezone.utc)
    start_ms = int(datetime(now.year, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    dex  = ASSETS[asset]['hyperliquid_dex']
    coin = ASSETS[asset]['hyperliquid_coin']

    try:
        async with hx.AsyncClient(timeout=15.0) as c:
            r = await c.get('https://www.okx.com/api/v5/public/funding-rate-history',
                            params={'instId': ASSETS[asset]['okx'], 'limit': 3})
            results['okx'] = {'status': r.status_code, 'sample': r.json().get('data', [])[:2]}
    except Exception as e:
        results['okx'] = {'error': str(e)}

    try:
        async with hx.AsyncClient(timeout=15.0) as c:
            rm = await c.post('https://api.hyperliquid.xyz/info',
                              json={'type': 'meta', 'dex': dex})
            universe = rm.json().get('universe', []) if rm.status_code == 200 else []
            names = [u.get('name', '') for u in universe]
            results['hyperliquid_meta'] = {'dex': dex, 'status': rm.status_code,
                                           'total_coins': len(names), 'all_coins': names}
    except Exception as e:
        results['hyperliquid_meta'] = {'error': str(e)}

    try:
        async with hx.AsyncClient(timeout=15.0) as c:
            r = await c.post('https://api.hyperliquid.xyz/info',
                             json={'type': 'fundingHistory', 'coin': coin, 'dex': dex,
                                   'startTime': start_ms, 'endTime': start_ms + 86400000 * 7})
            results['hyperliquid_funding'] = {
                'coin': coin, 'dex': dex, 'status': r.status_code,
                'sample': r.json()[:3] if r.status_code == 200 and isinstance(r.json(), list) else r.text[:300]
            }
    except Exception as e:
        results['hyperliquid_funding'] = {'error': str(e)}

    return JSONResponse(results)
