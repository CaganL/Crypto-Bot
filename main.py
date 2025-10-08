# -------------------------------
# Gerekli Kütüphaneler ve Ayarlar
# -------------------------------
import os
import requests
import pandas as pd
import schedule
import time
import json
import logging 
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD
from ta.volatility import BollingerBands
from telegram import Bot
from googletrans import Translator
from datetime import datetime
import csv # <-- YENİ: CSV kaydı için

# -------------------------------
# Logging Kurulumu
# -------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log"), 
        logging.StreamHandler()        
    ]
)
logger = logging.getLogger(__name__)

# -------------------------------
# API Anahtarları ve Telegram
# -------------------------------
BOT_TOKEN = "8320997161:AAFuNcpONcHLNdnitNehNZ2SOMskiGva6Qs"
CHAT_ID = 7294398674
TAAPI_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJjbHVlIjoiNjhlM2RkOTk4MDZmZjE2NTFlOGY3NjlkIiwiaWF0IjoxNzU5ODYyNDEyLCJleHAiOjMzMjY0MzI2NDEyfQ.dmvJC5-LNScEkhWdziBA21Ig8hc2oGsaNNohyfrIaD4"
COINGLASS_API_KEY = "36176ba717504abc9235e612d1daeb0c"
NEWSAPI_KEY = "" 

bot = Bot(BOT_TOKEN)
translator = Translator()

# -------------------------------
# Coin Alias ve Global Takipçiler
# -------------------------------
coin_aliases = {
    "BTCUSDT": ["BTC", "Bitcoin", "BTCUSDT"],
    "ETHUSDT": ["ETH", "Ethereum", "ETHUSDT"],
    "SOLUSDT": ["SOL", "Solana", "SOLUSDT"],
    "SUIUSDT": ["SUI", "Sui", "SUIUSDT"],
    "AVAXUSDT": ["AVAX", "Avalanche", "AVAXUSDT"]
}

last_strong_alert = {} 
last_prices = {} 

# -------------------------------
# Telegram Mesaj Fonksiyonu
# -------------------------------
def send_telegram_message(message):
    try:
        max_length = 4000 
        if len(message) > max_length:
            for i in range(0, len(message), max_length):
                bot.send_message(chat_id=CHAT_ID, text=message[i:i+max_length])
        else:
            bot.send_message(chat_id=CHAT_ID, text=message)
        logger.info("Telegram mesajı başarıyla gönderildi.")
    except Exception as e:
        logger.error(f"Telegram gönderim hatası: {e}")

# -------------------------------
# Binance Fiyat Verisi
# -------------------------------
def fetch_binance_klines(symbol="BTCUSDT", interval="4h", limit=100):
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
        data = requests.get(url, timeout=10).json()
        if not data or isinstance(data, dict) and 'code' in data:
            logger.error(f"Binance API hatası ({symbol}, {interval}): {data}")
            return pd.DataFrame()
        df = pd.DataFrame(data, columns=['open_time','open','high','low','close','volume','close_time','quote_asset_volume','trades','taker_base','taker_quote','ignore'])
        df['close'] = df['close'].astype(float)
        return df
    except Exception as e:
        logger.error(f"Binance klines çekme hatası ({symbol}, {interval}): {e}")
        return pd.DataFrame()

