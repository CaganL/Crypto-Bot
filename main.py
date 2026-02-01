import logging
import feedparser
import ccxt
import pandas as pd
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler
import asyncio
import os
import sys
import time
from datetime import datetime

# --- GÜVENLİK ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# Anahtarları topla
API_KEYS = []
if os.getenv("GEMINI_API_KEY"): API_KEYS.append(os.getenv("GEMINI_API_KEY"))
if os.getenv("GEMINI_API_KEY_2"): API_KEYS.append(os.getenv("GEMINI_API_KEY_2"))
if os.getenv("GEMINI_API_KEY_3"): API_KEYS.append(os.getenv("GEMINI_API_KEY_3"))

if not TELEGRAM_TOKEN or not API_KEYS:
    print("❌ HATA: API Anahtarları EKSİK!")
    sys.exit(1)

print(f"✅ V18.6 MATRIX ENGINE: {len(API_KEYS)} anahtar ile hazır.")

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO, force=True)

exchange = ccxt.binance({
    'enableRateLimit': True,
    'options': {'defaultType': 'future'}
})

# --- TEMİZLEYİCİ ---
def clean_markdown(text):
    if not text: return ""
    return text.replace("*", "").replace("_", "").replace("`", "").replace("[", "").replace("]", "")

# --- 1. VERİ ---
def fetch_data(symbol, timeframe='4h'):
    try:
        bars = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=100)
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df
    except: pass
    
    try:
        url = "https://api.binance.com/api/v3/klines"
        params = {'symbol': symbol, 'interval': timeframe, 'limit': 100}
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        df = pd.DataFrame(data, columns=['t', 'open', 'high', 'low', 'close', 'v', 'ct', 'qv', 'n', 'tb', 'tq', 'i'])
        df = df.astype({'open': float, 'high': float, 'low': float, 'close': float, 'v': float})
        df.rename(columns={'v': 'volume', 't': 'timestamp'}, inplace=True)
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df
    except: return None

# --- 2. HABER ---
def fetch_news(symbol):
    try:
        coin = symbol.replace("USDT", "").upper()
        url = f"https://cryptopanic.com/news/rss/currency/{coin}/"
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=5)
        feed = feedparser.parse(response.content)
        if feed.entries:
            return clean_markdown(feed.entries[0].title)
    except: return None
    return None

# --- 3. TEKNİK ---
def calculate_indicators(df):
    if df is None: return 0, 0, 0, 0, 0, ""
    close = df['close']
    
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    rsi = 100 - (100 / (1 + gain / loss))
    ema_50 = close.ewm(span=50, adjust=False).mean()
    
    macro_low = df['low'].min()
    macro_high = df['high'].max()
    
    history_str = ""
    last_candles = df.tail(12) 
    
    for index, row in last_candles.iterrows():
        time_str = row['timestamp'].strftime('%d/%m %H:%M')
        history_str += f"* {time_str} -> Kapanış: {row['close']:.4f} | En Yüksek: {row['high']:.4f}\n"

    return close.iloc[-1], rsi.iloc[-1], ema_50.iloc[-1], macro_low, macro_high, history_str

