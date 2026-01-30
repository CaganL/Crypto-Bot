import logging
import feedparser
import ccxt
import pandas as pd
import pandas_ta as ta
import google.generativeai as genai
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler
import asyncio
import os # Şifreleri sistemden okumak için gerekli kütüphane

# --- GÜVENLİK AYARLARI ---
# Kodun içinde şifre YOK! Railway'den çekiyoruz.
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Kontrol: Eğer Railway'e eklenmemişse bot hata verip dursun.
if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    raise ValueError("❌ HATA: API Anahtarları bulunamadı! Railway Variables kısmına eklediğinden emin ol.")

# İzleme Listesi
WATCHLIST = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "AVAXUSDT", "DOGEUSDT", "PEPEUSDT"]

# Gemini Ayarları
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- 1. VERİ ÇEKME ---
def fetch_data(symbol, timeframe):
    exchange = ccxt.binance()
    try:
        bars = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=100)
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        return df
    except:
        return None

# --- 2. HABERLER (RSS) ---
def fetch_news(symbol):
    coin_ticker = symbol.replace("USDT", "").upper()
    rss_url = f"https://cryptopanic.com/news/rss/currency/{coin_ticker}/"
    try:
        feed = feedparser.parse(rss_url)
        return [entry.title for entry in feed.entries[:3]] if feed.entries else []
    except:
        return []

# --- 3. ANALİZ MOTORU (V3.2 - Tam Kademeli) ---
def analyze_market(symbol):
    # Veri Çekme
    df_4h = fetch_data(symbol, '4h')
    df_15m = fetch_data(symbol, '15m')
    if df_4h is None or df_15m is None: return None

    # 4 Saatlik İndikatörler
    df_4h['ema_50'] = ta.ema(df_4h['close'], length=50)
    df_4h['rsi'] = ta.rsi(df_4h['close'], length=14)
    df_4h['vol_ma'] = ta.sma(df_4h['volume'], length=20)
    
    current_price = df_4h['close'].iloc[-1]
    ema_4h = df_4h['ema_50'].iloc[-1]
    rsi_4h = df_4h['rsi'].iloc[-1]
    current_vol = df_4h['volume'].iloc[-1]
    avg_vol = df_4h['vol_ma'].iloc[-1]

    # 15 Dakikalık İndikatörler
    df_15m['rsi'] = ta.rsi(df_15m['close'], length=14)
    rsi_15m = df_15m['rsi'].iloc[-1]

    # --- SKORLAMA ---
    score = 0
    
    # 1. Trend Gücü (EMA Farkı)
    diff_percent = ((current_price - ema_4h) / ema_4h) * 100
    if diff_percent > 3: score += 30
    elif diff_percent > 1: score += 20
    elif diff_percent > 0: score += 10
    elif diff_percent < -3: score -= 30
    elif diff_percent < -1: score -= 20
    else: score -= 10

    # 2. Hacim Gücü
    vol_ratio = current_vol / avg_vol
    if vol_ratio > 2.0:
        if score > 0: score += 20
        else: score -= 20
    elif vol_ratio > 1.2:
        if score > 0: score += 10
        else: score -= 10

    # 3. RSI (4H)
    if rsi_4h < 25: score += 30
    elif rsi_4h < 35: score += 20
    elif rsi_4h < 45: score += 10
    elif rsi_4h > 75: score -= 30
    elif rsi_4h > 65: score -= 20
    elif rsi_4h > 55: score -= 10

    # 4. Kısa Vade (15M)
    if score > 0:
        if rsi_15m < 30: score += 20
        elif rsi_15m < 50: score += 10
        elif rsi_15m > 70: score -= 15
    else:
        if rsi_15m > 70: score -= 20
        elif rsi_15m > 50: score -= 10
        elif rsi_15m < 30: score += 15

    direction = "YÜKSELİŞ (LONG) 🟢" if score > 0 else "DÜŞÜŞ (SHORT) 🔴"
    
    recent_high = df_4h['high'].tail(50).max()
    recent_low = df_4h['low'].tail(50).min()
    
    if score > 0:
        tp = recent_high
        sl = recent_low * 0.99 
    else:
        tp = recent_low
        sl = recent_high * 1.01 

    return {
        "symbol": symbol, "price": current_price, "score": score, 
        "direction": direction, "tp": tp, "sl": sl,
        "rsi_4h": rsi_4h, "rsi_15m": rsi_15m, 
        "vol_ratio": vol_ratio, "diff_percent": diff_percent
    }

# --- AI YORUMU ---
async def get_ai_comment(data, news):
    prompt = (
        f"Kripto analistisin. Verileri yorumla:\n"
        f"Coin: {data['symbol']} | Fiyat: {data['price']:.2f}\n"
        f"Skor: {data['score']}/100\n"
        f"Trend (EMA Farkı): %{data['diff_percent']:.2f}\n"
        f"Hacim Gücü: {data['vol_ratio']:.1f}x\n"
        f"RSI (4H): {data['rsi_4h']:.1f} | RSI (15m): {data['rsi_15m']:.1f}\n"
        f"Haberler: {', '.join(news)}\n"
        f"Yorum (Türkçe): Teknik Durum, Hacim Analizi ve Haber Etkisi başlıklarıyla özetle."
    )
    try:
        response = await asyncio.to_thread(model.generate_content, prompt)
        return response.text
    except: return "AI yorumu alınamadı."

# --- KOMUTLAR ---
async def incele(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text("❌ Örnek: `/incele BTCUSDT`")
    symbol = context.args[0].upper()
    await update.message.reply_text(f"🔍 {symbol} için Trend, Hacim ve AI analizi yapılıyor...")

    data = analyze_market(symbol)
    if not data: return await update.message.reply_text("❌ Veri yok.")

    news = fetch_news(symbol)
    ai_comment = await get_ai_comment(data, news)
    
    abs_score = abs(data['score'])
    strength = "🔥 ÇOK GÜÇLÜ" if abs_score >= 75 else "💪 GÜÇLÜ" if abs_score >= 50 else "⚠️ ZAYIF"

    msg = (
        f"💎 *{symbol} DETAYLI ANALİZ (V3.2)*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 *YÖN:* {data['direction']}\n"
        f"🏆 *Skor:* {data['score']}/100 {strength}\n"
        f"💵 *Fiyat:* {data['price']:.4f}\n\n"
        
        f"🧠 *YAPAY ZEKA YORUMU:*\n{ai_comment}\n\n"
        
        f"🎯 *İŞLEM PLANI:*\n"
        f"✅ *Hedef (TP):* {data['tp']:.4f}\n"
        f"⛔ *Stop (SL):* {data['sl']:.4f}\n"
    )
    await update.message.reply_text(msg, parse_mode='Markdown')

async def auto_scan(context: ContextTypes.DEFAULT_TYPE):
    for coin in WATCHLIST:
        data = analyze_market(coin)
        if data and abs(data['score']) >= 75:
            if context.job.chat_id:
                await context.bot.send_message(chat_id=context.job.chat_id, text=f"🚨 *FIRSAT:* {coin} Skoru {data['score']} oldu!")

async def baslat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_message.chat_id
    context.job_queue.run_repeating(auto_scan, interval=3600, first=10, chat_id=chat_id, name=str(chat_id))
    await update.message.reply_text("✅ Otomatik Avcı Başlatıldı!")

if __name__ == '__main__':
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("incele", incele))
    app.add_handler(CommandHandler("baslat", baslat))
    app.run_polling()
