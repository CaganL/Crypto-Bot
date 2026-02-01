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

# --- 1. VERİ ÇEKME (ÇOKLU ZAMAN) ---
def fetch_data(symbol, timeframe):
    try:
        # 100 mum hesaplamalar için yeterli
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

# --- 3. MATEMATİK MOTORU (ATR, HACİM, RSI) ---
def calculate_advanced_indicators(df):
    if df is None: return None
    
    close = df['close']
    high = df['high']
    low = df['low']
    volume = df['volume']
    
    # RSI Hesapla
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    rsi = 100 - (100 / (1 + gain / loss))
    
    # EMA 50 (Trend)
    ema_50 = close.ewm(span=50, adjust=False).mean()
    
    # Hacim Ortalaması (SMA 20) - Hacim patlamasını bulmak için
    vol_sma = volume.rolling(window=20).mean()
    
    # --- ATR HESAPLAMA (Volatilite Stopu İçin) ---
    # TR = Max(High-Low, Abs(High-PrevClose), Abs(Low-PrevClose))
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/14, adjust=False).mean()
    
    # Sonuçları Paketle
    return {
        'price': close.iloc[-1],
        'rsi': rsi.iloc[-1],
        'ema_50': ema_50.iloc[-1],
        'volume': volume.iloc[-1],
        'vol_avg': vol_sma.iloc[-1],
        'atr': atr.iloc[-1],
        'macro_low': low.min(),
        'macro_high': high.max(),
        'last_candles': df.tail(5) # Son 5 mumu AI'ya gönder
    }

# --- 4. AI MOTORU (V26.0 - SNIPER ELITE) ---
async def get_ai_comment(symbol, data_4h, data_15m, score, direction, news_title, tp_sl_data):
    news_text = f"Haber: {news_title}" if news_title else "Haber Yok"
    
    # Mum geçmişini metne dök
    history_str = ""
    for index, row in data_15m['last_candles'].iterrows():
        time_str = row['timestamp'].strftime('%H:%M')
        history_str += f"* {time_str} -> Fiyat: {row['close']:.4f}\n"

    prompt = (
        f"Sen Elit Seviye bir Algoritmik Tradersın. {symbol} paritesini 'Çoklu Zaman Dilimi' ile inceliyorsun.\n\n"
        f"📊 **4 SAATLİK (GENEL TREND):**\n"
        f"- Trend Durumu: {'YÜKSELİŞ (EMA Üstü)' if data_4h['price'] > data_4h['ema_50'] else 'DÜŞÜŞ (EMA Altı)'}\n"
        f"- RSI (4h): {data_4h['rsi']:.1f}\n"
        f"- Ana Destek: {data_4h['macro_low']:.4f}\n\n"
        f"🎯 **15 DAKİKALIK (GİRİŞ TETİĞİ):**\n"
        f"- Fiyat: {data_15m['price']:.4f}\n"
        f"- RSI (15m): {data_15m['rsi']:.1f}\n"
        f"- Hacim Durumu: {'🔥 HACİM PATLAMASI' if data_15m['volume'] > data_15m['vol_avg'] else 'Normal Hacim'}\n"
        f"- Volatilite (ATR): {data_15m['atr']:.4f}\n\n"
        f"📈 **OTOMATİK HESAPLANAN HEDEFLER (ATR BAZLI):**\n"
        f"- Önerilen Stop (SL): {tp_sl_data['sl']:.4f}\n"
        f"- Hedef 1 (TP1 - 1.5R): {tp_sl_data['tp1']:.4f}\n"
        f"- Hedef 2 (TP2 - 3.0R): {tp_sl_data['tp2']:.4f}\n\n"
        f"⚡ **SKOR:** {score}/60\n"
        f"⚡ **KARAR:** {direction}\n\n"
        f"**GÖREVİN:**\n"
        f"Bu verileri kullanarak 'Sayın Yatırımcı' hitabıyla profesyonel bir analiz yaz. "
        f"4 Saatlik trendi ve 15 dakikalık giriş fırsatını birleştir. ATR bazlı stop noktalarının neden güvenli olduğunu açıkla.\n\n"
        f"**ŞABLON:**\n"
        f"Sayın Yatırımcı,\n(Genel durumu özetle)\n\n"
        f"## 🦅 SNIPER ANALİZİ (Multi-Timeframe)\n"
        f"**Makro Görünüm (4 Saatlik):** (Ana yön ne?)\n"
        f"**Mikro Tetik (15 Dakikalık):** (RSI ve Hacim girişi destekliyor mu?)\n\n"
        f"## 🛡️ ATR TABANLI RİSK YÖNETİMİ\n"
        f"(ATR değerine göre stop noktasının mantığını anlat)\n\n"
        f"--- \n"
        f"## 🚀 İŞLEM KURULUMU: {symbol}\n\n"
        f"| İŞLEM | SEVİYE | AÇIKLAMA |\n"
        f"| :--- | :--- | :--- |\n"
        f"| **GİRİŞ** | {data_15m['price']:.4f} | Anlık Fiyat |\n"
        f"| **STOP (SL)** | {tp_sl_data['sl']:.4f} | ATR ile hesaplanmış güvenli bölge |\n"
        f"| **HEDEF 1** | {tp_sl_data['tp1']:.4f} | İlk kar alımı |\n"
        f"| **HEDEF 2** | {tp_sl_data['tp2']:.4f} | Ana yükseliş hedefi |\n\n"
        f"### 🧠 Algoritma Notu:\n(Risk/Kazanç oranını yorumla)"
    )

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    
    payload = {
        "model": "llama-3.3-70b-versatile", 
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.5, 
        "max_tokens": 1500
    }

    try:
        response = await asyncio.to_thread(requests.post, url, headers=headers, json=payload, timeout=20)
        if response.status_code == 200:
            content = response.json()['choices'][0]['message']['content']
            return clean_markdown(content) + "\n\n_(🦅 V26.0: ATR + Hacim + Multi-Timeframe)_"
        else:
            return f"⚠️ Analiz Hatası: {response.text}"
    except Exception as e:
        return f"⚠️ Bağlantı Hatası: {str(e)}"

