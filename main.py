# -------------------------------
# Gerekli Kütüphaneler
# -------------------------------
import os
import requests
import pandas as pd
import schedule
import time
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD
from ta.volatility import BollingerBands
from telegram import Bot
from dotenv import load_dotenv
from googletrans import Translator
import feedparser

# -------------------------------
# .env Yükleme
# -------------------------------
load_dotenv(".env")  # .env dosyasını yükle

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
TAAPI_KEY = os.getenv("TAAPI_API_KEY")
COINGLASS_API_KEY = os.getenv("COINGLASS_API_KEY")

bot = Bot(BOT_TOKEN) if BOT_TOKEN and CHAT_ID else None
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

keywords = [alias for aliases in coin_aliases.values() for alias in aliases]

# -------------------------------
# Telegram Fonksiyonu
# -------------------------------
def send_telegram_message(message):
    if bot:
        try:
            bot.send_message(chat_id=CHAT_ID, text=message)
        except Exception as e:
            print("Telegram gönderim hatası:", e)
    else:
        print("Telegram ayarları eksik veya bot başlatılmamış!")

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
    return indicators

# -------------------------------
# CoinGlass API
# -------------------------------
def fetch_coinglass_data(symbol="BTC", retries=3):
    if not COINGLASS_API_KEY:
        return {"long_ratio": None, "short_ratio": None}
    for attempt in range(retries):
        try:
            url = f"https://api.coinglass.com/api/pro/v1/futures/openInterest?symbol={symbol}"
            headers = {"coinglassSecret": COINGLASS_API_KEY}
            r = requests.get(url, headers=headers, timeout=10)
            r.raise_for_status()
            data = r.json()
            long_ratio = data.get("data", {}).get("longRate")
            short_ratio = data.get("data", {}).get("shortRate")
            return {"long_ratio": long_ratio, "short_ratio": short_ratio}
        except Exception as e:
            print(f"CoinGlass API hata ({attempt+1}/{retries}):", e)
            time.sleep(2)
    return {"long_ratio": None, "short_ratio": None}

# -------------------------------
# Basit AI Tahmin
# -------------------------------
def ai_position_prediction(indicators, cg_data=None):
    score = 0
    if indicators['rsi'] < 30:
        score += 1
    elif indicators['rsi'] > 70:
        score -= 1
    if indicators['ema_short'] > indicators['ema_long']:
        score += 1
    else:
        score -= 1
    if indicators['macd_diff'] > 0:
        score += 1
    else:
        score -= 1
    if cg_data and cg_data["long_ratio"] and cg_data["short_ratio"]:
        if cg_data["long_ratio"] > 0.6:
            score += 1
        elif cg_data["short_ratio"] > 0.6:
            score -= 1
    if score >= 2:
        return "Long"
    elif score <= -2:
        return "Short"
    else:
        return "Neutral"

# -------------------------------
# Analiz ve Telegram Alert
# -------------------------------
def analyze_and_alert():
    alerts = []
    for coin in coin_aliases.keys():
        indicators = fetch_and_analyze(coin)
        cg_data = fetch_coinglass_data(coin.replace("USDT",""))
        msg = f"{coin} Teknik Analiz:\n"
        rsi = indicators['rsi']
        msg += f"- RSI: {rsi:.1f}\n"
        trend = "Yukarı 🔼" if indicators['ema_short'] > indicators['ema_long'] else "Aşağı 🔽"
        msg += f"- EMA12/26 Trend: {trend}\n"
        macd_diff = indicators['macd_diff']
        macd_trend = "Pozitif" if macd_diff > 0 else "Negatif"
        msg += f"- MACD: {macd_trend}\n"
        if cg_data["long_ratio"] is not None and cg_data["short_ratio"] is not None:
            msg += f"- Long/Short Oran: {cg_data['long_ratio']*100:.1f}% / {cg_data['short_ratio']*100:.1f}%\n"
        position = ai_position_prediction(indicators, cg_data)
        msg += f"- AI Tahmini Pozisyon: {position}\n"
        alerts.append(msg)

    full_message = "\n\n".join(alerts)
    send_telegram_message(full_message)

# -------------------------------
# Scheduler - Her 2 Saatte Bir
# -------------------------------
schedule.every(2).hours.do(analyze_and_alert)
print("Bot çalışıyor, her 2 saatte bir analiz yapılacak...")

while True:
    schedule.run_pending()
    time.sleep(60)
