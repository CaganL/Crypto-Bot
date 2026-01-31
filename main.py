import logging
import ccxt
import pandas as pd
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler
import asyncio
import os
import sys

# --- GÜVENLİK ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    print("❌ HATA: API Anahtarları EKSİK!")
    sys.exit(1)

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# Borsa Ayarı
exchange = ccxt.binance({
    'enableRateLimit': True,
    'options': {'defaultType': 'future'}
})

# --- VERİ ÇEKME ---
def fetch_data(symbol, timeframe='4h'):
    try:
        bars = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=100)
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        return df
    except:
        pass
    
    try:
        url = "https://api.binance.com/api/v3/klines"
        params = {'symbol': symbol, 'interval': timeframe, 'limit': 100}
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        df = pd.DataFrame(data, columns=['t', 'open', 'high', 'low', 'close', 'v', 'ct', 'qv', 'n', 'tb', 'tq', 'i'])
        df = df.astype({'open': float, 'high': float, 'low': float, 'close': float, 'v': float})
        return df
    except Exception as e:
        print(f"❌ Veri Çekilemedi: {e}")
        return None

# --- TEKNİK HESAPLAMA ---
def calculate_indicators(df):
    if df is None: return 0, 0, 0
    close = df['close']
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    rsi = 100 - (100 / (1 + gain / loss))
    ema_50 = close.ewm(span=50, adjust=False).mean()
    return close.iloc[-1], rsi.iloc[-1], ema_50.iloc[-1]

# --- AI MOTORU ---
async def get_ai_comment(symbol, price, rsi, direction, score):
    prompt = (
        f"Kripto Analisti gibi konuş. Coin: {symbol}. "
        f"Fiyat: {price:.2f}, RSI: {rsi:.1f}, Yön: {direction}, Skor: {score}. "
        f"Yatırımcılara kısa ve net bir strateji (Hedef/Stop) öner."
    )
    headers = {'Content-Type': 'application/json'}
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    models = [
        ("Gemini 3.0 Pro", "gemini-3-pro-preview", 15),
        ("Gemini Flash 3.0", "gemini-3-flash-preview", 10),
        ("Gemini Flash 2.5", "gemini-2.5-flash", 10),
    ]

    for name, model_id, timeout in models:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent?key={GEMINI_API_KEY}"
            resp = await asyncio.to_thread(requests.post, url, headers=headers, json=payload, timeout=timeout)
            if resp.status_code == 200:
                # Gelen metni temizlemiyoruz, direkt ham haliyle döndürüyoruz
                return resp.json()['candidates'][0]['content']['parts'][0]['text'] + f"\n\n(Model: {name})"
        except:
            continue
    return "AI Servisi Meşgul."

# --- KOMUT ---
async def incele(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text("Kullanım: /incele BTCUSDT")
    symbol = context.args[0].upper()
    
    # Adım 1: Başlangıç
    msg = await update.message.reply_text(f"🔍 {symbol} taranıyor... (V9.2)")

    # Adım 2: Veri
    df = fetch_data(symbol)
    if df is None:
        return await msg.edit_text("❌ Veri Hatası!")
    
    # Kullanıcıya ilerlemeyi göster
    try:
        await msg.edit_text(f"✅ Veri çekildi. AI düşüncesi alınıyor...")
    except: pass # Hata verirse takılma, devam et

    price, rsi, ema = calculate_indicators(df)
    score = 0
    if price > ema: score += 20
    if rsi < 30: score += 30
    elif rsi > 70: score -= 30
    direction = "YUKSELIS" if score > 0 else "DUSUS"

    # Adım 3: AI
    comment = await get_ai_comment(symbol, price, rsi, direction, score)

    # Adım 4: SONUÇ (SÜSLEMESİZ - SAF METİN)
    # Yıldız (*) veya Alt çizgi (_) kullanmadan düz metin oluşturuyoruz.
    final_text = (
        f"ANALIZ RAPORU: {symbol} (V9.2)\n"
        f"Yon: {direction}\n"
        f"Skor: {score}\n"
        f"Fiyat: {price:.4f}\n"
        f"RSI: {rsi:.2f}\n\n"
        f"AI YORUMU:\n{comment}"
    )
    
    # parse_mode KULLANMIYORUZ! (Hata riskini sıfırlar)
    try:
        await msg.edit_text(final_text)
    except:
        await update.message.reply_text(final_text)

if __name__ == '__main__':
    print("BOT V9.2 CALISIYOR...")
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("incele", incele))
    app.run_polling()
