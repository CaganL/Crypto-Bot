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

# Logları hemen basması için force=True
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

# --- 4. AI MOTORU (RESMİ LİSTEYE GÖRE GÜNCELLENDİ) ---
async def get_ai_comment(symbol, price, rsi, direction, score, news_title):
    news_text = f"Haber: {news_title}" if news_title else "Haber Yok"
    
    prompt = (
        f"Kripto Analistisin. Coin: {symbol}\n"
        f"Fiyat {price:.4f} | RSI {rsi:.1f} | Yön {direction}\n"
        f"{news_text}\n"
        f"GÖREV: Net bir AL/SAT stratejisi yaz. Giriş, Hedef ve Stop ver."
    )
    headers = {'Content-Type': 'application/json'}
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    # --- SENİN LİSTENDEN SEÇİLEN KADRO ---
    models = [
        # 1. EN ZEKİ (Amiral Gemisi)
        ("Gemini 2.5 Pro", "gemini-2.5-pro"),
        
        # 2. EN YENİ VE ZEKİ (Alternatif)
        ("Gemini 3.0 Pro Preview", "gemini-3-pro-preview"),
        
        # 3. YENİ NESİL HIZLI (Dengeleyici)
        ("Gemini 2.5 Flash", "gemini-2.5-flash"),
        
        # 4. GÜVENİLİR HIZLI (Sağlamcı)
        ("Gemini 2.0 Flash", "gemini-2.0-flash"),
        
        # 5. SON KALE (Bu model asla ölmez)
        ("Gemini Flash Latest", "gemini-flash-latest")
    ]

    last_error = ""
    
    for name, model_id in models:
        try:
            print(f"🧠 Deneniyor: {name} ({model_id})...") 
            # API URL'sini senin verdiğin "models/" ön ekini dikkate alarak düzenledim
            # Not: API çağrısında bazen "models/" kısmı url'de zaten vardır, bazen yoktur.
            # En güvenli yöntem direkt model adını kullanmaktır.
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent?key={GEMINI_API_KEY}"
            
            resp = await asyncio.to_thread(requests.post, url, headers=headers, json=payload, timeout=12)
            
            if resp.status_code == 200:
                print(f"✅ BAŞARILI: {name}")
                raw_text = resp.json()['candidates'][0]['content']['parts'][0]['text']
                return clean_markdown(raw_text) + f"\n\n_(🧠 Çalışan Model: {name})_"
            else:
                error_msg = f"Kod {resp.status_code}"
                print(f"❌ {name} Başarısız: {error_msg}")
                last_error += f"\n{name}: {error_msg}"
                continue
        except Exception as e:
            print(f"⚠️ {name} Hatası: {str(e)}")
            last_error += f"\n{name}: {str(e)}"
            continue
            
    return f"⚠️ Hiçbir model çalışmadı. Detay:\n{last_error}"

# --- KOMUT ---
async def incele(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text("❌ Örnek: `/incele BTCUSDT`")
    symbol = context.args[0].upper()
    
    msg = await update.message.reply_text(f"🔍 *{symbol}* için listedeki 5 model deneniyor...", parse_mode='Markdown')

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

    try: await msg.edit_text(f"✅ Veri Tamam. Uygun model aranıyor...")
    except: pass

    comment = await get_ai_comment(symbol, price, rsi, direction_text, score, news_title)

    final_text = (
        f"💎 *{symbol} ANALİZ (V13.0)* 💎\n\n"
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
    print("🚀 BOT V13.0 (OFFICIAL LIST) ÇALIŞIYOR...")
    sys.stdout.flush()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("incele", incele))
    app.run_polling()
