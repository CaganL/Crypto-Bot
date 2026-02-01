import logging
import feedparser
import ccxt
import pandas as pd
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler
import asyncio
import os
import sys
import time
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold

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
        feed = feedparser.parse(url)
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

# --- 4. AI MOTORU (V20.1 - MULTI-MODEL SDK) ---
async def get_ai_comment(symbol, price, rsi, direction, score, news_title, macro_low, macro_high, history_str):
    news_text = f"Haber: {news_title}" if news_title else "Haber Yok"
    
    prompt = (
        f"Kripto Analistisin. Coin: {symbol}\n"
        f"ANLIK: Fiyat {price:.4f} | RSI {rsi:.1f} | Yön {direction}\n"
        f"GENİŞ AÇI: Dip {macro_low:.4f} | Tepe {macro_high:.4f}\n"
        f"MUM GEÇMİŞİ:\n{history_str}\n"
        f"{news_text}\n"
        f"GÖREV: Teknik analiz yap. Destek/Direnç ver. Yatırım tavsiyesi vermeden strateji oluştur."
    )

    safety_settings = {
        HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
    }

    # Google'ın tanıması muhtemel modeller listesi
    # 404 hatası almamak için sırayla dener
    models_to_try = [
        "gemini-1.5-flash",    # En yenisi
        "gemini-1.5-pro",      # En zekisi
        "gemini-1.0-pro",      # En eskisi (Garanti çalışır)
        "gemini-pro"           # Klasik isim
    ]

    last_error = ""

    # Tüm modelleri ve anahtarları kombine edip deniyoruz
    for model_name in models_to_try:
        print(f"🧠 Model deneniyor: {model_name}...")
        
        for api_key in API_KEYS:
            key_short = f"...{api_key[-4:]}"
            
            try:
                genai.configure(api_key=api_key)
                model = genai.GenerativeModel(model_name)
                
                # İsteği atıyoruz
                response = await asyncio.to_thread(
                    model.generate_content,
                    prompt,
                    safety_settings=safety_settings
                )
                
                if response.text:
                    return clean_markdown(response.text) + f"\n\n_(🛡️ SDK: {model_name} | 🔑 {key_short})_"
            
            except Exception as e:
                # 404 hatası gelirse (model bulunamadı) diğer modele geçmek için sessiz kalıyoruz
                error_msg = str(e)
                if "404" in error_msg or "not found" in error_msg:
                    print(f"  ❌ {model_name} bulunamadı. Sıradakine geçiliyor.")
                else:
                    print(f"  ⚠️ Hata ({model_name} - {key_short}): {error_msg}")
                    last_error = error_msg
                continue

    return f"⚠️ Analiz başarısız. Google SDK bağlantısı kuruldu ama model hatası alındı.\nSon Hata: {last_error}"

# --- KOMUT ---
async def incele(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text("❌ Örnek: `/incele BTCUSDT`")
    symbol = context.args[0].upper()
    
    msg = await update.message.reply_text(f"🛡️ *{symbol}* Google SDK V20.1 (Fixer) devrede...", parse_mode='Markdown')

    df = fetch_data(symbol)
    if df is None: return await msg.edit_text("❌ Borsa Verisi Yok!")
    
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

    try: await msg.edit_text(f"✅ Google Sunucusuna Girildi. Model seçiliyor...")
    except: pass

    comment = await get_ai_comment(symbol, price, rsi, direction_text, score, news_title, macro_low, macro_high, history_str)

    final_text = (
        f"💎 *{symbol} SDK ANALİZ (V20.1)* 💎\n\n"
        f"💰 *Fiyat:* `{price:.4f}` $\n"
        f"📊 *Sinyal:* {direction_icon} *{direction_text}* (Skor: {score})\n"
        f"───────────────────\n"
        f"🧠 *AI Yorumu:*\n{comment}"
    )
    
    try:
        await msg.edit_text(final_text, parse_mode='Markdown')
    except:
        await update.message.reply_text(final_text.replace("*", "").replace("`", ""))

if __name__ == '__main__':
    print("🚀 BOT V20.1 (FIXER) BAŞLATILIYOR...")
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("incele", incele))
    app.run_polling(drop_pending_updates=True)
