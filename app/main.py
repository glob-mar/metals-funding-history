from io import StringIO
import csv
import traceback
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from .config import ASSETS, BINANCE_VISION_BASE
from .db import init_db, upsert_rows, get_history
from .services import collect


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
        w = csv.DictWriter(buf, fieldnames=[
            'exchange', 'asset', 'symbol',
            'funding_time', 'funding_rate', 'mark_price', 'premium'
        ])
        w.writeheader()
        for row in rows:
            w.writerow(row)
        buf.seek(0)
        fname = f'{asset.lower()}_funding_history.csv'
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type='text/csv',
            headers={'Content-Disposition': f'attachment; filename={fname}'}
        )
    except Exception as e:
        return JSONResponse({'ok': False, 'error': str(e)}, status_code=500)


@app.get('/api/debug/{asset}')
async def debug(asset: str):
    asset = asset.upper()
    if asset not in ASSETS:
        return JSONResponse({'error': 'Неизвестный актив'}, status_code=404)
    import httpx
    results = {}
    now = datetime.now(tz=timezone.utc)
    year, month = now.year, now.month
    start_ms = int(datetime(now.year, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)

    # Binance Vision — берём предыдущий месяц
    prev_month = month - 1 if month > 1 else 12
    prev_year  = year if month > 1 else year - 1
    symbol_b = ASSETS[asset]['binance']
    fname = f'{symbol_b}-fundingRate-{prev_year}-{prev_month:02d}.zip'
    url_b = f'{BINANCE_VISION_BASE}/{symbol_b}/{fname}'
    try:
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.get(url_b)
            results['binance_vision'] = {'url': url_b, 'status': r.status_code, 'size_bytes': len(r.content)}
    except Exception as e:
        results['binance_vision'] = {'error': str(e)}

    # OKX
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get('https://www.okx.com/api/v5/public/funding-rate-history',
                            params={'instId': ASSETS[asset]['okx'], 'limit': 3})
            results['okx'] = {'status': r.status_code, 'sample': r.json().get('data', [])[:2]}
    except Exception as e:
        results['okx'] = {'error': str(e)}

    # Hyperliquid — meta + fundingHistory
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            rm = await c.post('https://api.hyperliquid.xyz/info', json={'type': 'meta'})
            universe = rm.json().get('universe', []) if rm.status_code == 200 else []
            all_names = [u['name'] for u in universe]
            # ищем тикер
            candidates = ['GOLD','XAU','SILVER','XAG','PLATINUM','XPT','PALLADIUM','XPD']
            found_metals = [n for n in all_names if n.upper() in candidates or any(c in n.upper() for c in candidates)]
            results['hyperliquid_meta'] = {
                'total_coins': len(all_names),
                'found_metals': found_metals[:20]
            }
    except Exception as e:
        results['hyperliquid_meta'] = {'error': str(e)}

    return JSONResponse(results)
