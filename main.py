import logging
import feedparser
import ccxt
import pandas as pd
import pandas_ta as ta
import google.generativeai as genai
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler
import asyncio

# --- AYARLAR VE ANAHTARLAR ---
# Senin Telegram Token'ın:
TELEGRAM_TOKEN = "8320997161:AAFuNcpONcHLNdnitNehNZ2SOMskiGva6Qs"

# Senin Gemini API Key'in:
GEMINI_API_KEY = "AIzaSyDS7qv7xvp6l_jS8dWU510DHPKT7qYgbFU"

# Otomatik Taranacak Coin Listesi (Virgülle ekleme yapabilirsin)
WATCHLIST = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "AVAXUSDT", "DOGEUSDT", "PEPEUSDT"]

# Gemini Ayarları
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- 1. VERİ ÇEKME (Çoklu Zaman Dilimi) ---
def fetch_data(symbol, timeframe):
    exchange = ccxt.binance()
    try:
        # Son 100 mumu çekiyoruz
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

# --- 3. ANALİZ MOTORU (V3.1 - Hassas Puanlama) ---
def analyze_market(symbol):
    # A. 4 Saatlik Veri (ANA TREND)
    df_4h = fetch_data(symbol, '4h')
    if df_4h is None: return None
    
    # B. 15 Dakikalık Veri (GİRİŞ ZAMANLAMASI)
    df_15m = fetch_data(symbol, '15m')
    if df_15m is None: return None

    # --- İndikatör Hesaplamaları ---
    # 4 Saatlik
    df_4h['ema_50'] = ta.ema(df_4h['close'], length=50)
    df_4h['rsi'] = ta.rsi(df_4h['close'], length=14)
    
    current_price = df_4h['close'].iloc[-1]
    ema_4h = df_4h['ema_50'].iloc[-1]
    rsi_4h = df_4h['rsi'].iloc[-1]

    # 15 Dakikalık
    df_15m['rsi'] = ta.rsi(df_15m['close'], length=14)
    rsi_15m = df_15m['rsi'].iloc[-1]

    # --- SKOR HESAPLAMA (100 Üzerinden Kademeli Sistem) ---
    score = 0
    
    # 1. Trend (4H) - Max 30 Puan
    if current_price > ema_4h: score += 30 # Trend Yukarı
    else: score -= 30 # Trend Aşağı

    # 2. RSI Durumu (4H) - KADEMELİ PUANLAMA
    # Ucuzluk (Long Fırsatı)
    if rsi_4h < 25: score += 40      # Çok Ucuz (Aşırı Satım)
    elif rsi_4h < 35: score += 30    # Ucuz
    elif rsi_4h < 45: score += 10    # Makul Seviye
    
    # Pahalılık (Short Fırsatı)
    elif rsi_4h > 75: score -= 40    # Çok Pahalı (Aşırı Alım)
    elif rsi_4h > 65: score -= 30    # Pahalı
    elif rsi_4h > 55: score -= 10    # Riskli Seviye

    # 3. Kısa Vade Onayı (15M) - HASSAS GİRİŞ
    # Eğer Ana Yön Yukarıysa (Long bakıyorsak)
    if score > 0: 
        if rsi_15m < 30: score += 30      # 15dk'da dip yapmış, MÜKEMMEL GİRİŞ!
        elif rsi_15m < 50: score += 10    # 15dk'da makul.
        elif rsi_15m > 70: score -= 20    # 15dk'da şişmiş, biraz bekle!
    # Eğer Ana Yön Aşağıysa (Short bakıyorsak)
    else: 
        if rsi_15m > 70: score -= 30      # 15dk'da tepe yapmış, MÜKEMMEL SATIŞ!
        elif rsi_15m > 50: score -= 10    # 15dk'da makul.
        elif rsi_15m < 30: score += 20    # 15dk'da dipte, short açma bekle!

    # --- SONUÇLAR VE HEDEFLER ---
    direction = "YÜKSELİŞ (LONG) 🟢" if score > 0 else "DÜŞÜŞ (SHORT) 🔴"
    
    # TP / SL (4H Grafiğe Göre Price Action)
    recent_high = df_4h['high'].tail(50).max()
    recent_low = df_4h['low'].tail(50).min()
    
    # Price Action Mantığı:
    if score > 0: # Long
        tp = recent_high
        sl = recent_low * 0.99 # Desteğin %1 altı
    else: # Short
        tp = recent_low
        sl = recent_high * 1.01 # Direncin %1 üstü

    return {
        "symbol": symbol, "price": current_price, "score": score, 
        "direction": direction, "tp": tp, "sl": sl,
        "rsi_4h": rsi_4h, "rsi_15m": rsi_15m
    }

