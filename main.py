import logging
import feedparser
import ccxt
import pandas as pd
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler
import asyncio
import os
import json

# --- GÜVENLİK ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    raise ValueError("❌ HATA: API Anahtarları Railway Variables kısmında eksik!")

# İzleme Listesi
WATCHLIST = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "AVAXUSDT", "DOGEUSDT", "PEPEUSDT"]

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- MATEMATİKSEL FONKSİYONLAR ---
def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/period, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/period, adjust=False).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calculate_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def calculate_sma(series, period):
    return series.rolling(window=period).mean()

# --- VERİ VE ANALİZ ---
def fetch_data(symbol, timeframe):
    exchange = ccxt.binance()
    try:
        bars = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=100)
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        return df
    except: return None

def fetch_news(symbol):
    coin_ticker = symbol.replace("USDT", "").upper()
    rss_url = f"https://cryptopanic.com/news/rss/currency/{coin_ticker}/"
    try:
        feed = feedparser.parse(rss_url)
        return [entry.title for entry in feed.entries[:3]] if feed.entries else []
    except: return []

def analyze_market(symbol):
    df_4h = fetch_data(symbol, '4h')
    df_15m = fetch_data(symbol, '15m')
    if df_4h is None or df_15m is None: return None

    current_price = df_4h['close'].iloc[-1]
    
    ema_50 = calculate_ema(df_4h['close'], 50).iloc[-1]
    rsi_series = calculate_rsi(df_4h['close'], 14)
    rsi_4h = rsi_series.iloc[-1]
    vol_sma = calculate_sma(df_4h['volume'], 20).iloc[-1]
    current_vol = df_4h['volume'].iloc[-1]
    rsi_15m = calculate_rsi(df_15m['close'], 14).iloc[-1]

    score = 0
    diff_percent = ((current_price - ema_50) / ema_50) * 100
    
    if diff_percent > 3: score += 30
    elif diff_percent > 1: score += 20
    elif diff_percent > 0: score += 10
    elif diff_percent < -3: score -= 30
    elif diff_percent < -1: score -= 20
    else: score -= 10

    vol_ratio = current_vol / vol_sma if vol_sma > 0 else 1
    if vol_ratio > 2.0: score += (20 if score > 0 else -20)
    elif vol_ratio > 1.2: score += (10 if score > 0 else -10)

    if rsi_4h < 25: score += 30
    elif rsi_4h < 35: score += 20
    elif rsi_4h > 75: score -= 30
    elif rsi_4h > 65: score -= 20

    if score > 0:
        if rsi_15m < 30: score += 20
        elif rsi_15m < 50: score += 10
        elif rsi_15m > 70: score -= 15
    else:
        if rsi_15m > 70: score -= 20
        elif rsi_15m > 50: score -= 10
        elif rsi_15m < 30: score += 15

    direction = "YÜKSELİŞ (LONG) 🟢" if score > 0 else "DÜŞÜŞ (SHORT) 🔴"
    
    recent_high = df_4h['high'].tail(50).max()
    recent_low = df_4h['low'].tail(50).min()
    
    if score > 0:
        tp = recent_high
        sl = recent_low * 0.99
    else:
        tp = recent_low
        sl = recent_high * 1.01

    return {
        "symbol": symbol, "price": current_price, "score": score, 
        "direction": direction, "tp": tp, "sl": sl,
        "rsi_4h": rsi_4h, "rsi_15m": rsi_15m
    }

# --- AI YORUMU (V6.1 - GEMINI 2.5 PRO) ---
async def get_ai_comment(data, news):
    prompt = (
        f"Sen usta bir kripto analistisin. Verileri yorumla:\n"
        f"Coin: {data['symbol']} | Fiyat: {data['price']:.2f}\n"
        f"Teknik Skor: {data['score']}/100 | Yön: {data['direction']}\n"
        f"RSI(4h): {data['rsi_4h']:.1f} | RSI(15m): {data['rsi_15m']:.1f}\n"
        f"Haber Başlıkları: {', '.join(news)}\n\n"
        f"GÖREV: Bu verileri kullanarak Türkçe, samimi ve yatırımcıya net bir tavsiye ver. Riskleri de belirt."
    )
    
    # GÜNCELLEME: Flash yerine PRO modelini seçtik.
    target_model = "gemini-2.5-pro" 
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{target_model}:generateContent?key={GEMINI_API_KEY}"
    
    headers = {'Content-Type': 'application/json'}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}]
    }

    try:
        # Pro modeli biraz daha yavaş olabilir, timeout süresini uzatmak gerekebilir ama
        # şimdilik standart request yeterli.
        response = await asyncio.to_thread(requests.post, url, headers=headers, json=payload)
        
        if response.status_code == 200:
            result = response.json()
            try:
                return result['candidates'][0]['content']['parts'][0]['text']
            except (KeyError, IndexError):
                 return "⚠️ AI cevabı anlaşılamadı."
        else:
            return f"⚠️ API Hatası ({response.status_code}): {response.text[:100]}"
    except Exception as e:
        return f"⚠️ Bağlantı Hatası: {str(e)}"

# --- KOMUTLAR ---
async def incele(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text("❌ Örnek: `/incele BTCUSDT`")
    symbol = context.args[0].upper()
    
    # Kullanıcıya Pro modelin çalıştığını haber verelim
    await update.message.reply_text(f"🔍 {symbol} (Gemini 2.5 Pro) ile analiz ediliyor... (Biraz sürebilir)")

    data = analyze_market(symbol)
    if not data: return await update.message.reply_text("❌ Veri alınamadı.")

    news = fetch_news(symbol)
    ai_comment = await get_ai_comment(data, news)
    
    strength = "🔥 GÜÇLÜ" if abs(data['score']) >= 50 else "⚠️ ZAYIF"

    msg = (
        f"💎 *{symbol} ANALİZ (V6.1 - Pro Max)*\n"
        f"📊 Yön: {data['direction']}\n"
        f"🏆 Skor: {data['score']} {strength}\n"
        f"💵 Fiyat: {data['price']:.4f}\n\n"
        f"🧠 *AI Yorumu:*\n{ai_comment}\n\n"
        f"🎯 Hedef: {data['tp']:.4f} | Stop: {data['sl']:.4f}"
    )
    await update.message.reply_text(msg, parse_mode='Markdown')

if __name__ == '__main__':
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("incele", incele))
    app.run_polling()
