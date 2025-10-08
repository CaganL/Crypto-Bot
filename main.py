# -------------------------------
# Gerekli Kütüphaneler (LOGGING EKLENDI)
# -------------------------------
import os
import requests
import pandas as pd
import schedule
import time
import json
import logging # <-- Yeni! Logging kütüphanesi
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD
from ta.volatility import BollingerBands
from telegram import Bot
from googletrans import Translator

# -------------------------------
# Logging Kurulumu
# -------------------------------
# Log formatını ve dosyasını ayarla
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log"), # log.txt dosyasına yaz
        logging.StreamHandler()        # Konsola da yaz
    ]
)
logger = logging.getLogger(__name__)

# -------------------------------
# API Anahtarları ve Telegram
# (Daha güvenli bir çözüm için .env kullanılması önerilir)
# -------------------------------
BOT_TOKEN = "8320997161:AAFuNcpONcHLNdnitNehNZ2SOMskiGva6Qs"
CHAT_ID = 7294398674
TAAPI_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJjbHVlIjoiNjhlM2RkOTk4MDZmZjE2NTFlOGY3NjlkIiwiaWF0IjoxNzU5ODYyNDEyLCJleHAiOjMzMjY0MzI2NDEyfQ.dmvJC5-LNScEkhWdziBA21Ig8hc2oGsaNNohyfrIaD4"
COINGLASS_API_KEY = "36176ba717504abc9235e612d1daeb0c"
NEWSAPI_KEY = "737732d907a6418e8542100a79ed705b"

bot = Bot(BOT_TOKEN)
translator = Translator()

# -------------------------------
# Coin Alias Sistemi
# -------------------------------
coin_aliases = {
    "BTCUSDT": ["BTC", "Bitcoin", "BTCUSDT"],
    "ETHUSDT": ["ETH", "Ethereum", "ETHUSDT"],
    "SOLUSDT": ["SOL", "Solana", "SOLUSDT"],
    "SUIUSDT": ["SUI", "Sui", "SUIUSDT"],
    "AVAXUSDT": ["AVAX", "Avalanche", "AVAXUSDT"]
}

# -------------------------------
# Telegram Mesaj Fonksiyonu
# -------------------------------
def send_telegram_message(message):
    try:
        max_length = 4000  # Telegram karakter sınırı
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
# (Yeni: interval parametresi ile her zaman dilimini çekebilir)
# -------------------------------
def fetch_binance_klines(symbol="BTCUSDT", interval="4h", limit=100):
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
        data = requests.get(url, timeout=10).json()
        if not data or isinstance(data, dict) and 'code' in data:
            logger.error(f"Binance API hatası ({symbol}, {interval}): {data}")
            return pd.DataFrame()

        df = pd.DataFrame(data, columns=[
            'open_time','open','high','low','close','volume',
            'close_time','quote_asset_volume','trades','taker_base','taker_quote','ignore'
        ])
        df['close'] = df['close'].astype(float)
        return df
    except Exception as e:
        logger.error(f"Binance klines çekme hatası ({symbol}, {interval}): {e}")
        return pd.DataFrame()


# -------------------------------
# Teknik Analiz
# -------------------------------
def calculate_technical_indicators(df):
    if df.empty:
        return {}
    
    result = {}
    rsi = RSIIndicator(close=df['close'], window=14)
    result['rsi'] = rsi.rsi().iloc[-1] if not rsi.rsi().empty else None

    ema_short = EMAIndicator(close=df['close'], window=12)
    ema_long = EMAIndicator(close=df['close'], window=26)
    result['ema_short'] = ema_short.ema_indicator().iloc[-1] if not ema_short.ema_indicator().empty else None
    result['ema_long'] = ema_long.ema_indicator().iloc[-1] if not ema_long.ema_indicator().empty else None

    macd_indicator = MACD(close=df['close'], window_slow=26, window_fast=12, window_sign=9)
    result['macd_diff'] = macd_indicator.macd_diff().iloc[-1] if not macd_indicator.macd_diff().empty else None

    # Sadece 4h için fiyat değişimi hesapla
    if len(df) >= 2:
        last_close = df['close'].iloc[-1]
        prev_close = df['close'].iloc[-2]
        result['price_change'] = ((last_close - prev_close) / prev_close) * 100
    else:
        result['price_change'] = 0
        
    result['last_close'] = df['close'].iloc[-1] if not df.empty else None

    return result

