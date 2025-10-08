# -------------------------------
# Gerekli Kütüphaneler
# -------------------------------
import os
import requests
import pandas as pd
import schedule
import time
import json
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD
from ta.volatility import BollingerBands
from telegram import Bot
from googletrans import Translator

# -------------------------------
# API Anahtarları ve Telegram
# -------------------------------
BOT_TOKEN = "8320997161:AAFuNcpONcHLNdnitNehNZ2SOMskiGva6Qs"
CHAT_ID = 7294398674
TAAPI_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJjbHVlIjoiNjhlM2RkOTk4MDZmZjE2NTFlOGY3NjlkIiwiaWF0IjoxNzU5ODYyNDEyLCJleHAiOjMzMjY0MzI2NDEyfQ.dmvJC5-LNScEkhWdziBA21Ig8hc2oGsaNNohyfrIaD4"
COINGLASS_API_KEY = "36176ba717504abc9235e612d1daeb0c"
NEWSAPI_KEY = "737732d907a6418e8542100a79ed705b"

bot = Bot(BOT_TOKEN)
translator = Translator()

# -------------------------------
# Coin Alias Sistemi
# -------------------------------
coin_aliases = {
    "BTCUSDT": ["BTC", "Bitcoin", "BTCUSDT"],
    "ETHUSDT": ["ETH", "Ethereum", "ETHUSDT"],
    "SOLUSDT": ["SOL", "Solana", "SOLUSDT"],
    "SUIUSDT": ["SUI", "Sui", "SUIUSDT"],
    "AVAXUSDT": ["AVAX", "Avalanche", "AVAXUSDT"]
}

# -------------------------------
# Telegram Mesaj Fonksiyonu
# -------------------------------
def send_telegram_message(message):
    try:
        max_length = 4000  # Telegram karakter sınırı
        if len(message) > max_length:
            for i in range(0, len(message), max_length):
                bot.send_message(chat_id=CHAT_ID, text=message[i:i+max_length])
        else:
            bot.send_message(chat_id=CHAT_ID, text=message)
    except Exception as e:
        print("Telegram gönderim hatası:", e)

# -------------------------------
# Binance Fiyat Verisi
# -------------------------------
def fetch_binance_klines(symbol="BTCUSDT", interval="4h", limit=100):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    data = requests.get(url, timeout=10).json()
    df = pd.DataFrame(data, columns=[
        'open_time','open','high','low','close','volume',
        'close_time','quote_asset_volume','trades','taker_base','taker_quote','ignore'
    ])
    df['close'] = df['close'].astype(float)
    return df

# -------------------------------
# Teknik Analiz
# -------------------------------
def calculate_technical_indicators(df):
    result = {}
    rsi = RSIIndicator(close=df['close'], window=14)
    result['rsi'] = rsi.rsi().iloc[-1]

    ema_short = EMAIndicator(close=df['close'], window=12)
    ema_long = EMAIndicator(close=df['close'], window=26)
    result['ema_short'] = ema_short.ema_indicator().iloc[-1]
    result['ema_long'] = ema_long.ema_indicator().iloc[-1]

    bb = BollingerBands(close=df['close'], window=20, window_dev=2)
    result['bb_upper'] = bb.bollinger_hband().iloc[-1]
    result['bb_middle'] = bb.bollinger_mavg().iloc[-1]
    result['bb_lower'] = bb.bollinger_lband().iloc[-1]

    macd_indicator = MACD(close=df['close'], window_slow=26, window_fast=12, window_sign=9)
    result['macd'] = macd_indicator.macd().iloc[-1]
    result['macd_signal'] = macd_indicator.macd_signal().iloc[-1]
    result['macd_diff'] = macd_indicator.macd_diff().iloc[-1]

    return result

def fetch_and_analyze(symbol="BTCUSDT"):
    df = fetch_binance_klines(symbol=symbol, interval="4h")
    indicators = calculate_technical_indicators(df)
    last_close = df['close'].iloc[-1]
    prev_close = df['close'].iloc[-2]
    price_change_pct = ((last_close - prev_close)/prev_close)*100
    indicators['price_change'] = price_change_pct
    return indicators, last_close

# -------------------------------
# CoinGlass API
# -------------------------------
# -------------------------------
# CoinGlass API
# -------------------------------
def fetch_coinglass_data(symbol="BTC", retries=3):
    if not COINGLASS_API_KEY:
        return {"long_ratio": None, "short_ratio": None}

    for attempt in range(retries):
        try:
            url = f"https://open-api.coinglass.com/api/pro/v1/futures/openInterest?symbol={symbol}"
            headers = {"coinglassSecret": COINGLASS_API_KEY}
            r = requests.get(url, headers=headers, timeout=10)

            # Boş veya hatalı yanıt kontrolü
            if r.status_code != 200:
                print(f"CoinGlass API HTTP {r.status_code}: {r.text[:100]}")
                time.sleep(2)
                continue

            if not r.text.strip():
                print(f"CoinGlass API boş yanıt döndü (deneme {attempt+1})")
                time.sleep(2)
                continue

            data = r.json()
            long_ratio = data.get("data", {}).get("longRate")
            short_ratio = data.get("data", {}).get("shortRate")
            return {"long_ratio": long_ratio, "short_ratio": short_ratio}

        except Exception as e:
            print(f"CoinGlass API hata ({attempt+1}/{retries}):", e)
            time.sleep(2)

    return {"long_ratio": None, "short_ratio": None}