# -------------------------------
# Teknik Analiz
# -------------------------------
def calculate_technical_indicators(df):
    if df.empty: return {}
    result = {}
    # RSI
    rsi = RSIIndicator(close=df['close'], window=14)
    result['rsi'] = rsi.rsi().iloc[-1] if not rsi.rsi().empty else None
    # EMA
    ema_short = EMAIndicator(close=df['close'], window=12)
    ema_long = EMAIndicator(close=df['close'], window=26)
    result['ema_short'] = ema_short.ema_indicator().iloc[-1] if not ema_short.ema_indicator().empty else None
    result['ema_long'] = ema_long.ema_indicator().iloc[-1] if not ema_long.ema_indicator().empty else None
    # MACD
    macd_indicator = MACD(close=df['close'], window_slow=26, window_fast=12, window_sign=9)
    result['macd_diff'] = macd_indicator.macd_diff().iloc[-1] if not macd_indicator.macd_diff().empty else None
    
    # Fiyat ve Değişim
    result['last_close'] = df['close'].iloc[-1] if not df.empty else None
    if len(df) >= 2:
        last_close = df['close'].iloc[-1]
        prev_close = df['close'].iloc[-2]
        result['price_change'] = ((last_close - prev_close) / prev_close) * 100
    else:
        result['price_change'] = 0
    
    return result

# -------------------------------
# Ana Veri Çekme ve Analiz (Çoklu Zaman Dilimli)
# -------------------------------
def fetch_multi_timeframe_analysis(symbol):
    analysis = {}
    intervals = {"15m": 100, "1h": 100, "4h": 100, "1d": 100} 
    for interval, limit in intervals.items():
        df = fetch_binance_klines(symbol=symbol, interval=interval, limit=limit)
        indicators = calculate_technical_indicators(df)
        analysis[interval] = indicators
    return analysis

# -------------------------------
# CoinGlass API / Binance fallback
# -------------------------------
def fetch_coinglass_data(symbol="BTC", retries=3):
    if not COINGLASS_API_KEY: return fetch_binance_openinterest(symbol)
    for attempt in range(retries):
        try:
            url = f"https://open-api.coinglass.com/api/pro/v1/futures/openInterest?symbol={symbol}"
            headers = {"coinglassSecret": COINGLASS_API_KEY}
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code != 200:
                logger.warning(f"CoinGlass API HTTP {r.status_code}: {r.text[:100]}"); time.sleep(2); continue
            if not r.text.strip():
                logger.warning(f"CoinGlass API boş yanıt döndü (deneme {attempt+1})"); time.sleep(2); continue
            data = r.json()
            if not data.get("data"):
                logger.warning(f"CoinGlass API veri boş (deneme {attempt+1})"); time.sleep(2); continue
            long_ratio = data.get("data", {}).get("longRate")
            short_ratio = data.get("data", {}).get("shortRate")
            return {"long_ratio": long_ratio, "short_ratio": short_ratio}
        except Exception as e:
            logger.error(f"CoinGlass API hata ({attempt+1}/{retries}): {e}"); time.sleep(2)
    return fetch_binance_openinterest(symbol)

# -------------------------------
# Binance Fallback: Hassas OpenInterest + FundingRate
# -------------------------------
def fetch_binance_openinterest(symbol="BTC"):
    try:
        url_oi = f"https://fapi.binance.com/futures/data/openInterestHist?symbol={symbol}USDT&period=4h&limit=1"
        r_oi = requests.get(url_oi, timeout=10); r_oi.raise_for_status()
        data_oi = r_oi.json(); oi_total = float(data_oi[-1]['sumOpenInterest']) if data_oi else 0
        url_funding = f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol}USDT&limit=1"
        r_f = requests.get(url_funding, timeout=10); r_f.raise_for_status()
        data_f = r_f.json(); funding_rate = float(data_f[0]['fundingRate']) if data_f else 0
        long_ratio = 0.5 + (funding_rate * 10) + (0.05 * (oi_total / max(oi_total, 1e6)))
        long_ratio = max(0, min(long_ratio, 1)); short_ratio = 1 - long_ratio
        return {"long_ratio": long_ratio, "short_ratio": short_ratio, "funding_rate": funding_rate}
    except Exception as e:
        logger.error(f"Binance OpenInterest/FundingRate hata: {e}")
        return {"long_ratio": None, "short_ratio": None, "funding_rate": None}