# -------------------------------
# Ana Veri Çekme ve Analiz (Çoklu Zaman Dilimli)
# -------------------------------
def fetch_multi_timeframe_analysis(symbol):
    analysis = {}
    
    # 1h, 4h, 1d verilerini çek
    intervals = {"1h": 100, "4h": 100, "1d": 100}
    
    for interval, limit in intervals.items():
        df = fetch_binance_klines(symbol=symbol, interval=interval, limit=limit)
        indicators = calculate_technical_indicators(df)
        analysis[interval] = indicators
        
    return analysis

# -------------------------------
# CoinGlass API / Binance fallback (Mevcut kodunuz)
# -------------------------------
# ... (Bu bölüm değiştirilmedi, CoinGlass veya Binance Fallback mantığınız korundu) ...
def fetch_coinglass_data(symbol="BTC", retries=3):
    if not COINGLASS_API_KEY:
        return fetch_binance_openinterest(symbol)

    for attempt in range(retries):
        try:
            url = f"https://open-api.coinglass.com/api/pro/v1/futures/openInterest?symbol={symbol}"
            headers = {"coinglassSecret": COINGLASS_API_KEY}
            r = requests.get(url, headers=headers, timeout=10)

            if r.status_code != 200:
                logger.warning(f"CoinGlass API HTTP {r.status_code}: {r.text[:100]}")
                time.sleep(2)
                continue

            if not r.text.strip():
                logger.warning(f"CoinGlass API boş yanıt döndü (deneme {attempt+1})")
                time.sleep(2)
                continue

            data = r.json()
            if not data.get("data"):
                logger.warning(f"CoinGlass API veri boş (deneme {attempt+1})")
                time.sleep(2)
                continue

            long_ratio = data.get("data", {}).get("longRate")
            short_ratio = data.get("data", {}).get("shortRate")
            return {"long_ratio": long_ratio, "short_ratio": short_ratio}

        except Exception as e:
            logger.error(f"CoinGlass API hata ({attempt+1}/{retries}): {e}")
            time.sleep(2)

    # 3 denemeden sonra Binance fallback
    return fetch_binance_openinterest(symbol)

# -------------------------------
# Binance Fallback: Hassas OpenInterest + FundingRate (Mevcut kodunuz)
# -------------------------------
def fetch_binance_openinterest(symbol="BTC"):
    try:
        # Open Interest
        url_oi = f"https://fapi.binance.com/futures/data/openInterestHist?symbol={symbol}USDT&period=4h&limit=1"
        r_oi = requests.get(url_oi, timeout=10)
        r_oi.raise_for_status()
        data_oi = r_oi.json()
        oi_total = float(data_oi[-1]['sumOpenInterest']) if data_oi else 0

        # Funding Rate
        url_funding = f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol}USDT&limit=1"
        r_f = requests.get(url_funding, timeout=10)
        r_f.raise_for_status()
        data_f = r_f.json()
        funding_rate = float(data_f[0]['fundingRate']) if data_f else 0

        # Hassas Long/Short oran tahmini
        long_ratio = 0.5 + (funding_rate * 10) + (0.05 * (oi_total / max(oi_total, 1e6)))
        long_ratio = max(0, min(long_ratio, 1))
        short_ratio = 1 - long_ratio

        return {"long_ratio": long_ratio, "short_ratio": short_ratio, "funding_rate": funding_rate}

    except Exception as e:
        logger.error(f"Binance OpenInterest/FundingRate hata: {e}")
        return {"long_ratio": None, "short_ratio": None, "funding_rate": None}

# -------------------------------
# NewsAPI Haberleri (Mevcut kodunuz)
# -------------------------------
def fetch_news():
    try:
        url = f"https://newsapi.org/v2/top-headlines?category=business&language=en&apiKey={NEWSAPI_KEY}"
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        articles = r.json().get("articles", [])
        return [f"{a['title']} - {a['url']}" for a in articles[:5]]
    except Exception as e:
        logger.warning(f"NewsAPI hata: {e}")
        return []

# -------------------------------
# AI Öğrenme Sistemi (GELİŞMİŞ AĞIRLIKLI SKORLAMA)
# -------------------------------
history_file = "history.json"

