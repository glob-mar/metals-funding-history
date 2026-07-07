from io import StringIO
import csv
import traceback
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from .config import ASSETS
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
    """Диагностика: показывает что именно падает при сборе данных"""
    asset = asset.upper()
    if asset not in ASSETS:
        return JSONResponse({'error': 'Неизвестный актив'}, status_code=404)
    import httpx
    from .config import ASSETS as A
    results = {}

    # Binance
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get('https://fapi.binance.com/fapi/v1/fundingRate',
                            params={'symbol': A[asset]['binance'], 'limit': 5})
            results['binance'] = {'status': r.status_code, 'sample': r.json()[:2] if r.status_code == 200 else r.text[:200]}
    except Exception as e:
        results['binance'] = {'error': str(e)}

    # OKX
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get('https://www.okx.com/api/v5/public/funding-rate-history',
                            params={'instId': A[asset]['okx'], 'limit': 5})
            data = r.json()
            results['okx'] = {'status': r.status_code, 'sample': data.get('data', [])[:2] if r.status_code == 200 else r.text[:200]}
    except Exception as e:
        results['okx'] = {'error': str(e)}

    # Hyperliquid
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post('https://api.hyperliquid.xyz/info',
                             json={'type': 'fundingHistory', 'coin': A[asset]['hyperliquid'],
                                   'startTime': 1751000000000, 'endTime': 1751400000000})
            results['hyperliquid'] = {'status': r.status_code, 'sample': r.json()[:2] if r.status_code == 200 else r.text[:200]}
    except Exception as e:
        results['hyperliquid'] = {'error': str(e)}

    return JSONResponse(results)
