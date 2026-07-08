from io import StringIO, BytesIO
import csv
import traceback
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional
from collections import defaultdict
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
async def export_csv(asset: str, exchange: Optional[str] = Query(None)):
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


@app.get('/api/export/{asset}.xlsx')
async def export_xlsx(asset: str, exchange: Optional[str] = Query(None)):
    """Excel-выгрузка с тремя листами:
       Raw — сырые данные
       Annual — годовая доходность по биржам
       Monthly — помесячная доходность по биржам
    """
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    asset = asset.upper()
    if asset not in ASSETS:
        return JSONResponse({'ok': False, 'error': 'Неизвестный актив'}, status_code=404)
    try:
        rows = await get_history(asset)
        if exchange:
            rows = [r for r in rows if r['exchange'] == exchange.lower()]

        wb = openpyxl.Workbook()

        # ===== Лист 1: Raw =====
        ws_raw = wb.active
        ws_raw.title = 'Raw'
        header_fill = PatternFill('solid', fgColor='1F4E79')
        header_font = Font(color='FFFFFF', bold=True)
        raw_headers = [
            'exchange', 'asset', 'symbol', 'datetime_utc',
            'funding_time_ms', 'funding_rate', 'funding_rate_pct',
            'mark_price', 'premium'
        ]
        for col, h in enumerate(raw_headers, 1):
            cell = ws_raw.cell(row=1, column=col, value=h)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal='center')

        for row in rows:
            fr = row['funding_rate'] if row['funding_rate'] is not None else 0.0
            ws_raw.append([
                row['exchange'],
                row['asset'],
                row['symbol'],
                ms_to_dt(row['funding_time']),
                row['funding_time'],
                round(fr, 10),
                round(fr * 100, 8),
                row['mark_price'] if row['mark_price'] is not None else None,
                round(row['premium'], 10) if row['premium'] is not None else None,
            ])

        for col in range(1, len(raw_headers) + 1):
            ws_raw.column_dimensions[get_column_letter(col)].width = 20

        # ===== Лист 2: Annual =====
        ws_annual = wb.create_sheet('Annual')
        annual_headers = [
            'exchange', 'periods', 'date_from', 'date_to',
            'days_observed', 'avg_rate_pct', 'annualized_yield_pct',
            'max_rate_pct', 'min_rate_pct', 'pct_positive'
        ]
        for col, h in enumerate(annual_headers, 1):
            cell = ws_annual.cell(row=1, column=col, value=h)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal='center')

        by_exchange: dict = defaultdict(list)
        by_exchange_ts: dict = defaultdict(list)
        for r in rows:
            ex = r['exchange']
            fr = r['funding_rate'] if r['funding_rate'] is not None else 0.0
            ts = r['funding_time']
            by_exchange[ex].append(fr)
            by_exchange_ts[ex].append(ts)

        for ex, rates in by_exchange.items():
            n = len(rates)
            avg = sum(rates) / n
            timestamps = by_exchange_ts[ex]
            ts_min = min(timestamps)
            ts_max = max(timestamps)
            days = (ts_max - ts_min) / 86400000  # ms -> days
            annual = (sum(rates) * 365 / days * 100) if days > 0 else 0.0
            pos = sum(1 for r in rates if r > 0)
            ws_annual.append([
                ex,
                n,
                ms_to_dt(ts_min),
                ms_to_dt(ts_max),
                round(days, 2),
                round(avg * 100, 6),
                round(annual, 4),
                round(max(rates) * 100, 6),
                round(min(rates) * 100, 6),
                round(pos / n * 100, 1),
            ])

        for col in range(1, len(annual_headers) + 1):
            ws_annual.column_dimensions[get_column_letter(col)].width = 22

        # ===== Лист 3: Monthly =====
        ws_monthly = wb.create_sheet('Monthly')
        monthly_headers = [
            'exchange', 'month', 'periods',
            'sum_funding_rate', 'avg_funding_rate_pct',
            'monthly_yield_pct', 'pct_positive'
        ]
        for col, h in enumerate(monthly_headers, 1):
            cell = ws_monthly.cell(row=1, column=col, value=h)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal='center')

        # Группируем по (exchange, year, month)
        monthly_data: dict = defaultdict(list)
        for r in rows:
            ex = r['exchange']
            fr = r['funding_rate'] if r['funding_rate'] is not None else 0.0
            dt = datetime.fromtimestamp(r['funding_time'] / 1000, tz=timezone.utc)
            key = (ex, dt.year, dt.month)
            monthly_data[key].append(fr)

        for (ex, year, month), rates in sorted(monthly_data.items()):
            n = len(rates)
            s = sum(rates)
            avg = s / n
            pos = sum(1 for r in rates if r > 0)
            ws_monthly.append([
                ex,
                f'{year}-{month:02d}',
                n,
                round(s * 100, 6),
                round(avg * 100, 6),
                round(s * 100, 4),    # суммарный фандинг за месяц (%)
                round(pos / n * 100, 1),
            ])

        for col in range(1, len(monthly_headers) + 1):
            ws_monthly.column_dimensions[get_column_letter(col)].width = 22

        # ===== Выгрузка =====
        buf = BytesIO()
        wb.save(buf)
        buf.seek(0)
        suffix = f'_{exchange}' if exchange else ''
        fname = f'{asset.lower()}_funding{suffix}.xlsx'
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            headers={'Content-Disposition': f'attachment; filename={fname}'}
        )
    except Exception as e:
        print(traceback.format_exc())
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

    if cfg.get('okx'):
        try:
            async with hx.AsyncClient(timeout=15.0) as c:
                r = await c.get('https://www.okx.com/api/v5/public/funding-rate-history',
                                params={'instId': cfg['okx'], 'limit': 2})
                results['okx'] = {'status': r.status_code, 'instId': cfg['okx'],
                                  'sample': r.json().get('data', [])[:2]}
        except Exception as e:
            results['okx'] = {'error': str(e)}

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
