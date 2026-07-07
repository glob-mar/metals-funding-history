# История фандинга по металлам

Сайт для выгрузки истории funding rate по драгметаллам с трёх бирж.

## Активы

| Актив | Binance | OKX | Hyperliquid |
|-------|---------|-----|-------------|
| XAU (Золото) | XAUUSDT | XAU-USDT-SWAP | XAU |
| XAG (Серебро) | XAGUSDT | XAG-USDT-SWAP | XAG |
| XPT (Платина) | XPTUSDT | XPT-USDT-SWAP | XPT |
| XPD (Палладий) | XPDUSDT | XPD-USDT-SWAP | XPD |

## Что умеет

- Собирает историю за весь текущий год
- Хранит в SQLite
- Показывает на сайте
- Даёт скачать CSV

## Деплой на Railway (3 шага)

1. Зайди на [railway.app](https://railway.app)
2. Нажми **New Project → Deploy from GitHub Repo**
3. Выбери `glob-mar/metals-funding-history` → **Deploy**

Рelway сам прочитает `railway.json` и запустит сервер.

## Локальный запуск

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Открыть: http://127.0.0.1:8000
