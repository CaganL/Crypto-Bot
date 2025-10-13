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
# Railway'de ortam değişkenleri kullanılır. BOT_TOKEN, CHAT_ID vb. Railway'de tanımlı olmalıdır.
BOT_TOKEN = os.getenv("BOT_TOKEN", "8320997161:AAFuNcpONcHLNdnitNehNZ2SOMskiGva6Qs") # Örnek değerler, Railway'deki ortam değişkenleri kullanılacak
CHAT_ID = int(os.getenv("CHAT_ID", "7294398674")) 
COINGLASS_API_KEY = os.getenv("COINGLASS_API_KEY", "36176ba717504abc9235e612d1daeb0c")
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "")

# PostgreSQL Bağlantı URL'si (Railway Otomatik Sağlar)
DATABASE_URL = os.getenv("DATABASE_URL")

bot = Bot(BOT_TOKEN)

# -------------------------------
# Global Takipçiler (9 Coin ile Güncel Hali)
# -------------------------------
coin_aliases = {
    # Mevcut Coin'ler
    "BTCUSDT": ["BTC", "Bitcoin", "BTCUSDT"],
    "ETHUSDT": ["ETH", "Ethereum", "ETHUSDT"],
    "SOLUSDT": ["SOL", "Solana", "SOLUSDT"],
    "SUIUSDT": ["SUI", "Sui", "SUIUSDT"],
    "AVAXUSDT": ["AVAX", "Avalanche", "AVAXUSDT"],
    
    # YENİ EKLENENLER (Öğrenmeyi hızlandırır)
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
    # ML verisi için tablo oluşturma (mevcut değilse)
    table_name = "ml_analysis_data"
    
    # Tüm indikatörler, long/short ve skorlar için sütunlar tanımlanır
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
# CoinGlass / Binance Fallback (Aynı Kaldı)
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
# AI Öğrenme Sistemi (Aynı Kaldı)
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
# Ani Fiyat Dalgalanma Uyarısı (%5) (Aynı Kaldı)
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
# YENİ FONKSİYON: PostgreSQL'e Kayıt (KESİN DÜZELTME)
# -------------------------------
def save_ml_data_to_db(coin, multi_indicators, cg_data, raw_score):
    conn = get_db_connection()
    if not conn: return
    
    cursor = conn.cursor()
    table_name = "ml_analysis_data"
    
    current_time = datetime.now()
    current_price = multi_indicators.get("4h", {}).get('last_close')
    
    # 1. Ham verileri toplama (Hala np.float64 olabilir)
    data_list = [current_time, coin, current_price, raw_score]

    for interval in ["1d", "4h", "1h", "15m"]:
        ind = multi_indicators.get(interval, {})
        # Not: Ema farkı hesaplaması da numpy değeri üretebilir
        ema_diff = ind.get('ema_short') - ind.get('ema_long') if ind.get('ema_short') is not None and ind.get('ema_long') is not None else None
        data_list.extend([
            ind.get('rsi'),
            ind.get('macd_diff'),
            ema_diff
        ])
    
    data_list.extend([cg_data.get('long_ratio'), cg_data.get('short_ratio')])

    # 🚨🚨 KRİTİK DÜZELTME: Tüm sayısal verileri zorla Python float'a çevirme
    final_data_list = []
    for item in data_list:
        # String, datetime veya None ise dokunma
        if item is None or isinstance(item, (str, datetime)):
            final_data_list.append(item)
        else:
            try:
                # Gelen her sayısal değeri (Python float, int, NumPy float64) float'a dönüştür
                final_data_list.append(float(item))
            except (ValueError, TypeError):
                # Dönüştürülemezse None olarak ekle
                final_data_list.append(None)
    # 🚨🚨 KESİN DÜZELTME SONU 🚨🚨

    # SQL komutu hazırlama
    columns = [
        'timestamp', 'symbol', 'price', 'raw_score',
        'd1_rsi', 'd1_macd', 'd1_ema_diff', 
        'h4_rsi', 'h4_macd', 'h4_ema_diff', 
        'h1_rsi', 'h1_macd', 'h1_ema_diff', 
        'm15_rsi', 'm15_macd', 'm15_ema_diff', 
        'long_ratio', 'short_ratio'
    ]
    
    values_placeholder = ', '.join(['%s'] * len(columns))
    insert_command = f"INSERT INTO {table_name} ({', '.join(columns)}) VALUES ({values_placeholder})"
    
    try:
        # DÜZELTİLMİŞ final_data_list'i kullan
        cursor.execute(insert_command, final_data_list)
        conn.commit()
        logger.info(f"{coin} için ML verisi veritabanına kaydedildi.")
    except Exception as e:
        # Bu hata artık "schema 'np' does not exist" olmamalı
        logger.error(f"Veritabanına veri yazma hatası ({coin}): {e}")
    finally:
        cursor.close()
        conn.close()

# -------------------------------
# ANLIK SİNYAL KONTROLÜ (Aynı Kaldı)
# -------------------------------
def check_immediate_alert():
    global last_strong_alert
    
    for coin in coin_aliases.keys():
        coin_short = coin.replace("USDT", "")
        
        multi_indicators = fetch_multi_timeframe_analysis(coin)
        cg_data = fetch_coinglass_data(coin_short)
        position, confidence, raw_score = ai_position_prediction(coin, multi_indicators, cg_data)
        
        # ... (Anlık uyarı mantığı aynı kalır) ...
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
        # ML VERİ KAYIT ADIMI (Veritabanı)
        # -----------------------------
        save_ml_data_to_db(coin, multi_indicators, cg_data, raw_score)

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
        
        if cg_data and cg_data.get("long_ratio") is not None and cg_data.get("short_ratio") is not None:
            long_short_ratio_msg = f"{cg_data['long_ratio']*100:.1f}% Long / {cg_data['short_ratio']*100:.1f}% Short"
            if cg_data['long_ratio'] > 0.65 or cg_data['short_ratio'] > 0.65:
                long_short_ratio_msg = f"⚠️ {long_short_ratio_msg} (Aşırı Duyarlılık!)"
            msg += f"**L/S Oranı:** {long_short_ratio_msg}\n"
            
        alerts.append(msg)

    full_message = "\n\n" + "\n\n".join(alerts)
    send_telegram_message(full_message)
    logger.info("Analiz döngüsü tamamlandı.")

# -------------------------------
# Bot Başlangıç ve Scheduler
# -------------------------------
if __name__ == "__main__":
    # Bot çalışmaya başlamadan önce tabloyu kontrol et/oluştur
    create_ml_table()
    
    # 🚨 KRİTİK EKLENTİ: Bot her başladığında hemen bir kereliğine analiz ve kaydı zorla
    analyze_and_alert()
    
    # Scheduler ayarları
    schedule.every(1).hour.do(analyze_and_alert)     
    schedule.every(15).minutes.do(check_immediate_alert)

    logger.info("Bot çalışıyor... Kalıcı veritabanı kaydı aktif ✅")

    while True:
        try:
            schedule.run_pending()
        except Exception as e:
            logger.error(f"Scheduler çalışma hatası: {e}")
        time.sleep(60)