# --- 4. AI MOTORU (TERSINE DÖNGÜ - MATRIX) ---
async def get_ai_comment(symbol, price, rsi, direction, score, news_title, macro_low, macro_high, history_str):
    news_text = f"Haber: {news_title}" if news_title else "Haber Yok"
    
    prompt = (
        f"Kripto Analistisin. Coin: {symbol}\n"
        f"ANLIK: Fiyat {price:.4f} | RSI {rsi:.1f} | Yön {direction}\n"
        f"GENİŞ AÇI (16 Gün): Dip {macro_low:.4f} | Tepe {macro_high:.4f}\n\n"
        f"YAKIN ÇEKİM (Son 48 Saat):\n{history_str}\n\n"
        f"{news_text}\n"
        f"GÖREV: Mum formasyonlarını incele, destek/dirençleri bul ve AL/SAT stratejisi oluştur."
    )
    headers = {'Content-Type': 'application/json'}
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    # Modelleri önem sırasına göre diziyoruz
    models = [
        ("Gemini 2.5 Pro", "gemini-2.5-pro"),         # 1. Öncelik
        ("Gemini 3.0 Pro Preview", "gemini-3-pro-preview"), # 2. Öncelik
        ("Gemini 2.0 Flash", "gemini-2.0-flash"),     # 3. Öncelik
        ("Gemini 1.5 Flash", "gemini-1.5-flash")      # 4. Öncelik (En sağlamı)
    ]

    last_error = ""

    # --- YENİ MANTIK: Önce Modeli Seç, Sonra Tüm Anahtarlarda Dene ---
    for name, model_id in models:
        print(f"🧠 MODEL DENENİYOR: {name} (Tüm anahtarlarda aranacak...)")
        
        for api_key in API_KEYS:
            key_short = f"...{api_key[-4:]}"
            
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent?key={api_key}"
                # Timeout'u kısa tutalım, seri geçsin
                resp = await asyncio.to_thread(requests.post, url, headers=headers, json=payload, timeout=8)
                
                if resp.status_code == 200:
                    raw_text = resp.json()['candidates'][0]['content']['parts'][0]['text']
                    return clean_markdown(raw_text) + f"\n\n_(🧠 {name} | 🔑 {key_short})_"
                
                elif resp.status_code == 429:
                    print(f"  ❌ {key_short} -> {name} İÇİN KOTA DOLU. (Sıradaki anahtara geçiliyor)")
                    last_error = f"{name}: Kota Dolu"
                    continue # Aynı model için diğer anahtarı dene
                
                else:
                    print(f"  ⚠️ {key_short} -> Hata: {resp.status_code}")
                    last_error = f"Hata {resp.status_code}"
                    continue
                    
            except Exception as e:
                last_error = str(e)
                continue
        
        # Eğer bu model hiçbir anahtarda çalışmadıysa, döngü başa döner ve
        # listedeki BİR SONRAKİ MODELİ (Flash) tüm anahtarlarda denemeye başlar.

    return f"⚠️ Analiz başarısız. Tüm modeller ve anahtarlar denendi. Hata: {last_error}"

# --- KOMUT ---
async def incele(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text("❌ Örnek: `/incele BTCUSDT`")
    symbol = context.args[0].upper()
    
    msg = await update.message.reply_text(f"🚀 *{symbol}* için Matrix Taraması (V18.6) başladı...", parse_mode='Markdown')

    df = fetch_data(symbol)
    if df is None: return await msg.edit_text("❌ Veri Hatası!")
    
    price, rsi, ema, macro_low, macro_high, history_str = calculate_indicators(df)
    news_title = fetch_news(symbol)
    
    score = 0
    if price > ema: score += 20
    if rsi < 30: score += 30
    elif rsi > 70: score -= 30
    
    if score >= 30: direction_icon, direction_text = "🚀", "GÜÇLÜ AL"
    elif score > 0: direction_icon, direction_text = "🟢", "AL"
    elif score > -30: direction_icon, direction_text = "🔴", "SAT"
    else: direction_icon, direction_text = "🩸", "GÜÇLÜ SAT"

    try: await msg.edit_text(f"✅ Veriler alındı. Uygun model aranıyor...")
    except: pass

    comment = await get_ai_comment(symbol, price, rsi, direction_text, score, news_title, macro_low, macro_high, history_str)

    final_text = (
        f"💎 *{symbol} MATRIX ANALİZ (V18.6)* 💎\n\n"
        f"💰 *Fiyat:* `{price:.4f}` $\n"
        f"🌍 *Ana Dip:* `{macro_low:.4f}`\n"
        f"🏔️ *Ana Tepe:* `{macro_high:.4f}`\n"
        f"🧭 *Sinyal:* {direction_icon} *{direction_text}* (Skor: {score})\n"
        f"───────────────────\n"
        f"📰 *Haber:* {news_title if news_title else 'Akış Sakin'}\n"
        f"───────────────────\n\n"
        f"🧠 *Strateji:*\n{comment}"
    )
    
    try:
        await msg.edit_text(final_text, parse_mode='Markdown')
    except:
        await update.message.reply_text(final_text.replace("*", "").replace("`", ""))

if __name__ == '__main__':
    print(f"🚀 BOT V18.6 (MATRIX ENGINE) ÇALIŞIYOR... ({len(API_KEYS)} Key Aktif)")
    sys.stdout.flush()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("incele", incele))
    app.run_polling()