# -------------------------------
# NewsAPI Haberleri (Pasif)
# -------------------------------
def fetch_news():
    if not NEWSAPI_KEY: return []
    try:
        url = f"https://newsapi.org/v2/top-headlines?category=business&language=en&apiKey={NEWSAPI_KEY}"
        r = requests.get(url, timeout=10); r.raise_for_status()
        articles = r.json().get("articles", [])
        return [f"{a['title']} - {a['url']}" for a in articles[:5]]
    except Exception as e:
        logger.warning(f"NewsAPI hata: {e}")
        return []

# -------------------------------
# AI Öğrenme Sistemi
# -------------------------------
history_file = "history.json"
def load_history():
    if os.path.exists(history_file):
        with open(history_file, "r") as f:
            try: return json.load(f)
            except json.JSONDecodeError:
                logger.warning(f"{history_file} dosyası bozuk, yeniden oluşturuluyor."); return {}
    return {}

def save_history(history):
    with open(history_file, "w") as f: json.dump(history, f)

def ai_position_prediction(symbol, multi_indicators, cg_data=None):
    history = load_history(); score = 0
    
    # 0. 15 Dakikalık (15m) Erken Sinyal (+/- 0.5)
    m15_ind = multi_indicators.get("15m", {})
    if m15_ind.get('macd_diff') is not None:
        if m15_ind['macd_diff'] > 0: score += 0.5 
        elif m15_ind['macd_diff'] < 0: score -= 0.5 

    # 1. Günlük (1d) Trend (+/- 2.5)
    d1_ind = multi_indicators.get("1d", {})
    if d1_ind.get('ema_short') > d1_ind.get('ema_long', 0): score += 2.5 
    elif d1_ind.get('ema_short') < d1_ind.get('ema_long', 0): score -= 2.5 
        
    # 2. 4 Saatlik (4h) Momentum (+/- 1.5)
    h4_ind = multi_indicators.get("4h", {})
    if h4_ind.get('rsi') is not None:
        if h4_ind['rsi'] < 30: score += 1.5 
        elif h4_ind['rsi'] > 70: score -= 1.5 

    # 3. 1 Saatlik (1h) Momentum Değişimi (+/- 1.0)
    h1_ind = multi_indicators.get("1h", {})
    if h1_ind.get('macd_diff') is not None:
        if h1_ind['macd_diff'] > 0: score += 1.0 
        elif h1_ind['macd_diff'] < 0: score -= 1.0 

    # 4. Long/Short Oranı (+/- 1.5)
    if cg_data and cg_data["long_ratio"] and cg_data["short_ratio"]:
        if cg_data["long_ratio"] > 0.65: score -= 1.5 
        elif cg_data["short_ratio"] > 0.65: score += 1.5 
            
    # 5. Pozisyon Sürekliliği (+/- 0.5)
    last_pos = history.get(symbol, {}).get("last_position")
    if last_pos == "Long" and score > 0: score += 0.5
    elif last_pos == "Short" and score < 0: score -= 0.5

    # Sonuçlandırma
    if score >= 3.0: position = "Long (Güçlü)"
    elif score >= 1.0: position = "Long"
    elif score <= -3.0: position = "Short (Güçlü)"
    elif score <= -1.0: position = "Short"
    else: position = "Neutral"

    confidence = min(abs(score / 5) * 100, 100) 
    history[symbol] = {"last_position": position.split()[0]} 
    save_history(history)
    return position, confidence, score

# -------------------------------
# Ani Fiyat Dalgalanma Uyarısı (%5)
# -------------------------------
def check_price_spike(symbol, current_price):
    global last_prices
    if current_price is None: return 
    if symbol in last_prices:
        old_price = last_prices[symbol]
        change_pct = ((current_price - old_price) / old_price) * 100
        if abs(change_pct) >= 5:
            direction = "📈 YÜKSELDİ" if change_pct > 0 else "📉 DÜŞTÜ"
            msg = f"⚠️ **{symbol} Fiyat Uyarısı (ŞOK!):**\nFiyat son kontrolünden beri %{change_pct:.2f} {direction}!\nAnlık Fiyat: {current_price:.2f} USDT"
            send_telegram_message(msg)
    last_prices[symbol] = current_price

