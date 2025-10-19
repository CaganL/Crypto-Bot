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
from datetime import datetime
import psycopg2
from urllib.parse import urlparse

# --- ML ENTEGRASYONU İÇİN EKLENENLER ---
import joblib 
import numpy as np 
# ----------------------------------------


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
BOT_TOKEN = os.getenv("BOT_TOKEN", "8320997161:AAFuNcpONcHLNdnitNehNZ2SOMskiGva6Qs")
CHAT_ID = int(os.getenv("CHAT_ID", "7294398674"))
COINGLASS_API_KEY = os.getenv("COINGLASS_API_KEY", "36176ba717504abc9235e612d1daeb0c")
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "")

DATABASE_URL = os.getenv("DATABASE_URL")

bot = Bot(BOT_TOKEN)

# -------------------------------
# Global Takipçiler (9 Coin ile Güncel Hali)
# -------------------------------
coin_aliases = {
    "BTCUSDT": ["BTC", "Bitcoin", "BTCUSDT"],
    "ETHUSDT": ["ETH", "Ethereum", "ETHUSDT"],
    "SOLUSDT": ["SOL", "Solana", "SOLUSDT"],
    "SUIUSDT": ["SUI", "Sui", "SUIUSDT"],
    "AVAXUSDT": ["AVAX", "Avalanche", "AVAXUSDT"],
    "BNBUSDT": ["BNB", "Binance Coin", "BNBUSDT"],
    "XRPUSDT": ["XRP", "Ripple", "XRPUSDT"],
    "ADAUSDT": ["ADA", "Cardano", "ADAUSDT"],
    "LINKUSDT": ["LINK", "Chainlink", "LINKUSDT"]
}

last_strong_alert = {}
last_prices = {}

# -------------------------------
# YENİ FONKSİYON: Veritabanı Bağlantısı ve Tablo Oluşturma
# -------------------------------
def get_db_connection():
    if not DATABASE_URL:
        logger.error("DATABASE_URL ortam değişkeni tanımlı değil.")
        return None

    try:
        url = urlparse(DATABASE_URL)
        conn = psycopg2.connect(
            database=url.path[1:],
            user=url.username,
            password=url.password,
            host=url.hostname,
            port=url.port
        )
        return conn
    except Exception as e:
        logger.error(f"Veritabanı bağlantı hatası: {e}")
        return None

def create_ml_table():
    conn = get_db_connection()
    if not conn: return

    cursor = conn.cursor()
    table_name = "ml_analysis_data"
    
    command = f"""
    CREATE TABLE IF NOT EXISTS {table_name} (
        id SERIAL PRIMARY KEY,
        timestamp TIMESTAMP WITH TIME ZONE,
        symbol VARCHAR(10),
        price NUMERIC,
        raw_score NUMERIC,
        
        d1_rsi NUMERIC, d1_macd NUMERIC, d1_ema_diff NUMERIC,
        h4_rsi NUMERIC, h4_macd NUMERIC, h4_ema_diff NUMERIC,
        h1_rsi NUMERIC, h1_macd NUMERIC, h1_ema_diff NUMERIC,
        m15_rsi NUMERIC, m15_macd NUMERIC, m15_ema_diff NUMERIC,
        
        long_ratio NUMERIC,
        short_ratio NUMERIC
    );
    """
    try:
        cursor.execute(command)
        conn.commit()
        logger.info(f"'{table_name}' tablosu kontrol edildi/oluşturuldu.")
    except Exception as e:
        logger.error(f"Tablo oluşturma hatası: {e}")
    finally:
        cursor.close()
        conn.close()

