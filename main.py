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

# 3 Anahtarı da kullanıyoruz (Eski V17 tek anahtardı, bu daha güçlü)
API_KEYS = []
if os.getenv("GEMINI_API_KEY"): API_KEYS.append(os.getenv("GEMINI_API_KEY"))
if os.getenv("GEMINI_API_KEY_2"): API_KEYS.append(os.getenv("GEMINI_API_KEY_2"))
if os.getenv("GEMINI_API_KEY_3"): API_KEYS.append(os.getenv("GEMINI_API_KEY_3"))

if not TELEGRAM_TOKEN or not API_KEYS:
    print("❌ HATA: API Anahtarları EKSİK!")
    sys.exit(1)

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO, force=True)

exchange = ccxt.binance({
    'enableRateLimit': True,
    'options': {'defaultType': 'future'}
})

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

# --- 4. AI MOTORU (V17 - FLASH VERSİYON) ---
async def get_ai_comment(symbol, price, rsi, direction, score, news_title, macro_low, macro_high, history_str):
    news_text = f"Haber: {news_title}" if news_title else "Haber Yok"
    
    # Orijinal V17 Prompt'u
    prompt = (
        f"Sen Kıdemli Kripto Analistisin. Coin: {symbol}\n"
        f"ANLIK: Fiyat {price:.4f} | RSI {rsi:.1f} | Yön {direction}\n"
        f"GENİŞ AÇI: Dip {macro_low:.4f} | Tepe {macro_high:.4f}\n"
        f"MUM GEÇMİŞİ:\n{history_str}\n"
        f"{news_text}\n"
        f"GÖREV: Bana 'Sayın Yatırımcı' diye hitap et. Teknik analizi geniş açıdan yap. Tuzaklara dikkat çek. "
        f"Net giriş, stop ve hedef noktaları ver. R/R oranını hesapla."
    )
    headers = {'Content-Type': 'application/json'}
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    # BURAYI DEĞİŞTİRDİM: Flash modeli çok daha hızlıdır ve timeout yemez.
    target_model = "gemini-1.5-flash"
    
    last_error = ""

    for i, api_key in enumerate(API_KEYS):
        key_short = f"...{api_key[-4:]}"
        print(f"🔄 [V17.0] Gemini Flash deneniyor (Key: {key_short})...")
        
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{target_model}:generateContent?key={api_key}"
            
            # Flash hızlı olduğu için 15sn yeter, fazlası Railway'i yorar
            resp = await asyncio.to_thread(requests.post, url, headers=headers, json=payload, timeout=15)
            
            if resp.status_code == 200:
                raw_text = resp.json()['candidates'][0]['content']['parts'][0]['text']
                return clean_markdown(raw_text) + f"\n\n_(🧠 Model: Gemini 1.5 Flash)_"
            
            elif resp.status_code == 429:
                print(f"  ⚠️ Kota Dolu (Key: {key_short}). Diğerine geçiliyor.")
                continue
            
            else:
                error_msg = resp.text
                print(f"  ⚠️ Hata: {resp.status_code}")
                last_error = f"Google Hata Kodu: {resp.status_code}"
                continue
                
        except Exception as e:
            print(f"  ⚠️ Bağlantı Sorunu: {str(e)}")
            last_error = "Zaman Aşımı (Timeout) - Railway IP Ban"
            continue

    return f"⚠️ V17.0 (Flash) Başarısız Oldu.\nSebep: {last_error}"

# --- KOMUT ---
async def incele(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text("❌ Örnek: `/incele BTCUSDT`")
    symbol = context.args[0].upper()
    
    msg = await update.message.reply_text(f"🚀 *{symbol}* için V17.0 (Flash) çalıştırılıyor...", parse_mode='Markdown')

    df = fetch_data(symbol)
    if df is None: return await msg.edit_text("❌ Veri Yok!")
    
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

    try: await msg.edit_text(f"✅ Veriler Google'a gönderildi (Hızlı Mod). Bekleniyor...")
    except: pass

    comment = await get_ai_comment(symbol, price, rsi, direction_text, score, news_title, macro_low, macro_high, history_str)

    final_text = (
        f"💎 *{symbol} V17.0 FLASH* 💎\n\n"
        f"💰 *Fiyat:* `{price:.4f}` $\n"
        f"📊 *Sinyal:* {direction_icon} *{direction_text}* (Skor: {score})\n"
        f"───────────────────\n"
        f"{comment}"
    )
    
    try:
        await msg.edit_text(final_text, parse_mode='Markdown')
    except:
        await update.message.reply_text(final_text.replace("*", "").replace("`", ""))

if __name__ == '__main__':
    print("🚀 BOT V17.0 FLASH BAŞLATILIYOR...")
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("incele", incele))
    app.run_polling(drop_pending_updates=True)
