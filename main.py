import logging
import feedparser
import ccxt
import pandas as pd
import pandas_ta as ta
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler

# --- AYARLAR ---
TELEGRAM_TOKEN = "8320997161:AAFuNcpONcHLNdnitNehNZ2SOMskiGva6Qs"
SYMBOL_TIMEFRAME = '4h'

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- 1. TEKNİK VERİ ÇEKME ---
def fetch_technical_data(symbol):
    exchange = ccxt.binance()
    try:
        # Daha sağlıklı destek/direnç için son 200 mumu çekiyoruz
        bars = exchange.fetch_ohlcv(symbol, timeframe=SYMBOL_TIMEFRAME, limit=200)
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        return df
    except Exception as e:
        return None

# --- 2. HABERLERİ ÇEKME (RSS) ---
def fetch_news(symbol):
    coin_ticker = symbol.replace("USDT", "").upper()
    rss_url = f"https://cryptopanic.com/news/rss/currency/{coin_ticker}/"
    try:
        feed = feedparser.parse(rss_url)
        news_list = [f"• {entry.title}" for entry in feed.entries[:3]]
        return news_list if news_list else ["Yakın zamanda önemli bir haber akışı yok."]
    except:
        return ["Haber kaynağına ulaşılamadı."]

# --- 3. PROFESYONEL ANALİZ MOTORU ---
def analyze_market(df):
    current_price = df['close'].iloc[-1]
    
    # --- İndikatörleri Hesapla ---
    # 1. RSI
    df['rsi'] = ta.rsi(df['close'], length=14)
    # 2. EMA (Trend)
    df['ema_50'] = ta.ema(df['close'], length=50)
    # 3. MACD (Momentum - Yeni Ekledik)
    macd = ta.macd(df['close'])
    df['macd'] = macd['MACD_12_26_9']
    df['macd_signal'] = macd['MACDs_12_26_9']

    # Son Değerler
    last_rsi = df['rsi'].iloc[-1]
    ema_val = df['ema_50'].iloc[-1]
    last_macd = df['macd'].iloc[-1]
    last_signal = df['macd_signal'].iloc[-1]

    # --- PUANLAMA (100 Üzerinden) ---
    score = 0
    
    # A. Trend Puanı (Max 40)
    if current_price > ema_val: score += 40
    else: score -= 40

    # B. RSI Puanı (Max 30)
    if last_rsi < 30: score += 30      # Ucuz
    elif last_rsi > 70: score -= 30    # Pahalı
    else: score += 10 if last_rsi > 50 else -10 # Nötr bölge

    # C. MACD Puanı (Max 30)
    if last_macd > last_signal: score += 30 # Al Sinyali
    else: score -= 30 # Sat Sinyali

    # --- MARKET YAPISINA GÖRE TP / SL (Price Action) ---
    # Son 50 mumun en yükseği (Major Direnç) ve en düşüğü (Major Destek)
    recent_high = df['high'].tail(50).max()
    recent_low = df['low'].tail(50).min()

    # Yön ve Seviyeler
    if score > 0:
        direction = "YÜKSELİŞ (LONG) 🟢"
        # Long için Hedef: Tepe noktası (Direnç)
        # Long için Stop: Dip noktasının biraz altı
        tp_price = recent_high
        sl_price = recent_low * 0.99 # %1 altına pay bırakıyoruz (Fake atmasın diye)
    else:
        direction = "DÜŞÜŞ (SHORT) 🔴"
        # Short için Hedef: Dip noktası (Destek)
        # Short için Stop: Tepe noktasının biraz üstü
        tp_price = recent_low
        sl_price = recent_high * 1.01 # %1 üstüne pay bırakıyoruz

    return {
        "price": current_price, "score": score, "direction": direction,
        "tp": tp_price, "sl": sl_price, 
        "support": recent_low, "resistance": recent_high
    }

# --- 4. TELEGRAM KOMUTU ---
async def incele(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Örnek: `/incele BTCUSDT`")
        return

    symbol = context.args[0].upper()
    await update.message.reply_text(f"⏳ {symbol} piyasa yapısı inceleniyor...")

    df = fetch_technical_data(symbol)
    if df is None:
        await update.message.reply_text("❌ Grafik verisi alınamadı.")
        return

    data = analyze_market(df)
    news = fetch_news(symbol)

    # Güven derecesi yorumu
    guven_yorum = ""
    abs_score = abs(data['score'])
    if abs_score >= 80: guven_yorum = "🔥 (Çok Güçlü)"
    elif abs_score >= 50: guven_yorum = "💪 (Güçlü)"
    else: guven_yorum = "⚠️ (Zayıf/Riskli)"

    msg = (
        f"💎 *{symbol} PROFESYONEL ANALİZ*\n"
        f"📊 *STRATEJİ:* {data['direction']}\n"
        f"🏆 *Güven Skoru:* {data['score']}/100 {guven_yorum}\n"
        f"💵 *Fiyat:* {data['price']:.4f}\n\n"
        
        f"🎯 *TİCARET KURULUMU (Price Action):*\n"
        f"✅ *Hedef (TP):* {data['tp']:.4f} (Direnç Bölgesi)\n"
        f"⛔ *Stop (SL):* {data['sl']:.4f} (Destek Altı)\n\n"
        
        f"🧱 *Market Yapısı:*\n"
        f"• Destek: {data['support']:.4f}\n"
        f"• Direnç: {data['resistance']:.4f}\n\n"
        
        f"📰 *PİYASA HABERLERİ:*\n"
    )
    for n in news: msg += f"{n}\n"
    
    await update.message.reply_text(msg, parse_mode='Markdown')

if __name__ == '__main__':
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("incele", incele))
    app.run_polling()