# --- KOMUT ---
async def incele(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text("❌ Örnek: `/incele BTCUSDT`")
    symbol = context.args[0].upper()
    
    msg = await update.message.reply_text(f"🦅 *{symbol}* Sniper Elite (V26.0) hazırlanıyor...", parse_mode='Markdown')

    # 1. İki farklı zaman dilimini çek
    df_4h = fetch_data(symbol, '4h')
    df_15m = fetch_data(symbol, '15m')
    
    if df_4h is None or df_15m is None: return await msg.edit_text("❌ Borsa Verisi Alınamadı!")
    
    # 2. İndikatörleri Hesapla
    data_4h = calculate_advanced_indicators(df_4h)
    data_15m = calculate_advanced_indicators(df_15m)
    news_title = fetch_news(symbol)
    
    # 3. PUANLAMA MOTORU (Scoring Engine)
    score = 0
    
    # A) 4 Saatlik Trend (Ana Yön) - Max 20 Puan
    if data_4h['price'] > data_4h['ema_50']: score += 20
    
    # B) 15 Dakikalık RSI (Giriş) - Max 30 Puan (Kademeli)
    rsi_15 = data_15m['rsi']
    if rsi_15 < 30: score += 30
    elif rsi_15 < 35: score += 15
    elif rsi_15 < 40: score += 7
    elif rsi_15 > 70: score -= 30
    elif rsi_15 > 65: score -= 15
    
    # C) Hacim Teyidi (Bonus) - Max 10 Puan
    if data_15m['volume'] > data_15m['vol_avg']: score += 10
    
    # 4. ATR BAZLI HEDEF HESAPLAMA (Otomatik Stop/TP)
    # ATR değerini al (15 dakikalık oynaklık)
    atr = data_15m['atr']
    current_price = data_15m['price']
    
    # Strateji: Long ise Stop aşağıda, Short ise Stop yukarıda
    # (Şimdilik basitlik için LONG senaryosu hesaplıyoruz, AI yönü düzeltecek)
    tp_sl_data = {
        'sl': current_price - (2.0 * atr),   # 2 ATR aşağısı Stop
        'tp1': current_price + (3.0 * atr),  # 3 ATR yukarısı TP1 (1.5R)
        'tp2': current_price + (5.0 * atr)   # 5 ATR yukarısı TP2 (2.5R)
    }
    
    # Sinyal Yönü
    if score >= 35: icon, text = "🚀", "GÜÇLÜ AL (SNIPER)"
    elif score >= 20: icon, text = "🟢", "AL"
    elif score >= 10: icon, text = "👀", "TAKİBE AL"
    elif score > -10: icon, text = "⚪", "NÖTR"
    elif score > -25: icon, text = "🔴", "SAT"
    else: icon, text = "🩸", "GÜÇLÜ SAT"

    try: await msg.edit_text(f"✅ 4H Trend ve 15m ATR incelendi (Skor: {score}). Yazılıyor...")
    except: pass

    comment = await get_ai_comment(symbol, data_4h, data_15m, score, text, news_title, tp_sl_data)

    final_text = (
        f"🦅 *{symbol} SNIPER ELITE (V26.0)* 🦅\n\n"
        f"💰 *Fiyat:* `{data_15m['price']:.4f}` $\n"
        f"📊 *Skor:* `{score}` / 60\n"
        f"⏱️ *ATR (15m):* `{data_15m['atr']:.4f}` (Volatilite)\n"
        f"🧭 *Sinyal:* {icon} *{text}*\n"
        f"───────────────────\n"
        f"{comment}"
    )
    
    try:
        await msg.edit_text(final_text, parse_mode='Markdown')
    except:
        await update.message.reply_text(final_text.replace("*", "").replace("`", ""))

if __name__ == '__main__':
    print("🚀 BOT V26.0 (MULTI-TIMEFRAME + ATR) BAŞLATILIYOR...")
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("incele", incele))
    app.run_polling(drop_pending_updates=True)
