def periods_per_year(funding_times: list[int]) -> float:
    """Периоды в год по медиане реальной дельты между funding_time (мс).

    Интервал начисления зависит от конкретного инструмента, а не только
    от биржи (напр. на Binance большинство товарных активов — 4ч, BTC — 8ч),
    поэтому считаем его из фактических данных, а не храним хардкодом.
    """
    times = sorted(set(funding_times))
    if len(times) < 2:
        return 8760.0
    deltas = sorted(t2 - t1 for t1, t2 in zip(times, times[1:]))
    n = len(deltas)
    mid = n // 2
    median_ms = deltas[mid] if n % 2 else (deltas[mid - 1] + deltas[mid]) / 2
    if median_ms <= 0:
        return 8760.0
    return (365 * 24 * 3600 * 1000) / median_ms


def interval_label(ppy: float) -> str:
    """Человекочитаемый интервал начисления, например 'раз в 4 часа'."""
    if ppy <= 0:
        return '—'
    hours = max(round((365 * 24) / ppy), 1)
    if hours == 1:
        return 'раз в час'
    n_mod100 = hours % 100
    n_mod10 = hours % 10
    if 11 <= n_mod100 <= 14:
        word = 'часов'
    elif n_mod10 == 1:
        word = 'час'
    elif 2 <= n_mod10 <= 4:
        word = 'часа'
    else:
        word = 'часов'
    return f'раз в {hours} {word}'