def load_history():
    if os.path.exists(history_file):
        with open(history_file, "r") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                logger.warning(f"{history_file} dosyası bozuk, yeniden oluşturuluyor.")
                return {}
    return {}

def save_history(history):
    with open(history_file, "w") as f:
        json.dump(history, f)

def ai_position_prediction(symbol, multi_indicators, cg_data=None):
    history = load_history()
    score = 0
    
    # ---------------------------
    # 1. Günlük (1d) Trend (AĞIRLIK: +/- 2.5)
    # ---------------------------
    # Büyük resim trendi en yüksek ağırlığa sahiptir.
    d1_ind = multi_indicators.get("1d", {})
    if d1_ind.get('ema_short') > d1_ind.get('ema_long', 0):
        score += 2.5 # Güçlü Long Sinyali
    elif d1_ind.get('ema_short') < d1_ind.get('ema_long', 0):
        score -= 2.5 # Güçlü Short Sinyali
        
    # ---------------------------
    # 2. 4 Saatlik (4h) Momentum (AĞIRLIK: +/- 1.5)
    # ---------------------------
    h4_ind = multi_indicators.get("4h", {})
    # RSI
    if h4_ind.get('rsi') is not None:
        if h4_ind['rsi'] < 30:
            score += 1.5 # Aşırı Satış
        elif h4_ind['rsi'] > 70:
            score -= 1.5 # Aşırı Alım

    # ---------------------------
    # 3. 1 Saatlik (1h) Momentum Değişimi (AĞIRLIK: +/- 1.0)
    # ---------------------------
    h1_ind = multi_indicators.get("1h", {})
    # MACD Çevrimi
    if h1_ind.get('macd_diff') is not None:
        if h1_ind['macd_diff'] > 0:
            score += 1.0 # Long momentumu artıyor
        elif h1_ind['macd_diff'] < 0:
            score -= 1.0 # Short momentumu artıyor

    # ---------------------------
    # 4. Long/Short Oranı (Kontrarian AĞIRLIK: +/- 1.5)
    # ---------------------------
    # Oranlar aşırıya kaçarsa tersine dönüş sinyali olarak kabul edilir.
    if cg_data and cg_data["long_ratio"] and cg_data["short_ratio"]:
        if cg_data["long_ratio"] > 0.65:
            score -= 1.5 # Aşırı Long: Düzeltme gelebilir (Kontrarian)
        elif cg_data["short_ratio"] > 0.65:
            score += 1.5 # Aşırı Short: Short sıkışması gelebilir (Kontrarian)
            
    # ---------------------------
    # 5. Pozisyon Sürekliliği (Momentum Desteği: +/- 0.5)
    # ---------------------------
    # Mevcut pozisyonu destekliyorsa hafif bir bonus ver.
    last_pos = history.get(symbol, {}).get("last_position")
    if last_pos == "Long" and score > 0:
        score += 0.5
    elif last_pos == "Short" and score < 0:
        score -= 0.5

    # ---------------------------
    # Sonuçlandırma
    # ---------------------------
    if score >= 3.0: # Yüksek eşik
        position = "Long (Güçlü)"
    elif score >= 1.0:
        position = "Long"
    elif score <= -3.0: # Düşük eşik
        position = "Short (Güçlü)"
    elif score <= -1.0:
        position = "Short"
    else:
        position = "Neutral"

    # Güven skoru: Mutlak skorun 5'e bölünmesi (max skor ~5)
    confidence = min(abs(score / 5) * 100, 100) 
    
    history[symbol] = {"last_position": position.split()[0]} # Güçlü/Zayıf bilgisini kaydetme
    save_history(history)
    return position, confidence, score

# -------------------------------
# Ani Fiyat Dalgalanma Uyarısı (%5) (Mevcut kodunuz)
# -------------------------------
last_prices = {}

