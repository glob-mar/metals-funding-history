from io import StringIO
import csv
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
        return JSONResponse({'error': 'Неизвестный актив'}, status_code=404)
    rows = await collect(asset)
    inserted = await upsert_rows(rows)
    return {'asset': asset, 'received': len(rows), 'new': inserted}


@app.get('/api/history/{asset}')
async def history(asset: str):
    asset = asset.upper()
    if asset not in ASSETS:
        return JSONResponse({'error': 'Неизвестный актив'}, status_code=404)
    rows = await get_history(asset)
    return {'asset': asset, 'count': len(rows), 'rows': rows}


@app.get('/api/export/{asset}.csv')
async def export(asset: str):
    asset = asset.upper()
    if asset not in ASSETS:
        return JSONResponse({'error': 'Неизвестный актив'}, status_code=404)
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