# -------------------------------
# NewsAPI Haberleri
# -------------------------------
def fetch_news():
    try:
        url = f"https://newsapi.org/v2/top-headlines?category=business&language=en&apiKey={NEWSAPI_KEY}"
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        articles = r.json().get("articles", [])
        return [f"{a['title']} - {a['url']}" for a in articles[:5]]
    except:
        return []

# -------------------------------
# AI Öğrenme Sistemi
# -------------------------------
history_file = "history.json"

def load_history():
    if os.path.exists(history_file):
        with open(history_file, "r") as f:
            return json.load(f)
    return {}

def save_history(history):
    with open(history_file, "w") as f:
        json.dump(history, f)

def ai_position_prediction(symbol, indicators, cg_data=None):
    history = load_history()
    score = 0

    # RSI
    if indicators['rsi'] < 30:
        score += 1
    elif indicators['rsi'] > 70:
        score -= 1
    # EMA
    if indicators['ema_short'] > indicators['ema_long']:
        score += 1
    else:
        score -= 1
    # MACD
    if indicators['macd_diff'] > 0:
        score += 1
    else:
        score -= 1
    # CoinGlass
    if cg_data and cg_data["long_ratio"] and cg_data["short_ratio"]:
        if cg_data["long_ratio"] > 0.6:
            score += 1
        elif cg_data["short_ratio"] > 0.6:
            score -= 1
    # Geçmiş öğrenimi
    last_pos = history.get(symbol, {}).get("last_position")
    if last_pos == "Long" and score > 0:
        score += 0.5
    elif last_pos == "Short" and score < 0:
        score -= 0.5

    if score >= 2:
        position = "Long"
    elif score <= -2:
        position = "Short"
    else:
        position = "Neutral"

    confidence = min(max((score + 3)/6, 0), 1)*100
    history[symbol] = {"last_position": position}
    save_history(history)
    return position, confidence

# -------------------------------
# Ani Fiyat Dalgalanma Uyarısı (%5)
# -------------------------------
last_prices = {}

def check_price_spike(symbol, current_price):
    global last_prices
    if symbol in last_prices:
        old_price = last_prices[symbol]
        change_pct = ((current_price - old_price) / old_price) * 100
        if abs(change_pct) >= 5:
            direction = "📈 YÜKSELDİ" if change_pct > 0 else "📉 DÜŞTÜ"
            msg = f"⚠️ {symbol} Fiyat Uyarısı:\nFiyat son kontrolünden beri %{change_pct:.2f} {direction}!\nAnlık Fiyat: {current_price:.2f} USDT"
            send_telegram_message(msg)
    last_prices[symbol] = current_price

# -------------------------------
# Ana Analiz Fonksiyonu
# -------------------------------
def analyze_and_alert():
    alerts = []
    for coin in coin_aliases.keys():
        indicators, current_price = fetch_and_analyze(coin)
        check_price_spike(coin, current_price)
        cg_data = fetch_coinglass_data(coin.replace("USDT", ""))
        position, confidence = ai_position_prediction(coin, indicators, cg_data)

        msg = f"{coin} Analizi (4s):\n"
        msg += f"💰 Fiyat: {current_price:.2f} USDT ({indicators['price_change']:+.2f}% son 4 saatte)\n"
        msg += f"📈 RSI: {indicators['rsi']:.1f}\n"
        trend = "🔼 Yukarı" if indicators['ema_short'] > indicators['ema_long'] else "🔽 Aşağı"
        msg += f"📉 EMA12/26 Trend: {trend}\n"
        macd_trend = "Pozitif" if indicators['macd_diff'] > 0 else "Negatif"
        msg += f"💹 MACD: {macd_trend}\n"
        if cg_data["long_ratio"] is not None and cg_data["short_ratio"] is not None:
            msg += f"📊 Long/Short Oran: {cg_data['long_ratio']*100:.1f}% / {cg_data['short_ratio']*100:.1f}%\n"
        msg += f"🤖 AI Tahmini: {position}\n"
        msg += f"📊 AI Güven Skoru: {confidence:.0f}%\n"

        news = fetch_news()
        if news:
            msg += "\n📰 Son Haberler:\n" + "\n".join(news)

        alerts.append(msg)

    full_message = "\n\n".join(alerts)
    send_telegram_message(full_message)

# -------------------------------
# Scheduler
# -------------------------------
schedule.every(1).minute.do(analyze_and_alert)
print("Bot çalışıyor... Her 2 saatte analiz + anlık %5 fiyat uyarısı aktif ✅")

while True:
    schedule.run_pending()
    time.sleep(60)






