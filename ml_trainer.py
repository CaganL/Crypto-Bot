# ml_trainer.py (TAM OTOMATİK EĞİTMEN)
import os
import pandas as pd
import psycopg2
from urllib.parse import urlparse
from datetime import datetime
import numpy as np
import io # Hafızada işlem yapmak için

# ML Kütüphaneleri
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score
import joblib

# AYARLAR
# Railway üzerinde çalışırken bu otomatik olarak ortam değişkeninden alınır.
DATABASE_URL = os.getenv("DATABASE_URL")

def get_db_connection():
    if not DATABASE_URL:
        print("HATA: DATABASE_URL tanımlı değil.")
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
        print(f"Veritabanı bağlantı hatası: {e}")
        return None

def create_model_table():
    """Modeli saklayacak tabloyu oluşturur."""
    conn = get_db_connection()
    if not conn: return
    cursor = conn.cursor()
    # Modeli BYTEA (Binary Data) olarak saklayacağız
    command = """
    CREATE TABLE IF NOT EXISTS ml_models (
        id SERIAL PRIMARY KEY,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        model_data BYTEA,
        accuracy NUMERIC
    );
    """
    try:
        cursor.execute(command)
        conn.commit()
        print("'ml_models' tablosu kontrol edildi/oluşturuldu.")
    except Exception as e:
        print(f"Tablo oluşturma hatası: {e}")
    finally:
        cursor.close()
        conn.close()

def fetch_all_ml_data():
    conn = get_db_connection()
    if not conn: return pd.DataFrame()
    
    try:
        query = "SELECT * FROM ml_analysis_data ORDER BY timestamp ASC;"
        df = pd.read_sql(query, conn)
        conn.close()
        print(f"Veritabanından {len(df)} satır veri çekildi.")
        
        df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
        numeric_cols = df.columns.drop(['id', 'timestamp', 'symbol'])
        for col in numeric_cols:
            df[col] = pd.to_numeric(df[col], errors='coerce') 
        return df
    except Exception as e:
        print(f"Veri çekme hatası: {e}")
        return pd.DataFrame()

def save_model_to_db(model, accuracy):
    """Eğitilen modeli veritabanına binary olarak kaydeder."""
    conn = get_db_connection()
    if not conn: return

    try:
        cursor = conn.cursor()
        
        # Modeli hafızada bir dosyaya (BytesIO) kaydet
        buffer = io.BytesIO()
        joblib.dump(model, buffer)
        model_binary = buffer.getvalue() # Binary veriyi al
        
        # Veritabanına yaz
        cursor.execute(
            "INSERT INTO ml_models (model_data, accuracy) VALUES (%s, %s)",
            (psycopg2.Binary(model_binary), accuracy)
        )
        conn.commit()
        print(f"✅ Yeni Model Veritabanına Kaydedildi! (Başarı: {accuracy:.3f})")
        
    except Exception as e:
        print(f"Model kaydetme hatası: {e}")
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

def prepare_and_train(df: pd.DataFrame):
    if df.empty: return

    # 1. Hedef Değişkeni Oluşturma
    df['future_price'] = df.groupby('symbol')['price'].shift(-4) 
    df['price_pct_change'] = ((df['future_price'] - df['price']) / df['price']) * 100

    df['target'] = 0 
    df.loc[df['price_pct_change'] >= 0.5, 'target'] = 1  # LONG
    df.loc[df['price_pct_change'] <= -0.5, 'target'] = -1 # SHORT

    df.dropna(subset=['future_price', 'raw_score', 'd1_rsi', 'long_ratio'], inplace=True)
    
    if len(df) < 50:
        print("Yetersiz veri.")
        return

    # 2. Eğitim
    features = [col for col in df.columns if col not in ['id', 'timestamp', 'symbol', 'price', 'future_price', 'price_pct_change', 'target']]
    X = df[features]
    y = df['target']
    
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)
    
    print(f"--- Model Eğitiliyor (Veri: {len(df)} satır) ---")
    model = RandomForestClassifier(n_estimators=150, random_state=42, class_weight='balanced', n_jobs=-1) 
    model.fit(X_train, y_train)
    
    # 3. Değerlendirme
    y_pred = model.predict(X_test)
    accuracy = accuracy_score(y_test, y_pred)
    print(f"Model Başarısı: {accuracy:.3f}")

    # 4. Veritabanına Kaydetme (DOSYA DEĞİL DB)
    save_model_to_db(model, accuracy)

if __name__ == "__main__":
    print("Otomatik ML Eğitimi Başlıyor...")
    create_model_table() # Tabloyu oluştur
    data_df = fetch_all_ml_data()
    if not data_df.empty:
        prepare_and_train(data_df)
    else:
        print("Veri bulunamadı.")