# -------------------------------
# Telegram Mesaj Fonksiyonu (Aynı Kaldı)
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
# Binance Fiyat Verisi (Aynı Kaldı)
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
# Teknik Analiz (Aynı Kaldı)
# -------------------------------
def calculate_technical_indicators(df):
    if df.empty: return {}
    result = {}
    rsi = RSIIndicator(close=df['close'], window=14)
    result['rsi'] = rsi.rsi().iloc[-1] if not rsi.rsi().empty else None
    ema_short = EMAIndicator(close=df['close'], window=12)
    ema_long = EMAIndicator(close=df['close'], window=26)
    result['ema_short'] = ema_short.ema_indicator().iloc[-1] if not ema_short.ema_indicator().empty else None
    result['ema_long'] = ema_long.ema_indicator().iloc[-1] if not ema_long.ema_indicator().empty else None
    macd_indicator = MACD(close=df['close'], window_slow=26, window_fast=12, window_sign=9)
    result['macd_diff'] = macd_indicator.macd_diff().iloc[-1] if not macd_indicator.macd_diff().empty else None
    
    result['last_close'] = df['close'].iloc[-1] if not df.empty else None
    if len(df) >= 2:
        last_close = df['close'].iloc[-1]
        prev_close = df['close'].iloc[-2]
        result['price_change'] = ((last_close - prev_close) / prev_close) * 100
    else:
        result['price_change'] = 0
    
    return result

# -------------------------------
# Ana Veri Çekme ve Analiz (Aynı Kaldı)
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
# CoinGlass / Binance Fallback (HIZLANDIRILDI)
# -------------------------------
def fetch_coinglass_data(symbol="BTC", retries=3):
    if not COINGLASS_API_KEY or True:
        logger.warning("CoinGlass atlandı, Binance Long/Short verisi kullanılıyor.")
        return fetch_binance_openinterest(symbol)

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
# YARDIMCI FONKSİYONLAR (LOAD/SAVE HISTORY)
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


# -------------------------------
# ML MODEL YÜKLEME VE FALLBACK TANIMLARI
# -------------------------------
ML_FEATURES = [
    'raw_score', 'd1_rsi', 'd1_macd', 'd1_ema_diff', 
    'h4_rsi', 'h4_macd', 'h4_ema_diff', 
    'h1_rsi', 'h1_macd', 'h1_ema_diff', 
    'm15_rsi', 'm15_macd', 'm15_ema_diff', 
    'long_ratio', 'short_ratio'
]
ML_MODEL = None # Başlangıçta model yok

try:
    # Modeli diskten yükleme
    ML_MODEL = joblib.load('crypto_ml_model.pkl')
    logger.info("ML modeli başarıyla yüklendi!")
except Exception as e:
    logger.error(f"ML Modeli Yükleme Hatası: {e}. Bot kural tabanlı devam edecek.")


# YENİ FALLBACK FONKSİYONU (ESKİ KURALLARINIZ BURAYA TAŞINDI)
def fallback_prediction(symbol, multi_indicators, cg_data=None):
    # Bu, ML modeli yüklenemediğinde çalışacak olan orijinal kural setinizdir.
    history = load_history(); score = 0
    
    # 0. 15 Dakikalık (15m) Erken Sinyal (+/- 0.5)
    m15_ind = multi_indicators.get("15m", {})
    if m15_ind.get('macd_diff') is not None:
        if m15_ind['macd_diff'] > 0: score += 0.5
        elif m15_ind['macd_diff'] < 0: score -= 0.5

    # 1. Günlük (1d) Trend (+/- 2.5)
    d1_ind = multi_indicators.get("1d", {})
    if d1_ind.get('ema_short') is not None and d1_ind.get('ema_long') is not None:
        if d1_ind.get('ema_short') > d1_ind.get('ema_long'): score += 2.5
        elif d1_ind.get('ema_short') < d1_ind.get('ema_long'): score -= 2.5
            
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
    if cg_data and cg_data.get("long_ratio") is not None and cg_data.get("short_ratio") is not None:
        if cg_data["long_ratio"] > 0.65: score -= 1.5
        elif cg_data["short_ratio"] > 0.65: score += 1.5
            
    # 5. Pozisyon Sürekliliği (+/- 0.5)
    last_pos = history.get(symbol, {}).get("last_position")
    if last_pos == "Long" and score > 0: score += 0.5
    elif last_pos == "Short" and score < 0: score -= 0.5

    # Sonuçlandırma
    if score >= 3.0: position = "Long (Güçlü - Kural)"
    elif score >= 1.0: position = "Long (Kural)"
    elif score <= -3.0: position = "Short (Güçlü - Kural)"
    elif score <= -1.0: position = "Short (Kural)"
    else: position = "Neutral (Kural)"

    confidence = min(max(abs(score) / 5 * 100, 0), 100) # Confidence score calculation logic
    save_history(history)
    return position, confidence, score
