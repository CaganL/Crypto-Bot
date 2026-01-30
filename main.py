import logging
import feedparser
import ccxt
import pandas as pd
import pandas_ta as ta
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler

# --- AYARLAR ---
# Senin Token'ın buraya ekli:
TELEGRAM_TOKEN = "8320997161:AAFuNcpONcHLNdnitNehNZ2SOMskiGva6Qs"
SYMBOL_TIMEFRAME = '4h'  # 4 Saatlik grafik analizi

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- 1. TEKNİK VERİ ÇEKME (Binance) ---
def fetch_technical_data(symbol):
    exchange = ccxt.binance()
    try:
        # Destek/Direnç tespiti için son 200 mumu çekiyoruz
        bars = exchange.fetch_ohlcv(symbol, timeframe=SYMBOL_TIMEFRAME, limit=200)
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        return df
    except Exception as e:
        return None

# --- 2. HABERLERİ ÇEKME (RSS - Ücretsiz) ---
def fetch_news(symbol):
    # Symbol "BTCUSDT" ise sadece "BTC" kısmını alıyoruz
    coin_ticker = symbol.replace("USDT", "").upper()
    rss_url = f"https://cryptopanic.com/news/rss/currency/{coin_ticker}/"
    
    try:
        feed = feedparser.parse(rss_url)
        news_list = []
        if feed.entries:
            for entry in feed.entries[:3]: # Son 3 haber
                news_list.append(f"• {entry.title}")
        return news_list if news_list else ["Yakın zamanda önemli bir haber akışı yok."]
    except:
        return ["Haber kaynağına ulaşılamadı."]

# --- 3. PROFESYONEL ANALİZ MOTORU (Hacim + Price Action) ---
def analyze_market(df):
    current_price = df['close'].iloc[-1]
    
    # --- İndikatörleri Hesapla ---
    df['rsi'] = ta.rsi(df['close'], length=14)
    df['ema_50'] = ta.ema(df['close'], length=50)
    
    # MACD
    macd = ta.macd(df['close'])
    df['macd'] = macd['MACD_12_26_9']
    df['macd_signal'] = macd['MACDs_12_26_9']
    
    # HACİM ORTALAMASI (Son 20 mumun ortalaması)
    df['vol_ma'] = ta.sma(df['volume'], length=20)

    # Son Değerler
    last_rsi = df['rsi'].iloc[-1]
    ema_val = df['ema_50'].iloc[-1]
    last_macd = df['macd'].iloc[-1]
    last_signal = df['macd_signal'].iloc[-1]
    current_vol = df['volume'].iloc[-1]
    avg_vol = df['vol_ma'].iloc[-1]
    price_change = df['close'].iloc[-1] - df['open'].iloc[-1]

    # --- PUANLAMA SİSTEMİ (100 Üzerinden) ---
    score = 0
    
    # A. Trend Puanı (EMA) - Max 30 Puan
    if current_price > ema_val: score += 30
    else: score -= 30

    # B. Hacim Puanı (Volume) - Max 30 Puan
    # Hacim ortalamadan yüksekse ve fiyat yönünü destekliyorsa puan ver
    if current_vol > avg_vol:
        if price_change > 0: score += 30 # Yükselişi hacim destekliyor (Güçlü Al)
        else: score -= 30 # Düşüşü hacim destekliyor (Güçlü Sat)
    else:
        pass # Hacim zayıfsa puan eklemiyoruz (Fake hareket riski)

    # C. MACD Puanı - Max 20 Puan
    if last_macd > last_signal: score += 20
    else: score -= 20

    # D. RSI Puanı - Max 20 Puan
    if last_rsi < 30: score += 20      # Ucuz (Alım Fırsatı)
    elif last_rsi > 70: score -= 20    # Pahalı (Satış Riski)
    # Ara değerlerde puan nötr kalır

    # --- MARKET YAPISINA GÖRE TP / SL (Price Action) ---
    # Son 50 mumun en yükseği (Major Direnç) ve en düşüğü (Major Destek)
    recent_high = df['high'].tail(50).max()
    recent_low = df['low'].tail(50).min()

    if score > 0:
        direction = "YÜKSELİŞ (LONG) 🟢"
        # Hedef: Direnç | Stop: Desteğin %1 altı
        tp_price = recent_high
        sl_price = recent_low * 0.99 
    else:
        direction = "DÜŞÜŞ (SHORT) 🔴"
        # Hedef: Destek | Stop: Direncin %1 üstü
        tp_price = recent_low
        sl_price = recent_high * 1.01 

    return {
        "price": current_price, "score": score, "direction": direction,
        "tp": tp_price, "sl": sl_price, 
        "support": recent_low, "resistance": recent_high
    }

# --- 4. TELEGRAM KOMUTU ---
async def incele(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Kullanım: `/incele BTCUSDT`")
        return

    symbol = context.args[0].upper()
    await update.message.reply_text(f"🔍 {symbol} için Hacim, Trend ve Haberler inceleniyor...")

    df = fetch_technical_data(symbol)
    if df is None:
        await update.message.reply_text("❌ Grafik verisi alınamadı. Sembolü kontrol et (örn: BTCUSDT).")
        return

    data = analyze_market(df)
    news = fetch_news(symbol)

    # Güven derecesini yorumla
    abs_score = abs(data['score'])
    if abs_score >= 80: guven_yorum = "🔥 (Çok Güçlü Sinyal)"
    elif abs_score >= 50: guven_yorum = "💪 (Güçlü Sinyal)"
    else: guven_yorum = "⚠️ (Zayıf/Riskli Sinyal)"

    msg = (
        f"💎 *{symbol} PROFESYONEL ANALİZ*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 *STRATEJİ:* {data['direction']}\n"
        f"🏆 *Güven Skoru:* {data['score']}/100\n"
        f"ℹ️ *Durum:* {guven_yorum}\n"
        f"💵 *Anlık Fiyat:* {data['price']:.4f}\n\n"
        
        f"🎯 *TİCARET KURULUMU (Price Action):*\n"
        f"✅ *Kar Al (TP):* {data['tp']:.4f} (Direnç Bölgesi)\n"
        f"⛔ *Zarar Durdur (SL):* {data['sl']:.4f} (Destek Altı)\n\n"
        
        f"🧱 *Market Yapısı:*\n"
        f"• Ana Destek: {data['support']:.4f}\n"
        f"• Ana Direnç: {data['resistance']:.4f}\n\n"
        
        f"📰 *SON DAKİKA HABERLERİ:*\n"
    )
    for n in news: msg += f"{n}\n"
    
    msg += "\n⚠️ _Yatırım tavsiyesi değildir. Robot analizidir._"

    await update.message.reply_text(msg, parse_mode='Markdown')

if __name__ == '__main__':
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("incele", incele))
    print("Bot Başlatıldı! Telegram'dan yazabilirsin.")
    app.run_polling()
