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

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO, force=True)

exchange = ccxt.binance({
    'enableRateLimit': True,
    'options': {'defaultType': 'future'}
})

# --- TEMİZLEYİCİ ---
def clean_markdown(text):
    if not text: return ""
    return text.replace("*", "").replace("_", "").replace("`", "").replace("[", "").replace("]", "")

# --- 1. VERİ ÇEKME ---
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

# --- 2. HABERLER (KİMLİK KARTI EKLENDİ) ---
def fetch_news(symbol):
    try:
        coin = symbol.replace("USDT", "").upper()
        # RSS beslemesini requests ile çekip, user-agent ekliyoruz
        url = f"https://cryptopanic.com/news/rss/currency/{coin}/"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=5)
        
        # Gelen veriyi feedparser'a ver
        feed = feedparser.parse(response.content)
        
        if feed.entries:
            return clean_markdown(feed.entries[0].title)
    except Exception as e:
        print(f"Haber Hatası: {e}")
        return None
    return None

# --- 3. TEKNİK (DESTEK/DİRENÇ EKLENDİ) ---
def calculate_indicators(df):
    if df is None: return 0, 0, 0, 0, 0
    close = df['close']
    
    # RSI
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    rsi = 100 - (100 / (1 + gain / loss))
    
    # EMA 50
    ema_50 = close.ewm(span=50, adjust=False).mean()
    
    # --- YENİ: DESTEK VE DİRENÇ HESAPLAMA ---
    # Son 50 mumun en düşüğü (Destek) ve en yükseği (Direnç)
    support = df['low'].tail(50).min()
    resistance = df['high'].tail(50).max()
    
    return close.iloc[-1], rsi.iloc[-1], ema_50.iloc[-1], support, resistance

# --- 4. AI MOTORU (MATEMATİK DESTEKLİ) ---
async def get_ai_comment(symbol, price, rsi, direction, score, news_title, support, resistance):
    news_text = f"Son Dakika: {news_title}" if news_title else "Piyasa Haberi Yok"
    
    prompt = (
        f"Kripto Analistisin. Coin: {symbol}\n"
        f"Veriler: Fiyat {price:.2f} | RSI {rsi:.1f} | Yön {direction}\n"
        f"Teknik Seviyeler: Ana Destek {support:.2f} | Ana Direnç {resistance:.2f}\n"
        f"{news_text}\n"
        f"GÖREV: Bu teknik seviyeleri (Destek/Direnç) kullanarak mantıklı bir işlem stratejisi kur.\n"
        f"ÇIKTI: Net bir Giriş, Hedef ve Stop noktası ver."
    )
    headers = {'Content-Type': 'application/json'}
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    # RESMİ MODEL LİSTESİ (V13'ten devam)
    models = [
        ("Gemini 2.5 Pro", "gemini-2.5-pro"),
        ("Gemini 3.0 Pro Preview", "gemini-3-pro-preview"),
        ("Gemini 2.5 Flash", "gemini-2.5-flash"),
        ("Gemini 2.0 Flash", "gemini-2.0-flash"),
        ("Gemini Flash Latest", "gemini-flash-latest")
    ]

    last_error = ""
    for name, model_id in models:
        try:
            print(f"🧠 Deneniyor: {name}...") 
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent?key={GEMINI_API_KEY}"
            resp = await asyncio.to_thread(requests.post, url, headers=headers, json=payload, timeout=12)
            
            if resp.status_code == 200:
                raw_text = resp.json()['candidates'][0]['content']['parts'][0]['text']
                return clean_markdown(raw_text) + f"\n\n_(🧠 Model: {name})_"
            else:
                last_error += f"\n{name}: {resp.status_code}"
                continue
        except: continue
            
    return f"⚠️ Analiz başarısız. Detay:\n{last_error}"

# --- KOMUT ---
async def incele(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text("❌ Örnek: `/incele BTCUSDT`")
    symbol = context.args[0].upper()
    
    msg = await update.message.reply_text(f"🔍 *{symbol}* taranıyor...", parse_mode='Markdown')

    df = fetch_data(symbol)
    if df is None: return await msg.edit_text("❌ Veri Hatası!")
    
    # Yeni fonksiyonu çağır (5 değer dönecek)
    price, rsi, ema, support, resistance = calculate_indicators(df)
    news_title = fetch_news(symbol)
    
    score = 0
    if price > ema: score += 20
    if rsi < 30: score += 30
    elif rsi > 70: score -= 30
    
    if score >= 30: direction_icon, direction_text = "🚀", "GÜÇLÜ AL"
    elif score > 0: direction_icon, direction_text = "🟢", "AL"
    elif score > -30: direction_icon, direction_text = "🔴", "SAT"
    else: direction_icon, direction_text = "🩸", "GÜÇLÜ SAT"

    try: await msg.edit_text(f"✅ Veri ve Haberler çekildi. AI düşünüyor...")
    except: pass

    # AI'ya yeni parametreleri gönder
    comment = await get_ai_comment(symbol, price, rsi, direction_text, score, news_title, support, resistance)

    final_text = (
        f"💎 *{symbol} ANALİST (V14.0)* 💎\n\n"
        f"💰 *Fiyat:* `{price:.4f}` $\n"
        f"📊 *RSI:* `{rsi:.2f}`\n"
        f"🛡️ *Destek:* `{support:.2f}`\n"
        f"🚧 *Direnç:* `{resistance:.2f}`\n"
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
    print("🚀 BOT V14.0 (THE ANALYST) ÇALIŞIYOR...")
    sys.stdout.flush()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("incele", incele))
    app.run_polling()
