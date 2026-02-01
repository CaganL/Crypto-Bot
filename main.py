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
import json

# --- GÜVENLİK ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not TELEGRAM_TOKEN or not GROQ_API_KEY:
    # Kod çökmesin ama loga yazsın
    print("❌ UYARI: API Anahtarları eksik olabilir. Railway Variables kontrol et.")
    pass

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

# --- 4. AI MOTORU (GROQ - DETAYLI + TABLO) ---
async def get_ai_comment(symbol, price, rsi, direction, score, news_title, macro_low, macro_high, history_str):
    news_text = f"Haber: {news_title}" if news_title else "Haber Yok"
    
    # --- YENİ PROMPT: Hem Analiz Hem Tablo ---
    prompt = (
        f"Sen tecrübeli bir Kripto Stratejistisin. {symbol} paritesini inceliyorsun.\n\n"
        f"📊 **TEKNİK VERİLER:**\n"
        f"- Fiyat: {price:.4f}\n"
        f"- RSI: {rsi:.1f}\n"
        f"- Trend Sinyali: {direction}\n"
        f"- Ana Destek: {macro_low:.4f}\n"
        f"- Ana Direnç: {macro_high:.4f}\n"
        f"- Haber: {news_text}\n\n"
        f"🕯️ **MUM HAREKETLERİ:**\n{history_str}\n\n"
        f"⚡ **GÖREVİN:**\n"
        f"1. **PİYASA YORUMU:** Önce grafikte gördüklerini, mum formasyonlarını ve piyasa psikolojisini detaylıca açıkla (3-4 cümle). Yatırımcıya ne olup bittiğini anlat.\n"
        f"2. **STRATEJİ TABLOSU:** Ardından net rakamlarla aşağıdaki tabloyu doldur.\n\n"
        f"Formatın tam olarak şöyle olsun (Türkçe):\n\n"
        f"📝 **PİYASA ANALİZİ:**\n"
        f"(Buraya detaylı yorumunu yaz...)\n\n"
        f"🎯 **İŞLEM KURULUMU:**\n"
        f"🔵 **GİRİŞ:** (Net fiyat)\n"
        f"🟢 **TP1:** (Kar al 1)\n"
        f"🟢 **TP2:** (Kar al 2)\n"
        f"🔴 **STOP:** (Zarar kes)\n"
        f"⚠️ **RİSK:** (Kısa uyarı)"
    )

    url = "https://api.groq.com/openai/v1/chat/completions"
    
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "llama3-70b-8192", 
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.6, # Biraz daha yaratıcı olsun diye 0.6 yaptık
        "max_tokens": 1024  # Daha uzun yazabilsin diye artırdık
    }

    print(f"⚡ Groq (Hybrid Mod) isteği gönderiliyor...")

    try:
        response = await asyncio.to_thread(requests.post, url, headers=headers, json=payload, timeout=20)
        
        if response.status_code == 200:
            data = response.json()
            content = data['choices'][0]['message']['content']
            return clean_markdown(content) + "\n\n_(⚡ Llama 3 - 70B | Groq)_"
        else:
            error_msg = response.text
            print(f"❌ Groq Hatası: {error_msg}")
            return f"⚠️ Analiz alınamadı. Groq Hatası: {response.status_code}"

    except Exception as e:
        print(f"❌ Bağlantı Hatası: {str(e)}")
        return f"⚠️ Bağlantı hatası: {str(e)}"

# --- KOMUT ---
async def incele(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text("❌ Örnek: `/incele BTCUSDT`")
    symbol = context.args[0].upper()
    
    msg = await update.message.reply_text(f"🧠 *{symbol}* Detaylı Analiz (V22.2) hazırlanıyor...", parse_mode='Markdown')

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

    try: await msg.edit_text(f"✅ Veriler Groq'a iletildi. Yapay zeka düşünüyor...")
    except: pass

    comment = await get_ai_comment(symbol, price, rsi, direction_text, score, news_title, macro_low, macro_high, history_str)

    final_text = (
        f"💎 *{symbol} HIBRIT ANALİZ (V22.2)* 💎\n\n"
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
    print("🚀 BOT V22.2 (HYBRID COMMANDER) BAŞLATILIYOR...")
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("incele", incele))
    app.run_polling(drop_pending_updates=True)
