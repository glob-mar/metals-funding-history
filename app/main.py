from io import StringIO
import csv
import traceback
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from .config import ASSETS, PERIODS_PER_YEAR
from .db import init_db, upsert_rows, get_history
from .services import collect


def ms_to_dt(ms: int) -> str:
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
async def history(asset: str, exchange: Optional[str] = Query(None)):
    asset = asset.upper()
    if asset not in ASSETS:
        return JSONResponse({'ok': False, 'error': 'Неизвестный актив'}, status_code=404)
    try:
        rows = await get_history(asset)
        if exchange:
            rows = [r for r in rows if r['exchange'] == exchange.lower()]
        return JSONResponse({'ok': True, 'asset': asset, 'count': len(rows), 'rows': rows})
    except Exception as e:
        print(traceback.format_exc())
        return JSONResponse({'ok': False, 'error': str(e)}, status_code=500)


@app.get('/api/stats/{asset}')
async def stats(asset: str):
    asset = asset.upper()
    if asset not in ASSETS:
        return JSONResponse({'ok': False, 'error': 'Неизвестный актив'}, status_code=404)
    try:
        rows = await get_history(asset)
        if not rows:
            return JSONResponse({'ok': True, 'asset': asset,
                                 'message': 'Нет данных. Нажми «Собрать» сначала.'})
        from collections import defaultdict
        by_exchange: dict = defaultdict(list)
        for r in rows:
            by_exchange[r['exchange']].append(r['funding_rate'])
        result = {'ok': True, 'asset': asset, 'total_rows': len(rows), 'by_exchange': {}}
        for exchange, rates in by_exchange.items():
            n = len(rates)
            avg = sum(rates) / n
            ppy = PERIODS_PER_YEAR.get(exchange, 8760)
            annual = avg * ppy * 100
            pos = sum(1 for r in rates if r > 0)
            neg = sum(1 for r in rates if r < 0)
            result['by_exchange'][exchange] = {
                'periods':          n,
                'periods_per_year': ppy,
                'avg_rate_pct':     f'{avg * 100:.6f}%',
                'annualized_yield': f'{annual:.4f}%',
                'max_rate_pct':     f'{max(rates) * 100:.6f}%',
                'min_rate_pct':     f'{min(rates) * 100:.6f}%',
                'positive_periods': pos,
                'negative_periods': neg,
                'pct_positive':     f'{pos / n * 100:.1f}%',
            }
        return JSONResponse(result)
    except Exception as e:
        print(traceback.format_exc())
        return JSONResponse({'ok': False, 'error': str(e)}, status_code=500)


@app.get('/api/export/{asset}.csv')
async def export(asset: str, exchange: Optional[str] = Query(None)):
    asset = asset.upper()
    if asset not in ASSETS:
        return JSONResponse({'ok': False, 'error': 'Неизвестный актив'}, status_code=404)
    try:
        rows = await get_history(asset)
        if exchange:
            rows = [r for r in rows if r['exchange'] == exchange.lower()]
        buf = StringIO()
        fieldnames = [
            'exchange', 'asset', 'symbol',
            'datetime_utc', 'funding_time_ms',
            'funding_rate', 'funding_rate_pct',
            'mark_price', 'premium',
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
                'funding_rate':     f'{fr:.10f}',
                'funding_rate_pct': f'{fr * 100:.8f}%',
                'mark_price':       row['mark_price'] if row['mark_price'] is not None else '',
                'premium':          f"{row['premium']:.10f}" if row['premium'] is not None else '',
            })
        buf.seek(0)
        suffix = f'_{exchange}' if exchange else ''
        fname = f'{asset.lower()}_funding{suffix}.csv'
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
    cfg = ASSETS[asset]

    # OKX
    if cfg.get('okx'):
        try:
            async with hx.AsyncClient(timeout=15.0) as c:
                r = await c.get('https://www.okx.com/api/v5/public/funding-rate-history',
                                params={'instId': cfg['okx'], 'limit': 2})
                results['okx'] = {'status': r.status_code, 'instId': cfg['okx'],
                                  'sample': r.json().get('data', [])[:2]}
        except Exception as e:
            results['okx'] = {'error': str(e)}

    # Binance
    if cfg.get('binance'):
        try:
            async with hx.AsyncClient(timeout=15.0) as c:
                r = await c.get('https://fapi.binance.com/fapi/v1/fundingRate',
                                params={'symbol': cfg['binance'], 'limit': 2,
                                        'startTime': start_ms})
                results['binance'] = {'status': r.status_code, 'symbol': cfg['binance'],
                                      'sample': r.json()[:2] if r.status_code == 200 else r.text[:200]}
        except Exception as e:
            results['binance'] = {'error': str(e)}

    # Hyperliquid
    try:
        async with hx.AsyncClient(timeout=15.0) as c:
            r = await c.post('https://api.hyperliquid.xyz/info',
                             json={'type': 'fundingHistory',
                                   'coin': cfg['hyperliquid_coin'],
                                   'dex': cfg['hyperliquid_dex'],
                                   'startTime': start_ms,
                                   'endTime': start_ms + 86400000 * 3})
            results['hyperliquid'] = {
                'coin': cfg['hyperliquid_coin'], 'status': r.status_code,
                'sample': r.json()[:3] if r.status_code == 200 and isinstance(r.json(), list) else r.text[:300]
            }
    except Exception as e:
        results['hyperliquid'] = {'error': str(e)}

    return JSONResponse(results)
