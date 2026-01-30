import logging
import feedparser
import ccxt
import pandas as pd
import pandas_ta as ta
import google.generativeai as genai
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, JobQueue
import asyncio

# --- AYARLAR VE ANAHTARLAR ---
# Senin verdiğin Telegram Token:
TELEGRAM_TOKEN = "8320997161:AAFuNcpONcHLNdnitNehNZ2SOMskiGva6Qs"

# Senin verdiğin Gemini API Key:
GEMINI_API_KEY = "AIzaSyDS7qv7xvp6l_jS8dWU510DHPKT7qYgbFU"

# Otomatik Taranacak Coin Listesi (İstediğini ekleyip çıkarabilirsin)
WATCHLIST = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "AVAXUSDT", "DOGEUSDT"]

# Gemini Ayarları
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash') # Daha hızlı ve ücretsiz kota dostu model

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- 1. VERİ ÇEKME (Çoklu Zaman Dilimi) ---
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

# --- 3. ANALİZ MOTORU (Multi-Timeframe) ---
def analyze_market(symbol):
    # A. 4 Saatlik Veri (ANA TREND)
    df_4h = fetch_data(symbol, '4h')
    if df_4h is None: return None
    
    # B. 15 Dakikalık Veri (GİRİŞ ZAMANLAMASI)
    df_15m = fetch_data(symbol, '15m')
    if df_15m is None: return None

    # --- 4H İndikatörler (Trend Yönü İçin) ---
    df_4h['ema_50'] = ta.ema(df_4h['close'], length=50)
    df_4h['rsi'] = ta.rsi(df_4h['close'], length=14)
    current_price = df_4h['close'].iloc[-1]
    ema_4h = df_4h['ema_50'].iloc[-1]
    rsi_4h = df_4h['rsi'].iloc[-1]

    # --- 15M İndikatörler (Hassas Giriş İçin) ---
    df_15m['rsi'] = ta.rsi(df_15m['close'], length=14)
    rsi_15m = df_15m['rsi'].iloc[-1]

    # --- SKOR HESAPLAMA (100 Puan) ---
    score = 0
    
    # 1. Trend (4H) - 40 Puan
    if current_price > ema_4h: score += 40 # Ana yön yukarı
    else: score -= 40 # Ana yön aşağı

    # 2. RSI Durumu (4H) - 30 Puan
    if rsi_4h < 30: score += 30
    elif rsi_4h > 70: score -= 30
    
    # 3. Kısa Vade Onayı (15M) - 30 Puan
    # Eğer 4H Long ise, 15M'de RSI şişmemiş olmalı
    if score > 0: # Long bakıyoruz
        if rsi_15m < 70: score += 30 # Giriş uygun
        else: score -= 10 # Bekle, kısa vadede şişmiş
    else: # Short bakıyoruz
        if rsi_15m > 30: score -= 30 # Giriş uygun
        else: score += 10 # Bekle, kısa vadede dipte

    # --- SONUÇLAR ---
    direction = "YÜKSELİŞ (LONG) 🟢" if score > 0 else "DÜŞÜŞ (SHORT) 🔴"
    
    # TP / SL (4H Grafiğe Göre)
    recent_high = df_4h['high'].tail(50).max()
    recent_low = df_4h['low'].tail(50).min()
    
    tp = recent_high if score > 0 else recent_low
    sl = recent_low * 0.99 if score > 0 else recent_high * 1.01

    return {
        "symbol": symbol, "price": current_price, "score": score, 
        "direction": direction, "tp": tp, "sl": sl,
        "rsi_4h": rsi_4h, "rsi_15m": rsi_15m
    }

# --- 4. GEMINI AI YORUMCUSU ---
async def get_ai_comment(data, news):
    prompt = (
        f"Sen profesyonel bir kripto analistisin. Şu verilere göre çok kısa ve net bir yorum yap (Türkçe):\n"
        f"Coin: {data['symbol']}\n"
        f"Fiyat: {data['price']}\n"
        f"Teknik Skor: {data['score']} (100 üzerinden. Pozitifler Long, Negatifler Short)\n"
        f"Ana Trend (4H): {'Yukarı' if data['score'] > 0 else 'Aşağı'}\n"
        f"RSI (4H): {data['rsi_4h']:.1f}\n"
        f"RSI (15m): {data['rsi_15m']:.1f}\n"
        f"Son Haberler: {', '.join(news)}\n\n"
        f"Yorumun şu başlıkları içersin: 'Teknik Görünüm', 'Haber Etkisi' ve 'Son Tavsiye'. Asla yatırım tavsiyesi değildir deme."
    )
    try:
        response = await asyncio.to_thread(model.generate_content, prompt)
        return response.text
    except Exception as e:
        return f"AI Yorumu alınamadı. (Hata: {e})"

# --- 5. TELEGRAM KOMUTU (/incele) ---
async def incele(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Örnek: `/incele BTCUSDT`")
        return

    symbol = context.args[0].upper()
    await update.message.reply_text(f"🤖 {symbol} için Yapay Zeka (Gemini) analiz yapıyor, lütfen bekle...")

    data = analyze_market(symbol)
    if not data:
        await update.message.reply_text("❌ Veri alınamadı. Sembolü kontrol et.")
        return

    news = fetch_news(symbol)
    ai_comment = await get_ai_comment(data, news)

    msg = (
        f"💎 *{symbol} AI ANALİZ (V3.0)*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 *STRATEJİ:* {data['direction']}\n"
        f"🏆 *Güven Skoru:* {data['score']}/100\n"
        f"💵 *Fiyat:* {data['price']:.4f}\n\n"
        
        f"🧠 *GEMINI AI YORUMU:*\n{ai_comment}\n\n"
        
        f"🎯 *TİCARET PLANI:*\n"
        f"✅ *TP:* {data['tp']:.4f}\n"
        f"⛔ *SL:* {data['sl']:.4f}\n"
    )
    await update.message.reply_text(msg, parse_mode='Markdown')

# --- 6. OTOMATİK AVCI (Scanner Job) ---
async def auto_scan(context: ContextTypes.DEFAULT_TYPE):
    for coin in WATCHLIST:
        data = analyze_market(coin)
        # Sadece ÇOK GÜÇLÜ (Skor 80+) fırsatları bildir
        if data and abs(data['score']) >= 80:
            if context.job.chat_id:
                await context.bot.send_message(
                    chat_id=context.job.chat_id,
                    text=f"🚨 *FIRSAT ALARMI!* \n\n{coin} Skoru: {data['score']} oldu!\nDetay için: `/incele {coin}`"
                )

# Scanner'ı başlatmak için komut
async def baslat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_message.chat_id
    current_jobs = context.job_queue.get_jobs_by_name(str(chat_id))
    for job in current_jobs: job.schedule_removal()
    
    # 3600 saniye = 1 Saat
    context.job_queue.run_repeating(auto_scan, interval=3600, first=10, chat_id=chat_id, name=str(chat_id))
    await update.message.reply_text(f"✅ Otomatik Avcı Başlatıldı! İzlenenler: {', '.join(WATCHLIST)}\nHer saat başı güçlü sinyalleri tarayacağım.")

# --- 7. BAŞLATMA ---
if __name__ == '__main__':
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("incele", incele))
    app.add_handler(CommandHandler("baslat", baslat))
    
    print("V3.0 Ultimate Bot Çalışıyor...")
    app.run_polling()