# -------------------------------
# ANLIK SİNYAL KONTROLÜ
# -------------------------------
def check_immediate_alert():
    global last_strong_alert
    
    for coin in coin_aliases.keys():
        coin_short = coin.replace("USDT", "")
        
        multi_indicators = fetch_multi_timeframe_analysis(coin)
        cg_data = fetch_coinglass_data(coin_short)
        position, confidence, raw_score = ai_position_prediction(coin, multi_indicators, cg_data)
        
        current_price = multi_indicators.get("4h", {}).get('last_close')
        if current_price is None: continue

        is_strong_long = raw_score >= 3.0
        is_strong_short = raw_score <= -3.0
        
        current_strong_pos = None
        if is_strong_long: current_strong_pos = "Long"
        elif is_strong_short: current_strong_pos = "Short"
            
        last_sent_pos = last_strong_alert.get(coin, "Neutral")
        
        if current_strong_pos and current_strong_pos != last_sent_pos:
            direction = "BÜYÜK ALIM SİNYALİ GELDİ!" if current_strong_pos == "Long" else "BÜYÜK SATIM SİNYALİ GELDİ!"
            
            msg = (f"🚨🚨 **ANLIK GÜÇLÜ SİNYAL UYARISI!** 🚨🚨\n\n"
                   f"**COIN:** {coin}\n"
                   f"**SİNYAL:** {current_strong_pos} ({direction})\n"
                   f"**GÜVEN SKORU:** {confidence:.0f}%\n"
                   f"**ANLIK FİYAT:** {current_price:.2f} USDT\n\n"
                   f"*(Not: Bu sinyal, AI skorunun $\ge 3.0$ veya $\le -3.0$ olduğu için hemen gönderilmiştir.)*")
            
            send_telegram_message(msg)
            
            last_strong_alert[coin] = current_strong_pos
            logger.info(f"Anlık güçlü sinyal gönderildi: {coin} -> {current_strong_pos}")
        
        elif current_strong_pos is None and last_sent_pos != "Neutral":
            last_strong_alert[coin] = "Neutral"
            logger.info(f"{coin} güçlü sinyal durumu nötrlendi.")
            
# -------------------------------
# YENİ FONKSİYON: ML Verisi Kayıt
# -------------------------------
ML_DATA_FILE = "ml_data.csv"
ML_HEADERS = [
    'timestamp', 'symbol', 'price', 
    '1d_rsi', '1d_macd', '1d_ema_diff', 
    '4h_rsi', '4h_macd', '4h_ema_diff', 
    '1h_rsi', '1h_macd', '1h_ema_diff', 
    '15m_rsi', '15m_macd', '15m_ema_diff', 
    'long_ratio', 'short_ratio', 
    'raw_score' 
]

def save_ml_data(coin, multi_indicators, cg_data, raw_score):
    """
    ML eğitimi için gerekli tüm indikatör değerlerini CSV dosyasına kaydeder.
    """
    try:
        current_time = datetime.now().isoformat()
        
        # ML Veri Satırını Hazırla
        data_row = [current_time, coin, multi_indicators.get("4h", {}).get('last_close')]
        
        # Çoklu Zaman Dilimi İndikatörlerini Ekle
        for interval in ["1d", "4h", "1h", "15m"]:
            ind = multi_indicators.get(interval, {})
            # EMA_DIFF: (ema_short - ema_long) trendin gücünü gösterir
            ema_diff = ind.get('ema_short', 0) - ind.get('ema_long', 0)
            data_row.extend([
                ind.get('rsi'),
                ind.get('macd_diff'),
                ema_diff
            ])

        # Long/Short Oranlarını Ekle
        data_row.extend([
            cg_data.get('long_ratio'), 
            cg_data.get('short_ratio')
        ])
        
        # Sonuç Skoru Ekle
        data_row.append(raw_score)

        # CSV'ye Yazma
        file_exists = os.path.isfile(ML_DATA_FILE)
        with open(ML_DATA_FILE, 'a', newline='') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(ML_HEADERS)
            writer.writerow(data_row)
            
        logger.info(f"{coin} için ML verisi başarıyla kaydedildi: {ML_DATA_FILE}")

    except Exception as e:
        logger.error(f"ML verisi kaydetme hatası ({coin}): {e}")