def check_price_spike(symbol, current_price):
    global last_prices
    if current_price is None: return # Fiyat yoksa kontrol etme
    
    if symbol in last_prices:
        old_price = last_prices[symbol]
        change_pct = ((current_price - old_price) / old_price) * 100
        if abs(change_pct) >= 5:
            direction = "📈 YÜKSELDİ" if change_pct > 0 else "📉 DÜŞTÜ"
            msg = f"⚠️ **{symbol} Fiyat Uyarısı (ŞOK!):**\nFiyat son kontrolünden beri %{change_pct:.2f} {direction}!\nAnlık Fiyat: {current_price:.2f} USDT"
            send_telegram_message(msg)
    last_prices[symbol] = current_price

# -------------------------------
# Ana Analiz Fonksiyonu
# -------------------------------
def analyze_and_alert():
    logger.info("Analiz döngüsü başlatılıyor.")
    alerts = []
    
    for coin in coin_aliases.keys():
        coin_short = coin.replace("USDT", "")
        
        # Çoklu zaman dilimi verilerini çek
        multi_indicators = fetch_multi_timeframe_analysis(coin)
        
        # 4h verilerinden gerekli bilgileri al
        h4_indicators = multi_indicators.get("4h", {})
        current_price = h4_indicators.get('last_close')
        price_change_4h = h4_indicators.get('price_change', 0)
        rsi_4h = h4_indicators.get('rsi')

        if current_price is None:
             logger.warning(f"{coin} için fiyat verisi çekilemedi. Analiz atlanıyor.")
             continue
        
        # Fiyat sıçrama kontrolü
        check_price_spike(coin, current_price)
        
        # CoinGlass/Binance verisi
        cg_data = fetch_coinglass_data(coin_short)
        
        # AI Tahmini
        position, confidence, raw_score = ai_position_prediction(coin, multi_indicators, cg_data)

        # Rapor Mesajı
        msg = f"--- 🤖 **{coin} Çoklu Zaman Dilimi Raporu** ---\n"
        msg += f"💰 **Fiyat:** {current_price:.2f} USDT ({price_change_4h:+.2f}% son 4 saatte)\n"
        msg += f"🔥 **AI Tahmini:** **{position}**\n"
        msg += f"📊 **Güven Skoru:** **{confidence:.0f}%** (Skor: {raw_score:+.1f})\n"
        msg += "\n"
        
        # Detaylar
        msg += "--- DETAYLI ANALİZ ---\n"
        
        # 1D
        d1_ind = multi_indicators.get("1d", {})
        d1_trend = "🔼 Güçlü YUKARI" if d1_ind.get('ema_short', 0) > d1_ind.get('ema_long', 0) else "🔽 Güçlü AŞAĞI"
        msg += f"**D1 TREND (Ana Yön):** {d1_trend}\n"
        
        # 4H
        h4_trend = "🔼 Yukarı" if h4_indicators.get('ema_short', 0) > h4_indicators.get('ema_long', 0) else "🔽 Aşağı"
        msg += f"**H4 RSI:** {rsi_4h:.1f} | **H4 EMA:** {h4_trend}\n"
        
        # Long/Short Oran
        if cg_data["long_ratio"] is not None and cg_data["short_ratio"] is not None:
            long_short_ratio_msg = f"{cg_data['long_ratio']*100:.1f}% Long / {cg_data['short_ratio']*100:.1f}% Short"
            if cg_data['long_ratio'] > 0.65 or cg_data['short_ratio'] > 0.65:
                 long_short_ratio_msg = f"⚠️ {long_short_ratio_msg} (Aşırı Duyarlılık!)"
            msg += f"**L/S Oranı:** {long_short_ratio_msg}\n"
            
        # Haberler (Sadece bir kez çekiliyor, eklemeyi buraya koyalım)
        if coin == list(coin_aliases.keys())[0]: # Sadece BTC için haberi al ve ekle
             news = fetch_news()
             if news:
                 msg += "\n📰 **Son Haberler:**\n" + "\n".join(news)

        alerts.append(msg)

    full_message = "\n\n" + "\n\n".join(alerts)
    send_telegram_message(full_message)
    logger.info("Analiz döngüsü tamamlandı.")

# -------------------------------
# Scheduler
# -------------------------------
schedule.every(1).hours.do(analyze_and_alert)
logger.info("Bot çalışıyor... Her 2 saatte analiz + anlık %5 fiyat uyarısı aktif ✅")

while True:
    try:
        schedule.run_pending()
    except Exception as e:
        logger.error(f"Scheduler çalışma hatası: {e}")
    time.sleep(60)

