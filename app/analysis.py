from collections import defaultdict
from datetime import datetime, timezone
from statistics import pstdev

from .metrics import periods_per_year, interval_label


def ms_to_dt(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def exchange_stats(rows: list[dict]) -> dict:
    """Сводная статистика funding rate для одной пары (биржа, актив).
    rows — записи funding_history с ключами funding_rate/funding_time."""
    rates = [r['funding_rate'] if r['funding_rate'] is not None else 0.0 for r in rows]
    times = [r['funding_time'] for r in rows]
    n = len(rates)
    avg = sum(rates) / n
    ppy = periods_per_year(times)
    annual = avg * ppy * 100
    pos = sum(1 for r in rates if r > 0)
    ts_min, ts_max = min(times), max(times)
    return {
        'periods':              n,
        'periods_per_year':     round(ppy, 1),
        'funding_interval':     interval_label(ppy),
        'date_start':           ms_to_dt(ts_min),
        'date_end':             ms_to_dt(ts_max),
        'avg_rate_pct':         round(avg * 100, 6),
        'annualized_yield_pct': round(annual, 4),
        'cumulative_return_pct': round(sum(rates) * 100, 4),
        'volatility_pct':       round(pstdev(rates) * 100, 6) if n > 1 else 0.0,
        'max_rate_pct':         round(max(rates) * 100, 6),
        'min_rate_pct':         round(min(rates) * 100, 6),
        'positive_periods':     pos,
        'pct_positive':         round(pos / n * 100, 1),
    }


def funding_price_correlation(funding_rows: list[dict], price_rows: list[dict]) -> float | None:
    """Корреляция Пирсона между funding rate и ценой актива по дневным средним.
    None, если общих дней меньше 3 (расчёт не имеет смысла)."""
    daily_funding: dict = defaultdict(list)
    for r in funding_rows:
        fr = r['funding_rate'] if r['funding_rate'] is not None else 0.0
        day = datetime.fromtimestamp(r['funding_time'] / 1000, tz=timezone.utc).date()
        daily_funding[day].append(fr)

    daily_price: dict = defaultdict(list)
    for p in price_rows:
        day = datetime.fromtimestamp(p['ts'] / 1000, tz=timezone.utc).date()
        daily_price[day].append(p['close'])

    common_days = sorted(set(daily_funding) & set(daily_price))
    if len(common_days) < 3:
        return None

    xs = [sum(daily_funding[d]) / len(daily_funding[d]) for d in common_days]
    ys = [sum(daily_price[d]) / len(daily_price[d]) for d in common_days]

    n = len(xs)
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x == 0 or var_y == 0:
        return None
    return round(cov / (var_x ** 0.5 * var_y ** 0.5), 4)


def hourly_heatmap(funding_rows: list[dict]) -> list[dict]:
    """Средняя ставка по (день недели, час UTC) — для heatmap сезонности.
    day: 0=Пн..6=Вс (как в datetime.weekday())."""
    buckets: dict = defaultdict(list)
    for r in funding_rows:
        fr = r['funding_rate'] if r['funding_rate'] is not None else 0.0
        dt = datetime.fromtimestamp(r['funding_time'] / 1000, tz=timezone.utc)
        buckets[(dt.weekday(), dt.hour)].append(fr)
    result = []
    for (day, hour), rates in sorted(buckets.items()):
        result.append({
            'day': day,
            'hour': hour,
            'avg_rate_pct': round(sum(rates) / len(rates) * 100, 6),
            'count': len(rates),
        })
    return result


def monthly_table(rows: list[dict]) -> list[dict]:
    """Помесячная доходность для одной пары (биржа, актив): доходность за месяц —
    сумма ставок за месяц (см. Инструкцию: без домножения на 365)."""
    monthly: dict = defaultdict(list)
    for r in rows:
        fr = r['funding_rate'] if r['funding_rate'] is not None else 0.0
        dt = datetime.fromtimestamp(r['funding_time'] / 1000, tz=timezone.utc)
        monthly[(dt.year, dt.month)].append(fr)
    result = []
    for (year, month), rates in sorted(monthly.items()):
        n = len(rates)
        s = sum(rates)
        pos = sum(1 for r in rates if r > 0)
        result.append({
            'month':        f'{year}-{month:02d}',
            'periods':      n,
            'avg_rate_pct': round(s / n * 100, 6),
            'return_pct':   round(s * 100, 4),
            'pct_positive': round(pos / n * 100, 1),
        })
    return result