# -------------------------------
# Ana Analiz Fonksiyonu (Periyodik Rapor)
# -------------------------------
def analyze_and_alert():
    logger.info("Analiz döngüsü başlatılıyor.")
    alerts = []
    
    for coin in coin_aliases.keys():
        coin_short = coin.replace("USDT", "")
        
        multi_indicators = fetch_multi_timeframe_analysis(coin)
        h4_indicators = multi_indicators.get("4h", {})
        current_price = h4_indicators.get('last_close')
        price_change_4h = h4_indicators.get('price_change', 0)
        rsi_4h = h4_indicators.get('rsi')

        if current_price is None: continue
        
        check_price_spike(coin, current_price)
        cg_data = fetch_coinglass_data(coin_short)
        position, confidence, raw_score = ai_position_prediction(coin, multi_indicators, cg_data)

        # -----------------------------
        # ML VERİ KAYIT ADIMI (YENİ)
        # -----------------------------
        save_ml_data(coin, multi_indicators, cg_data, raw_score)

        # Rapor Mesajı (Telegram'a gönderilecek)
        msg = f"--- 🤖 **{coin} Çoklu Zaman Dilimi Raporu** ---\n"
        msg += f"💰 **Fiyat:** {current_price:.2f} USDT ({price_change_4h:+.2f}% son 4 saatte)\n"
        msg += f"🔥 **AI Tahmini:** **{position}**\n"
        msg += f"📊 **Güven Skoru:** **{confidence:.0f}%** (Skor: {raw_score:+.1f})\n"
        msg += "\n--- DETAYLI ANALİZ ---\n"
        
        d1_ind = multi_indicators.get("1d", {})
        d1_trend = "🔼 Güçlü YUKARI" if d1_ind.get('ema_short', 0) > d1_ind.get('ema_long', 0) else "🔽 Güçlü AŞAĞI"
        msg += f"**D1 TREND (Ana Yön):** {d1_trend}\n"
        
        h4_trend = "🔼 Yukarı" if h4_indicators.get('ema_short', 0) > h4_indicators.get('ema_long', 0) else "🔽 Aşağı"
        msg += f"**H4 RSI:** {rsi_4h:.1f} | **H4 EMA:** {h4_trend}\n"
        
        if cg_data["long_ratio"] is not None and cg_data["short_ratio"] is not None:
            long_short_ratio_msg = f"{cg_data['long_ratio']*100:.1f}% Long / {cg_data['short_ratio']*100:.1f}% Short"
            if cg_data['long_ratio'] > 0.65 or cg_data['short_ratio'] > 0.65:
                 long_short_ratio_msg = f"⚠️ {long_short_ratio_msg} (Aşırı Duyarlılık!)"
            msg += f"**L/S Oranı:** {long_short_ratio_msg}\n"
            
        alerts.append(msg)

    full_message = "\n\n" + "\n\n".join(alerts)
    send_telegram_message(full_message)
    logger.info("Analiz döngüsü tamamlandı.")

# -------------------------------
# Scheduler
# -------------------------------
schedule.every(1).hour.do(analyze_and_alert)      
schedule.every(15).minutes.do(check_immediate_alert)

logger.info("Bot çalışıyor... Her 1 saatte rapor + 15 dakikada anlık sinyal kontrolü aktif ✅")

while True:
    try:
        schedule.run_pending()
    except Exception as e:
        logger.error(f"Scheduler çalışma hatası: {e}")
    time.sleep(60)