# -------------------------------
# -------------------------------


def ai_position_prediction(symbol, multi_indicators, cg_data=None):
    # Eğer ML Modeli yüklenemediyse, eski kural tabanlı sistemi kullan.
    if ML_MODEL is None:
        # Fallback'ten gelen tahmin ve skor kullanılır.
        return fallback_prediction(symbol, multi_indicators, cg_data)
        
    # 1. Özellikleri (Features) Hazırlama
    
    # ML modelinin beklediği 15 özellik listesini hazırlama
    data = {}
    
    # Kural tabanlı sistemin skorunu al (ML modelinin ilk özelliği)
    _, _, raw_score_old = fallback_prediction(symbol, multi_indicators, cg_data)
    data['raw_score'] = raw_score_old
    
    # Diğer 14 özellik
    for interval in ["1d", "4h", "1h", "15m"]:
        ind = multi_indicators.get(interval, {})
        ema_diff = ind.get('ema_short') - ind.get('ema_long') if ind.get('ema_short') is not None and ind.get('ema_long') is not None else None
        
        data[f'{interval}_rsi'] = ind.get('rsi')
        data[f'{interval}_macd'] = ind.get('macd_diff')
        data[f'{interval}_ema_diff'] = ema_diff
        
    data['long_ratio'] = cg_data.get('long_ratio')
    data['short_ratio'] = cg_data.get('short_ratio')
    
    # DataFrame oluşturma (Model, Pandas DataFrame bekler)
    X_predict = pd.DataFrame([data], columns=ML_FEATURES)
    
    # Boş (None) değerleri 0 ile doldur (Model None gönderemeyiz)
    X_predict = X_predict.fillna(0)

    # 2. Tahmin Yapma
    prediction = ML_MODEL.predict(X_predict)[0] # Tahmin: 1 (Long), 0 (Neutral), veya -1 (Short)
    
    # 3. Sonuçları Yorumlama ve Filtreleme (YENİ MANTIK)
    
    # Modelin her bir sınıfa olan güvenini (olasılığını) al.
    try:
        probabilities = ML_MODEL.predict_proba(X_predict)[0]
    except Exception as e:
        logger.warning(f"predict_proba hatası: {e}. Sabit güven skoru kullanılıyor.")
        probabilities = [0.33, 0.34, 0.33] # Varsayılan dağılım
        
    # Tahmin edilen sınıfın olasılığını bul (Long=1, Neutral=0, Short=-1)
    if prediction == 1:
        confidence = probabilities[2] # Long sınıfının olasılığı (RandomForest sınıf dizini: -1=0, 0=1, 1=2)
        position_str = "Long (ML)"
    elif prediction == -1:
        confidence = probabilities[0] # Short sınıfının olasılığı
        position_str = "Short (ML)"
    else: # prediction == 0 (Neutral)
        confidence = probabilities[1] # Neutral sınıfının olasılığı
        position_str = "Neutral (ML)"
    
    confidence_pct = confidence * 100
    raw_score = 0 # Ham skor artık sadece raporlama için kullanılır
    
    # D1 Trendini tekrar alalım (ANA GÜVENLİK FİLTRESİ)
    d1_ind = multi_indicators.get("1d", {})
    is_d1_trend_up = d1_ind.get('ema_short', 0) > d1_ind.get('ema_long', 0)
    is_d1_trend_down = d1_ind.get('ema_short', 0) < d1_ind.get('ema_long', 0)
    
    # FİLTRELEME MANTIĞI: ML tahmini Ana Trendin tersi ise, güveni düşür veya Nötr yap.
    if (position_str.startswith("Long") and is_d1_trend_down) or \
       (position_str.startswith("Short") and is_d1_trend_up):
        
        position_str = "Neutral (Filtre)" # Ters trendde Nötr sinyaline düşür
        confidence_pct = max(confidence_pct - 30, 40) # Güven skorunu 30 puan düşür, minimum 40 olsun

    
    # Sonuçlandırma için history.json güncelleme
    history = load_history()
    history[symbol] = {"last_position": position_str.split()[0]}
    save_history(history)
    
    return position_str, confidence_pct, raw_score
