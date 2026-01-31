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

# --- GÜVENLİK ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    print("❌ HATA: API Anahtarları EKSİK!")
    sys.exit(1)

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

exchange = ccxt.binance({
    'enableRateLimit': True,
    'options': {'defaultType': 'future'}
})

# --- TEMİZLEYİCİ ---
def clean_markdown(text):
    if not text: return ""
    # Telegram'ı bozan karakterleri temizle
    return text.replace("*", "").replace("_", "").replace("`", "").replace("[", "").replace("]", "")

# --- 1. VERİ ---
def fetch_data(symbol, timeframe='4h'):
    try:
        bars = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=100)
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        return df
    except: pass
    
    try:
        url = "https://api.binance.com/api/v3/klines"
        params = {'symbol': symbol, 'interval': timeframe, 'limit': 100}
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        df = pd.DataFrame(data, columns=['t', 'open', 'high', 'low', 'close', 'v', 'ct', 'qv', 'n', 'tb', 'tq', 'i'])
        df = df.astype({'open': float, 'high': float, 'low': float, 'close': float, 'v': float})
        df.rename(columns={'v': 'volume'}, inplace=True)
        return df
    except: return None

# --- 2. HABER ---
def fetch_news(symbol):
    try:
        coin = symbol.replace("USDT", "").upper()
        url = f"https://cryptopanic.com/news/rss/currency/{coin}/"
        feed = feedparser.parse(url)
        if feed.entries:
            return clean_markdown(feed.entries[0].title)
    except: return None
    return None

# --- 3. TEKNİK ---
def calculate_indicators(df):
    if df is None: return 0, 0, 0
    close = df['close']
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    rsi = 100 - (100 / (1 + gain / loss))
    ema_50 = close.ewm(span=50, adjust=False).mean()
    return close.iloc[-1], rsi.iloc[-1], ema_50.iloc[-1]

# --- 4. AI MOTORU (5 KATMANLI GARANTİ SİSTEMİ) ---
async def get_ai_comment(symbol, price, rsi, direction, score, news_title):
    news_text = f"Son Dakika: {news_title}" if news_title else "Haber Akışı: Nötr"
    
    prompt = (
        f"Dünyaca Ünlü Kripto Stratejisti gibi davran. Coin: {symbol}\n"
        f"Teknik Veri: Fiyat {price:.4f} | RSI {rsi:.1f} | Yön {direction} (Skor {score})\n"
        f"{news_text}\n"
        f"GÖREV: Yatırımcıya net bir yol haritası çiz.\n"
        f"ÇIKTI FORMATI:\n"
        f"ANALİZ: (Tekniği ve haberi 1 cümleyle yorumla)\n"
        f"STRATEJİ: (Long/Short veya Bekle)\n"
        f"GİRİŞ: (İdeal alım/satım bölgesi)\n"
        f"HEDEF (TP): (Kar alma noktası)\n"
        f"STOP (SL): (Zarar kesme noktası)"
    )
    headers = {'Content-Type': 'application/json'}
    
    # Sansür Engelleyici
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "safetySettings": [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
        ]
    }

    # --- İŞTE SENİN 5'Lİ YILDIZ TAKIMIN ---
    models = [
        # 1. KAPTAN (En Zeki) - Derin analiz yapar.
        ("Gemini 1.5 Pro", "gemini-1.5-pro", 25),      
        
        # 2. YARDIMCI KAPTAN (En Zeki Alternatif) - Kaptan meşgulse bu bakar.
        ("Gemini 1.5 Pro Latest", "gemini-1.5-pro-latest", 25),
        
        # 3. YENİ YILDIZ (Hızlı ve Zeki) - 2.0 teknolojisi.
        ("Gemini 2.0 Flash Exp", "gemini-2.0-flash-exp", 15),

        # 4. ORTA SAHA (Güvenilir) - Standart sağlam model.
        ("Gemini 1.5 Flash", "gemini-1.5-flash", 10),

        # 5. KALECİ (Hızlı Kurtarıcı) - Eğer hepsi hata verirse bu mutlaka tutar.
        ("Gemini 1.5 Flash-8B", "gemini-1.5-flash-8b", 8) 
    ]

    last_error = ""
    
    for name, model_id, timeout in models:
        try:
            print(f"🧠 Deneniyor: {name}...") 
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent?key={GEMINI_API_KEY}"
            resp = await asyncio.to_thread(requests.post, url, headers=headers, json=payload, timeout=timeout)
            
            if resp.status_code == 200:
                raw_text = resp.json()['candidates'][0]['content']['parts'][0]['text']
                return clean_markdown(raw_text) + f"\n\n_(🧠 Model: {name})_"
            else:
                last_error = f"Kod {resp.status_code}"
                continue
        except Exception as e:
            last_error = str(e)
            continue
            
    return f"⚠️ 5 Model de denendi ama sonuç alınamadı. (Son Hata: {last_error})"

# --- KOMUT ---
async def incele(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text("❌ Örnek: `/incele ETHUSDT`")
    symbol = context.args[0].upper()
    
    msg = await update.message.reply_text(f"🔍 *{symbol}* için 5 farklı yapay zeka taranıyor...", parse_mode='Markdown')

    df = fetch_data(symbol)
    if df is None: return await msg.edit_text("❌ Veri Hatası!")
    
    price, rsi, ema = calculate_indicators(df)
    news_title = fetch_news(symbol)
    
    score = 0
    if price > ema: score += 20
    if rsi < 30: score += 30
    elif rsi > 70: score -= 30
    
    if score >= 30: direction_icon, direction_text = "🚀", "GÜÇLÜ AL"
    elif score > 0: direction_icon, direction_text = "🟢", "AL"
    elif score > -30: direction_icon, direction_text = "🔴", "SAT"
    else: direction_icon, direction_text = "🩸", "GÜÇLÜ SAT"

    try: await msg.edit_text(f"✅ Veri Hazır. Analiz zinciri çalışıyor (1-5)...")
    except: pass

    comment = await get_ai_comment(symbol, price, rsi, direction_text, score, news_title)

    final_text = (
        f"💎 *{symbol} PENTAGON ANALİZ (V10.3)* 💎\n\n"
        f"💰 *Fiyat:* `{price:.4f}` $\n"
        f"📊 *RSI:* `{rsi:.2f}`\n"
        f"🧭 *Sinyal:* {direction_icon} *{direction_text}* (Skor: {score})\n"
        f"───────────────────\n"
        f"📰 *Haber:* {news_title if news_title else 'Nötr'}\n"
        f"───────────────────\n\n"
        f"🧠 *Uzman Görüşü:*\n{comment}"
    )
    
    try:
        await msg.edit_text(final_text, parse_mode='Markdown')
    except:
        await update.message.reply_text(final_text.replace("*", "").replace("`", ""))

if __name__ == '__main__':
    print("🚀 BOT V10.3 (THE PENTAGON) ÇALIŞIYOR...")
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("incele", incele))
    app.run_polling()
