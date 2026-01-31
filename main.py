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

# --- 1. VERİ ---
def fetch_data(symbol, timeframe='4h'):
    try:
        # Son 100 mumu çekiyoruz (Hem Macro hem Micro analiz için)
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
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=5)
        feed = feedparser.parse(response.content)
        if feed.entries:
            return clean_markdown(feed.entries[0].title)
    except: return None
    return None

# --- 3. TEKNİK (HİBRİT ANALİZ VERİSİ) ---
def calculate_indicators(df):
    if df is None: return 0, 0, 0, 0, 0, ""
    close = df['close']
    
    # RSI & EMA
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    rsi = 100 - (100 / (1 + gain / loss))
    ema_50 = close.ewm(span=50, adjust=False).mean()
    
    # --- MACRO VIEW (GENİŞ AÇI - 100 MUM) ---
    # Son 100 mumun (yaklaşık 16 gün) en düşüğü ve en yükseği
    macro_low = df['low'].min()
    macro_high = df['high'].max()
    
    # --- MICRO VIEW (YAKIN ÇEKİM - 12 MUM) ---
    # Son 12 mumun (48 Saat) detaylı hareketi
    history_str = ""
    last_candles = df.tail(12) 
    
    for index, row in last_candles.iterrows():
        # Her mumun detayını yazıyoruz
        history_str += f"[H:{row['high']:.0f}|L:{row['low']:.0f}|C:{row['close']:.0f}] "

    return close.iloc[-1], rsi.iloc[-1], ema_50.iloc[-1], macro_low, macro_high, history_str

# --- 4. AI MOTORU (ÇİFT GÖZLÜK) ---
async def get_ai_comment(symbol, price, rsi, direction, score, news_title, macro_low, macro_high, history_str):
    news_text = f"Haber: {news_title}" if news_title else "Haber Yok"
    
    prompt = (
        f"Sen Kıdemli Kripto Analistisin. Coin: {symbol}\n"
        f"Anlık Fiyat: {price:.2f} | RSI: {rsi:.1f} | Yön: {direction}\n\n"
        f"1. GENİŞ AÇI (Son 16 Günün Özeti):\n"
        f"   - Ana Destek (Dip): {macro_low:.2f}\n"
        f"   - Ana Direnç (Tepe): {macro_high:.2f}\n\n"
        f"2. YAKIN ÇEKİM (Son 48 Saatin Hareketi):\n"
        f"   {history_str}\n\n"
        f"{news_text}\n\n"
        f"GÖREV: Bu iki veriyi birleştir.\n"
        f"- Eğer kısa vadeli hareket dar bir alandaysa (Range), geniş açıdaki ana destek/dirençlere göre uyar.\n"
        f"- Tuzağa düşme. Net bir Giriş, Hedef ve Stop stratejisi kur."
    )
    headers = {'Content-Type': 'application/json'}
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    # RESMİ MODEL LİSTESİ
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
    
    msg = await update.message.reply_text(f"🔍 *{symbol}* için Geniş ve Dar açı analiz ediliyor...", parse_mode='Markdown')

    df = fetch_data(symbol)
    if df is None: return await msg.edit_text("❌ Veri Hatası!")
    
    # 6 Değer dönüyor (Macro + Micro veriler)
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

    try: await msg.edit_text(f"✅ Büyük Resim ve Detaylar hazır. AI yorumluyor...")
    except: pass

    comment = await get_ai_comment(symbol, price, rsi, direction_text, score, news_title, macro_low, macro_high, history_str)

    final_text = (
        f"💎 *{symbol} HYBRID ANALİZ (V17.0)* 💎\n\n"
        f"💰 *Fiyat:* `{price:.4f}` $\n"
        f"🌍 *Ana Dip (16 Gün):* `{macro_low:.2f}`\n"
        f"🏔️ *Ana Tepe (16 Gün):* `{macro_high:.2f}`\n"
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
    print("🚀 BOT V17.0 (THE HYBRID EYE) ÇALIŞIYOR...")
    sys.stdout.flush()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("incele", incele))
    app.run_polling()