# --- 4. GEMINI AI YORUMCUSU ---
async def get_ai_comment(data, news):
    prompt = (
        f"Sen bir kripto uzmanısın. Şu verilere göre kısa bir yorum yap (Türkçe):\n"
        f"Coin: {data['symbol']}\n"
        f"Fiyat: {data['price']:.2f}\n"
        f"Teknik Skor: {data['score']} (100 üzerinden. + puanlar Long, - puanlar Short)\n"
        f"Ana Trend (4H): {'Yukarı' if data['score'] > 0 else 'Aşağı'}\n"
        f"RSI (4H): {data['rsi_4h']:.1f} (Genel Güç)\n"
        f"RSI (15m): {data['rsi_15m']:.1f} (Anlık Durum)\n"
        f"Son Haberler: {', '.join(news)}\n\n"
        f"Yorumun şu 3 başlığı içersin: 'Teknik Durum', 'Haberlerin Etkisi' ve 'Yatırımcıya Not'. Asla yatırım tavsiyesi değildir deme, sadece analiz yap."
    )
    try:
        response = await asyncio.to_thread(model.generate_content, prompt)
        return response.text
    except Exception as e:
        return f"AI Yorumu alınamadı. Hata: {e}"

# --- 5. TELEGRAM KOMUTU (/incele) ---
async def incele(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Örnek: `/incele BTCUSDT`")
        return

    symbol = context.args[0].upper()
    await update.message.reply_text(f"🤖 {symbol} için 4H ve 15M grafikler taranıyor, Yapay Zeka (Gemini) düşünüyor...")

    data = analyze_market(symbol)
    if not data:
        await update.message.reply_text("❌ Veri alınamadı. Sembolü kontrol et.")
        return

    news = fetch_news(symbol)
    ai_comment = await get_ai_comment(data, news)

    # Skorun gücüne göre emoji
    abs_score = abs(data['score'])
    if abs_score >= 80: strength = "🔥 (ÇOK GÜÇLÜ)"
    elif abs_score >= 50: strength = "💪 (GÜÇLÜ)"
    else: strength = "⚠️ (ZAYIF/RİSKLİ)"

    msg = (
        f"💎 *{symbol} AI ANALİZ (V3.1 - HASSAS)*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 *STRATEJİ:* {data['direction']}\n"
        f"🏆 *Skor:* {data['score']}/100 {strength}\n"
        f"💵 *Fiyat:* {data['price']:.4f}\n\n"
        
        f"🧠 *GEMINI YORUMU:*\n{ai_comment}\n\n"
        
        f"🎯 *TİCARET PLANI:*\n"
        f"✅ *Hedef (TP):* {data['tp']:.4f}\n"
        f"⛔ *Stop (SL):* {data['sl']:.4f}\n"
    )
    await update.message.reply_text(msg, parse_mode='Markdown')

# --- 6. OTOMATİK AVCI (Scanner Job) ---
async def auto_scan(context: ContextTypes.DEFAULT_TYPE):
    for coin in WATCHLIST:
        data = analyze_market(coin)
        # Sadece ÇOK GÜÇLÜ (Mutlak Skoru 75 ve üzeri) fırsatları bildir
        if data and abs(data['score']) >= 75:
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
    await update.message.reply_text(f"✅ Otomatik Avcı Başlatıldı! \n📋 Liste: {', '.join(WATCHLIST)}\n⏰ Her saat başı 75+ puanlı fırsatları bildireceğim.")

# --- 7. BAŞLATMA ---
if __name__ == '__main__':
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("incele", incele))
    app.add_handler(CommandHandler("baslat", baslat))
    
    print("V3.1 Ultimate Bot Çalışıyor...")
    app.run_polling()
