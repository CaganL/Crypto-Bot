import logging
import feedparser
import ccxt
import pandas as pd
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler
import asyncio
import os
import time

# --- GÜVENLİK ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    raise ValueError("❌ HATA: API Anahtarları Railway Variables kısmında eksik!")

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# Exchange Ayarı
exchange = ccxt.binance({
    'enableRateLimit': True,
    'options': {'defaultType': 'future'}
})

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

# --- VERİ ÇEKME (ÇİFT MOTOR) ---
def fetch_data(symbol, timeframe='4h'):
    try:
        bars = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=100)
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        return df
    except Exception as e:
        print(f"⚠️ CCXT Hatası: {e}, HTTP deneniyor...")
    
    try:
        base_url = "https://api.binance.com/api/v3/klines"
        params = {'symbol': symbol, 'interval': timeframe, 'limit': 100}
        response = requests.get(base_url, params=params, timeout=10)
        data = response.json()
        df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'q_vol', 'trades', 'tb_base', 'tb_quote', 'ignore'])
        df = df.astype({'open': float, 'high': float, 'low': float, 'close': float, 'volume': float})
        return df
    except Exception as e:
        print(f"❌ HTTP Hatası: {e}")
        return None

def fetch_news(symbol):
    coin_ticker = symbol.replace("USDT", "").upper()
    rss_url = f"https://cryptopanic.com/news/rss/currency/{coin_ticker}/"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    try:
        response = requests.get(rss_url, headers=headers, timeout=5)
        if response.status_code == 200:
            feed = feedparser.parse(response.content)
            return [entry.title for entry in feed.entries[:5]] if feed.entries else []
        return []
    except: return []

def analyze_market(symbol):
    df_4h = fetch_data(symbol, '4h')
    if df_4h is None: return None
    
    df_15m = fetch_data(symbol, '15m')
    if df_15m is None: return None

    current_price = df_4h['close'].iloc[-1]
    ema_50 = calculate_ema(df_4h['close'], 50).iloc[-1]
    rsi_4h = calculate_rsi(df_4h['close'], 14).iloc[-1]
    rsi_15m = calculate_rsi(df_15m['close'], 14).iloc[-1]
    
    score = 0
    diff_percent = ((current_price - ema_50) / ema_50) * 100
    if diff_percent > 0: score += 10
    else: score -= 10
    
    if rsi_4h < 30: score += 30
    elif rsi_4h > 70: score -= 30
    
    direction = "YÜKSELİŞ (LONG) 🟢" if score > 0 else "DÜŞÜŞ (SHORT) 🔴"
    
    return {
        "symbol": symbol, "price": current_price, "score": score, 
        "direction": direction, "tp": df_4h['high'].max(), "sl": df_4h['low'].min(),
        "rsi_4h": rsi_4h, "rsi_15m": rsi_15m
    }

# --- AI YORUMU (ANTI-FLOOD SİSTEMİ) ---
async def get_ai_comment(data, news, status_msg):
    if news: news_text = "\n".join([f"- {n}" for n in news])
    else: news_text = "Haber yok."

    prompt = (
        f"Sen usta bir kripto analistisin. Türkçe analiz yap.\n"
        f"Coin: {data['symbol']} | Fiyat: {data['price']:.2f}\n"
        f"Teknik Skor: {data['score']}/100 | Yön: {data['direction']}\n"
        f"RSI(4h): {data['rsi_4h']:.1f} | RSI(15m): {data['rsi_15m']:.1f}\n"
        f"HABERLER:\n{news_text}\n"
        f"GÖREV: Yorumla ve strateji ver."
    )
    
    headers = {'Content-Type': 'application/json'}
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    # Model Listesi
    models = [
        ("Gemini 3.0 Pro Preview", "gemini-3-pro-preview"),
        ("Gemini 2.5 Pro", "gemini-2.5-pro"),
        ("Gemini 3.0 Flash Preview", "gemini-3-flash-preview"),
        ("Gemini 2.5 Flash", "gemini-2.5-flash"),
        ("Gemini 2.5 Flash Lite", "gemini-2.5-flash-lite")
    ]

    last_error = ""

    for model_name, model_id in models:
        try:
            # Telegram mesajını güncellemeye çalış (Hata verirse yut ve devam et)
            try:
                await status_msg.edit_text(f"🧠 Düşünüyor: {model_name}...")
            except:
                pass # Telegram "Çok hızlısın" derse takma, işine bak.
            
            # Google'a İsteği At
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent?key={GEMINI_API_KEY}"
            response = await asyncio.to_thread(requests.post, url, headers=headers, json=payload, timeout=25)
            
            if response.status_code == 200:
                return response.json()['candidates'][0]['content']['parts'][0]['text'] + f"\n\n_(👑 Analiz: {model_name})_"
            else:
                last_error = f"Kod: {response.status_code}"
                # Hata alınca biraz bekle ki Telegram spam sanmasın
                await asyncio.sleep(1)
                continue 
        except Exception as e:
            last_error = str(e)
            await asyncio.sleep(1)
            continue

    return f"⚠️ HATA: Tüm modeller denendi ama başarısız oldu.\nSon Hata: {last_error}"

# --- KOMUTLAR ---
async def incele(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text("❌ Örnek: `/incele BTCUSDT`")
    symbol = context.args[0].upper()
    
    status_msg = await update.message.reply_text(f"🔍 {symbol} verileri toplanıyor...")

    data = analyze_market(symbol)
    if not data:
        return await status_msg.edit_text("❌ Veri alınamadı (Binance Bağlantı Hatası).")
    
    news = fetch_news(symbol)
    
    # AI Analizini Başlat
    ai_comment = await get_ai_comment(data, news, status_msg)
    
    strength = "🔥 GÜÇLÜ" if abs(data['score']) >= 50 else "⚠️ ZAYIF"

    msg = (
        f"💎 *{symbol} ANALİZ (V8.5 - Anti-Flood)*\n"
        f"📊 Yön: {data['direction']}\n"
        f"🏆 Skor: {data['score']} {strength}\n"
        f"💵 Fiyat: {data['price']:.4f}\n\n"
        f"🧠 *AI Yorumu:*\n{ai_comment}\n\n"
        f"🎯 Hedef: {data['tp']:.4f} | Stop: {data['sl']:.4f}"
    )
    
    # KİLİT NOKTA: Mesajı düzenlemeyi dene, olmazsa YENİ mesaj at
    try:
        await status_msg.edit_text(msg, parse_mode='Markdown')
    except:
        await update.message.reply_text(msg, parse_mode='Markdown')

if __name__ == '__main__':
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("incele", incele))
    app.run_polling()
