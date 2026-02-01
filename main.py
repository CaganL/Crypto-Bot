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
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not TELEGRAM_TOKEN or not GROQ_API_KEY:
    print("❌ UYARI: API Anahtarları eksik! Railway Variables kontrol et.")
    pass

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO, force=True)

exchange = ccxt.binance({
    'enableRateLimit': True,
    'options': {'defaultType': 'future'}
})

def clean_markdown(text):
    if not text: return ""
    return text.replace("*", "").replace("_", "").replace("`", "").replace('"', '').replace("'", "")

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

# --- 4. AI MOTORU (V25.3 - KADEMELİ PUANLAMA) ---
async def get_ai_comment(symbol, price, rsi, direction, score, news_title, macro_low, macro_high, history_str):
    news_text = f"Haber: {news_title}" if news_title else "Haber Yok"
    
    prompt = (
        f"Sen Kıdemli bir Kripto Stratejistisin. {symbol} paritesini inceliyorsun.\n"
        f"Senin farkın, piyasayı siyah-beyaz değil, gri tonlarıyla (kademeli) okumandır.\n\n"
        f"📊 **TEKNİK VERİLER:**\n"
        f"- Fiyat: {price:.4f}\n"
        f"- RSI: {rsi:.1f} (Kademeli Değerlendirme)\n"
        f"- Sinyal Puanı: {score}/57 (Maksimum)\n"
        f"- Trend: {direction}\n"
        f"- Ana Dip: {macro_low:.4f}\n"
        f"- Ana Tepe: {macro_high:.4f}\n"
        f"- Haber: {news_text}\n\n"
        f"🕯️ **MUM HAREKETLERİ:**\n{history_str}\n\n"
        f"⚡ **GÖREVİN:**\n"
        f"Aşağıdaki şablonu kullanarak Türkçe bir analiz yaz. V17 tarzında, 'Sayın Yatırımcı' diye başla.\n"
        f"Eğer puan düşükse 'Henüz erken' de, yüksekse 'Fırsat' de.\n\n"
        f"**ŞABLON:**\n"
        f"Sayın Yatırımcı,\n"
        f"(RSI ve Trend durumunu yorumla.)\n\n"
        f"## 🔍 GENİŞ AÇI VE YAPISAL ANALİZ\n"
        f"**Konumlandırma:** (Fiyat nerede? Destek/Direnç?)\n"
        f"**Momentum:** (Piyasa yorgun mu, istekli mi?)\n\n"
        f"## ⚠️ RİSK VE TUZAK UYARISI\n"
        f"(Olası tehlikeler ve fake hareketler)\n\n"
        f"--- \n"
        f"## 🛠️ TİCARET PLANI: {symbol} ({direction})\n\n"
        f"| İŞLEM | SEVİYE | STRATEJİ |\n"
        f"| :--- | :--- | :--- |\n"
        f"| Giriş | (Fiyat Aralığı) | (Gerekçe) |\n"
        f"| Stop Loss | (Fiyat) | (Risk yönetimi) |\n"
        f"| Hedef 1 (TP1) | (Fiyat) | (Kar al) |\n"
        f"| Hedef 2 (TP2) | (Fiyat) | (Ana hedef) |\n\n"
        f"### 🧠 Analist Notu (R/R Analizi):\n"
        f"(İşlemin risk/kazanç oranını hesapla.)"
    )

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    
    payload = {
        "model": "llama-3.3-70b-versatile", 
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.6, 
        "max_tokens": 1500
    }

    try:
        response = await asyncio.to_thread(requests.post, url, headers=headers, json=payload, timeout=20)
        if response.status_code == 200:
            content = response.json()['choices'][0]['message']['content']
            return clean_markdown(content) + "\n\n_(🧠 V25.3: Hassas Terazi)_"
        else:
            return f"⚠️ Analiz Hatası: {response.text}"
    except Exception as e:
        return f"⚠️ Bağlantı Hatası: {str(e)}"

# --- KOMUT ---
async def incele(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text("❌ Örnek: `/incele BTCUSDT`")
    symbol = context.args[0].upper()
    
    msg = await update.message.reply_text(f"⚖️ *{symbol}* Kademeli Analiz (V25.3) yapılıyor...", parse_mode='Markdown')

    df = fetch_data(symbol)
    if df is None: return await msg.edit_text("❌ Borsa Verisi Yok!")
    
    price, rsi, ema, macro_low, macro_high, history_str = calculate_indicators(df)
    news_title = fetch_news(symbol)
    
    score = 0
    
    # 1. Trend Puanı (EMA) -> Maksimum 20 Puan
    if price > ema: score += 20
    
    # 2. RSI Puanı (Kademeli/Gradient)
    # --- ALIM BÖLGESİ ---
    if rsi < 30: 
        score += 30          # Tam Puan (Dip)
    elif 30 <= rsi < 35:
        score += 15          # Yarım Puan (Çok Yakın)
    elif 35 <= rsi < 40:
        score += 7           # Çeyrek Puan (Fırsat Başlıyor)

    # --- SATIŞ BÖLGESİ ---
    elif rsi > 70:
        score -= 30          # Tam Puan (Tepe)
    elif 65 < rsi <= 70:
        score -= 15          # Yarım Puan (Riskli)
    elif 60 < rsi <= 65:
        score -= 7           # Çeyrek Puan (Uyarı)
    
    # Sinyal Yorumlama
    if score >= 27: direction_icon, direction_text = "🚀", "GÜÇLÜ AL"
    elif score >= 15: direction_icon, direction_text = "🟢", "AL"
    elif score >= 7: direction_icon, direction_text = "👀", "TAKİBE AL (GİRİŞ ARANIYOR)"
    elif score > -7: direction_icon, direction_text = "⚪", "NÖTR/BEKLE"
    elif score > -15: direction_icon, direction_text = "⚠️", "DİKKAT (SATIŞ GELEBİLİR)"
    elif score > -27: direction_icon, direction_text = "🔴", "SAT"
    else: direction_icon, direction_text = "🩸", "GÜÇLÜ SAT"

    try: await msg.edit_text(f"✅ Skor hesaplandı: {score}. Yapay zeka yazıyor...")
    except: pass

    comment = await get_ai_comment(symbol, price, rsi, direction_text, score, news_title, macro_low, macro_high, history_str)

    final_text = (
        f"💎 *{symbol} HASSAS ANALİZ (V25.3)* 💎\n\n"
        f"💰 *Fiyat:* `{price:.4f}` $\n"
        f"📊 *Skor:* `{score}`\n"
        f"🧭 *Sinyal:* {direction_icon} *{direction_text}*\n"
        f"───────────────────\n"
        f"📰 *Haber:* {news_title if news_title else 'Akış Sakin'}\n"
        f"───────────────────\n\n"
        f"{comment}"
    )
    
    try:
        await msg.edit_text(final_text, parse_mode='Markdown')
    except:
        await update.message.reply_text(final_text.replace("*", "").replace("`", ""))

if __name__ == '__main__':
    print("🚀 BOT V25.3 (GRADIENT SCORING) BAŞLATILIYOR...")
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("incele", incele))
    app.run_polling(drop_pending_updates=True)
