import logging
import requests
import ccxt
import pandas as pd
import pandas_ta as ta
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler
import os

# --- AYARLAR ---
# Token'larını tırnak içine yaz
TELEGRAM_TOKEN = "BURAYA_TELEGRAM_TOKEN_GELECEK"
CRYPTOPANIC_API_KEY = "BURAYA_CRYPTOPANIC_API_KEY_GELECEK"
SYMBOL_TIMEFRAME = '4h'

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- 1. TEKNİK VERİ ÇEKME ---
def fetch_technical_data(symbol):
    exchange = ccxt.binance()
    try:
        # ATR ve EMA hesaplamak için son 100 mumu çekiyoruz
        bars = exchange.fetch_ohlcv(symbol, timeframe=SYMBOL_TIMEFRAME, limit=100)
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        return df
    except Exception as e:
        return None

# --- 2. HABERLERİ ÇEKME (CryptoPanic) ---
def fetch_news(symbol):
    coin_ticker = symbol.replace("USDT", "")
    url = f"https://cryptopanic.com/api/v1/posts/?auth_token={CRYPTOPANIC_API_KEY}&currencies={coin_ticker}&filter=important&public=true"
    
    try:
        response = requests.get(url).json()
        news_list = []
        if "results" in response:
            for post in response["results"][:3]:
                title = post["title"]
                sentiment = ""
                if "votes" in post:
                    if post["votes"]["bullish"] > post["votes"]["bearish"]:
                        sentiment = "🟢 (Pozitif)"
                    elif post["votes"]["bearish"] > post["votes"]["bullish"]:
                        sentiment = "🔴 (Negatif)"
                news_list.append(f"• {title} {sentiment}")
        return news_list if news_list else ["Yakın zamanda önemli bir haber akışı yok."]
    except Exception as e:
        return ["Haber verisi çekilemedi."]

# --- 3. ANALİZ MOTORU ---
def analyze_market(df):
    current_price = df['close'].iloc[-1]
    df['rsi'] = ta.rsi(df['close'], length=14)
    df['ema_50'] = ta.ema(df['close'], length=50)
    df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
    
    last_rsi = df['rsi'].iloc[-1]
    last_atr = df['atr'].iloc[-1]
    ema_val = df['ema_50'].iloc[-1]

    score = 0
    # RSI Puanı
    if last_rsi < 30: score += 30
    elif last_rsi > 70: score -= 30
    else: score += 10 if last_rsi > 50 else -10

    # Trend Puanı
    if current_price > ema_val: score += 40
    else: score -= 40

    # TP / SL Hesaplama
    support = df['low'].tail(20).min()
    resistance = df['high'].tail(20).max()

    if score > 0:
        sl_price = current_price - (last_atr * 1.5)
        tp_price = current_price + (last_atr * 2.5)
        direction = "YÜKSELİŞ (LONG) 🟢"
    else:
        sl_price = current_price + (last_atr * 1.5)
        tp_price = current_price - (last_atr * 2.5)
        direction = "DÜŞÜŞ (SHORT) 🔴"

    return {
        "price": current_price, "score": score, "direction": direction,
        "tp": tp_price, "sl": sl_price, "support": support, "resistance": resistance
    }

# --- 4. KOMUT ---
async def incele(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Örnek kullanım: `/incele BTCUSDT`")
        return

    symbol = context.args[0].upper()
    await update.message.reply_text(f"🔍 {symbol} analiz ediliyor...")

    df = fetch_technical_data(symbol)
    if df is None:
        await update.message.reply_text("❌ Veri alınamadı. Sembolü kontrol et.")
        return

    data = analyze_market(df)
    news = fetch_news(symbol)

    msg = (
        f"💎 *{symbol} ANALİZ RAPORU*\n"
        f"📊 *YÖN:* {data['direction']}\n"
        f"🌡 *Güven Skoru:* {data['score']}/70\n"
        f"💵 *Fiyat:* {data['price']:.4f}\n\n"
        f"✅ *TP (Hedef):* {data['tp']:.4f}\n"
        f"⛔ *SL (Stop):* {data['sl']:.4f}\n\n"
        f"📰 *ÖNEMLİ HABERLER:*\n"
    )
    for n in news: msg += f"{n}\n"
    
    await update.message.reply_text(msg, parse_mode='Markdown')

if __name__ == '__main__':
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("incele", incele))
    app.run_polling()

