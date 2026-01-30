import logging
import feedparser
import ccxt
import pandas as pd
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler
import asyncio
import os
import json

# --- GÜVENLİK ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    raise ValueError("❌ HATA: API Anahtarları Railway Variables kısmında eksik!")

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- DEDEKTİF FONKSİYONU ---
async def list_available_models():
    # Google'a "Elinde ne var ne yok göster" diyoruz
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}"
    
    try:
        response = await asyncio.to_thread(requests.get, url)
        
        if response.status_code == 200:
            data = response.json()
            if 'models' in data:
                # Sadece sohbet edebilen modelleri filtrele
                chat_models = [m['name'] for m in data['models'] if 'generateContent' in m['supportedGenerationMethods']]
                
                if not chat_models:
                    return "⚠️ Google cevap verdi ama sohbet modeli bulamadı. API Key yetkilerini kontrol et."
                
                # Modelleri listele
                model_list = "\n".join(chat_models)
                return f"✅ İŞTE GOOGLE'IN KABUL ETTİĞİ LİSTE:\n\n{model_list}\n\n(Bu listeyi bana kopyala at!)"
            else:
                return "⚠️ Liste boş döndü."
        else:
            return f"❌ BAĞLANTI HATASI ({response.status_code}):\n{response.text}"
            
    except Exception as e:
        return f"❌ KRİTİK HATA: {str(e)}"

# --- KOMUTLAR ---
async def incele(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🕵️‍♂️ Google sunucularına bağlanıp model listesi isteniyor... Bekle.")
    
    # Dedektif çalışıyor
    result = await list_available_models()
    
    await update.message.reply_text(result)

if __name__ == '__main__':
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("incele", incele))
    app.run_polling()
