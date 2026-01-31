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

# Hata varsa loglara bas ve durdur
if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    print("❌ HATA: API Anahtarları EKSİK! Lütfen Railway Variables kontrol et.")
    sys.exit(1)

# Logları basitleştirdik
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# Exchange Ayarı
exchange = ccxt.binance({
    'enableRateLimit': True,
    'options': {'defaultType': 'future'}
})

# --- ANALİZ FONKSİYONLARI ---
def fetch_data(symbol, timeframe='4h'):
    # Önce CCXT dene
    try:
        bars = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=100)
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        return df
    except:
        pass # Sessizce geç
    
    # Sonra HTTP dene (Yedek)
    try:
        url = "https://api.binance.com/api/v3/klines"
        params = {'symbol': symbol, 'interval': timeframe, 'limit': 100}
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        df = pd.DataFrame(data, columns=['t', 'open', 'high', 'low', 'close', 'v', 'ct', 'qv', 'n', 'tb', 'tq', 'i'])
        df = df.astype({'open': float, 'high': float, 'low': float, 'close': float, 'v': float})
        return df
    except Exception as e:
        print(f"❌ Veri Hatası: {e}")
        return None

def calculate_indicators(df):
    if df is None: return None
    close = df['close']
    # RSI
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    rsi = 100 - (100 / (1 + gain / loss))
    # EMA
    ema = close.ewm(span=50, adjust=False).mean()
    return rsi.iloc[-1], ema.iloc[-1]

async def get_ai_comment(symbol, price, rsi, direction, score):
    prompt = (
        f"Kripto Analizi yap. Coin: {symbol}, Fiyat: {price}, RSI: {rsi:.1f}, "
        f"Yön: {direction}, Skor: {score}/100. Kısa ve net strateji ver."
    )
    headers = {'Content-Type': 'application/json'}
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    # Timeout süreleri
    models = [
        ("Gemini 3.0 Pro", "gemini-3-pro-preview", 15),
        ("Gemini 2.5 Pro", "gemini-2.5-pro", 15),
        ("Gemini Flash 3.0", "gemini-3-flash-preview", 8),
        ("Gemini Flash 2.5", "gemini-2.5-flash", 8),
        ("Gemini Flash Lite", "gemini-2.5-flash-lite", 5)
    ]

    for name, model_id, timeout in models:
        try:
            print(f"🧠 {name} deneniyor...")
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent?key={GEMINI_API_KEY}"
            resp = await asyncio.to_thread(requests.post, url, headers=headers, json=payload, timeout=timeout)
            if resp.status_code == 200:
                return resp.json()['candidates'][0]['content']['parts'][0]['text'] + f"\n\n_(👑 Analiz: {name})_"
        except:
            continue
    
    return "⚠️ Modeller meşgul. Teknik verilere göre işlem yapın."

# --- TELEGRAM KOMUTU ---
async def incele(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text("❌ Örnek: `/incele BTCUSDT`")
    symbol = context.args[0].upper()
    
    # 1. Bildirim (Cevap vermezse buradan anlarız)
    msg = await update.message.reply_text(f"🔍 {symbol} için V9.0 motoru çalışıyor...")

    # 2. Veri Çekme
    df = fetch_data(symbol)
    if df is None:
        return await msg.edit_text("❌ Binance verisi alınamadı.")
    
    current_price = df['close'].iloc[-1]
    rsi, ema = calculate_indicators(df)
    
    # Skorlama
    score = 0
    if current_price > ema: score += 20
    if rsi < 30: score += 30
    elif rsi > 70: score -= 30
    
    direction = "YÜKSELİŞ 🟢" if score > 0 else "DÜŞÜŞ 🔴"
    
    # 3. AI Çağırma
    try:
        comment = await get_ai_comment(symbol, current_price, rsi, direction, score)
    except Exception as e:
        comment = f"AI Hatası: {e}"

    # 4. Sonuç Gönderme
    final_msg = (
        f"💎 *{symbol} ANALİZ (V9.0 - Clean)*\n"
        f"📊 Yön: {direction}\n"
        f"🏆 Skor: {score}\n"
        f"💵 Fiyat: {current_price:.4f}\n"
        f"📈 RSI: {rsi:.2f}\n\n"
        f"🧠 *AI Yorumu:*\n{comment}"
    )
    
    # Eski mesajı düzenle (En temizi)
    try:
        await msg.edit_text(final_msg, parse_mode='Markdown')
    except:
        await update.message.reply_text(final_msg, parse_mode='Markdown')

if __name__ == '__main__':
    print("🚀 BOT V9.0 BAŞLATILIYOR... (Eski Tokenleri Unutmayın!)")
    # En temel, en sade başlatıcı
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("incele", incele))
    app.run_polling()
