import sqlite3
import requests
import time
import urllib3
import re
import threading
import pandas as pd
import numpy as np
from datetime import datetime
import os

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ===== KONFIGURASI =====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
DEFAULT_CHAT_ID = os.getenv("DEFAULT_CHAT_ID", "")

# Pengaturan Symbol & Exchange
TV_SYMBOL = "XAUUSD"
TV_EXCHANGE = "FOREXCOM"

# Global variables untuk tracking state alert
last_pre_alert_time = None
last_closed_candle_time = None

# ===== DATABASE SETUP =====
conn = sqlite3.connect('trading_stats.db', check_same_thread=False)
c = conn.cursor()

c.execute('''CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pair TEXT,
                direction TEXT,
                entry_price REAL,
                fibo_50 REAL,
                tp_price REAL,
                sl_price REAL,
                rsi REAL,
                body_size REAL,
                wick_pct REAL,
                result TEXT DEFAULT 'PENDING',
                exit_price REAL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )''')
conn.commit()

# ===== FUNGSI STATISTIK & DATABASE =====
def get_winrate():
    c.execute("SELECT count(*) FROM trades WHERE result='WIN'")
    wins = c.fetchone()[0]
    c.execute("SELECT count(*) FROM trades WHERE result IN ('WIN', 'LOSS')")
    total = c.fetchone()[0]
    if total == 0:
        return 0.0, 0
    return round((wins / total) * 100, 1), total

def get_probability_analysis():
    """Menganalisis karakteristik OHLC (Body, Wick, RSI) dari trade WIN vs LOSS"""
    c.execute("SELECT body_size, wick_pct, rsi, result FROM trades WHERE result IN ('WIN', 'LOSS')")
    data = c.fetchall()
    
    if not data:
        return "Belum ada data evaluasi yang cukup untuk analisis statistik."

    df_stats = pd.DataFrame(data, columns=['body_size', 'wick_pct', 'rsi', 'result'])
    win_df = df_stats[df_stats['result'] == 'WIN']
    loss_df = df_stats[df_stats['result'] == 'LOSS']

    win_count = len(win_df)
    loss_count = len(loss_df)
    total = win_count + loss_count

    win_rate = (win_count / total * 100) if total > 0 else 0

    avg_win_body = win_df['body_size'].mean() if not win_df.empty else 0
    avg_loss_body = loss_df['body_size'].mean() if not loss_df.empty else 0
    avg_win_wick = win_df['wick_pct'].mean() if not win_df.empty else 0
    avg_loss_wick = loss_df['wick_pct'].mean() if not loss_df.empty else 0

    report = f"""
📊 <b>ANALISIS PROBABILITAS OHLC DATA</b>
 Total Trade Evaluasi: <b>{total}</b>
 Winrate Akumulasi: <b>{win_rate:.1f}%</b>

🟢 <b>PROFIL TRADE WIN ({win_count}):</b>
• Rata-rata Body: <code>{avg_win_body:.1f} Pips</code>
• Rata-rata Wick %: <code>{avg_win_wick:.1f}%</code>

🔴 <b>PROFIL TRADE LOSS ({loss_count}):</b>
• Rata-rata Body: <code>{avg_loss_body:.1f} Pips</code>
• Rata-rata Wick %: <code>{avg_loss_wick:.1f}%</code>
    """
    return report

def log_signal(pair, direction, entry, fibo_50, tp, sl, rsi, body_size, wick_pct, timestamp_str=None):
    if timestamp_str:
        c.execute("""INSERT INTO trades (pair, direction, entry_price, fibo_50, tp_price, sl_price, rsi, body_size, wick_pct, result, timestamp) 
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)""", 
                  (pair, direction, entry, fibo_50, tp, sl, rsi, body_size, wick_pct, timestamp_str))
    else:
        c.execute("""INSERT INTO trades (pair, direction, entry_price, fibo_50, tp_price, sl_price, rsi, body_size, wick_pct, result) 
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING')""", 
                  (pair, direction, entry, fibo_50, tp, sl, rsi, body_size, wick_pct))
    conn.commit()
    return c.lastrowid

def update_trade_result(trade_id, result, exit_price=None):
    if exit_price:
        c.execute("UPDATE trades SET result = ?, exit_price = ? WHERE id = ?", (result, exit_price, trade_id))
    else:
        c.execute("UPDATE trades SET result = ? WHERE id = ?", (result, trade_id))
    conn.commit()

# ===== TELEGRAM API WRAPPERS =====
def send_telegram_msg(chat_id, text):
    if not chat_id: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, verify=False, timeout=10)
    except:
        pass

def send_signal_with_buttons(chat_id, pair, direction, price, sl, tp, fibo, rsi, body_pips, wick_pct, full_datetime="NOW"):
    trade_id = log_signal(pair, direction, price, fibo, tp, sl, rsi, body_pips, wick_pct, full_datetime if full_datetime != "NOW" else None)
    winrate, total_trades = get_winrate()
    
    msg = f"""
🚀 <b>MOMENTUM CANDLE DETECTED (#{trade_id})</b>
📅 <b>Waktu Candle</b>: <code>{full_datetime}</code>

* **Pair**: {pair} ({TV_EXCHANGE})
* **Arah**: <b>{direction}</b>
* **Entry (Close)**: {price:.2f}
* **Re-entry Fibo 50%**: <code>{fibo:.2f}</code>
* **Take Profit**: {tp:.2f}
* **Stop Loss**: {sl:.2f}

📊 <b>Body Size</b>: <code>{body_pips:.1f} Pips</code> (≥ 650)
📊 <b>Wick Ratio</b>: <code>{wick_pct:.1f}%</code> (≤ 30%)
📊 <b>RSI</b>: {rsi:.1f}
📈 <b>Winrate Database</b>: {winrate}% ({total_trades} trade)

🤖 <i>Auto-OHLC Engine aktif mengawasi TP/SL...</i>
    """
    
    keyboard = {
        "inline_keyboard": [
            [
                {"text": f"✅ WIN (Manual #{trade_id})", "callback_data": f"WIN_{trade_id}"},
                {"text": f"❌ LOSS (Manual #{trade_id})", "callback_data": f"LOSS_{trade_id}"}
            ]
        ]
    }
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML", "reply_markup": keyboard}, verify=False, timeout=10)
    except:
        pass

def answer_callback(callback_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery"
    try:
        requests.post(url, json={"callback_query_id": callback_id, "text": text}, verify=False, timeout=10)
    except:
        pass

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

# ===== AUTO-EVALUATION OHLC ENGINE =====
def evaluate_pending_trades(df):
    """Mengecek pergerakan High/Low dari candle terbaru terhadap trade PENDING"""
    c.execute("SELECT id, direction, entry_price, tp_price, sl_price FROM trades WHERE result='PENDING'")
    pending_trades = c.fetchall()

    if not pending_trades or df is None or df.empty:
        return

    latest_candle = df.iloc[-2]
    high_p = latest_candle['high']
    low_p = latest_candle['low']

    for trade in pending_trades:
        trade_id, direction, entry, tp, sl = trade
        
        if direction == "BUY":
            if high_p >= tp:
                update_trade_result(trade_id, "WIN", tp)
                wr, total = get_winrate()
                send_telegram_msg(DEFAULT_CHAT_ID, f"🎯 <b>AUTO-EVALUATION: WIN! (#{trade_id})</b>\nTrade BUY menyentuh TP ({tp:.2f}).\nWinrate Baru: <b>{wr}%</b> ({total} trade).")
            elif low_p <= sl:
                update_trade_result(trade_id, "LOSS", sl)
                wr, total = get_winrate()
                send_telegram_msg(DEFAULT_CHAT_ID, f"🛑 <b>AUTO-EVALUATION: LOSS (#{trade_id})</b>\nTrade BUY menyentuh SL ({sl:.2f}).\nWinrate Baru: <b>{wr}%</b> ({total} trade).")
                
        elif direction == "SELL":
            if low_p <= tp:
                update_trade_result(trade_id, "WIN", tp)
                wr, total = get_winrate()
                send_telegram_msg(DEFAULT_CHAT_ID, f"🎯 <b>AUTO-EVALUATION: WIN! (#{trade_id})</b>\nTrade SELL menyentuh TP ({tp:.2f}).\nWinrate Baru: <b>{wr}%</b> ({total} trade).")
            elif high_p >= sl:
                update_trade_result(trade_id, "LOSS", sl)
                wr, total = get_winrate()
                send_telegram_msg(DEFAULT_CHAT_ID, f"🛑 <b>AUTO-EVALUATION: LOSS (#{trade_id})</b>\nTrade SELL menyentuh SL ({sl:.2f}).\nWinrate Baru: <b>{wr}%</b> ({total} trade).")

# ===== TELEGRAM BOT POLL LOOP =====
def telegram_polling():
    global DEFAULT_CHAT_ID
    print("🤖 Telegram Bot Active...", flush=True)
    last_update_id = None
    
    while True:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
        try:
            res = requests.get(url, params={"offset": last_update_id, "timeout": 10}, verify=False)
            updates = res.json()
            
            if updates.get("result"):
                for item in updates["result"]:
                    last_update_id = item["update_id"] + 1
                    
                    if "message" in item:
                        chat_id = item["message"]["chat"]["id"]
                        DEFAULT_CHAT_ID = chat_id 
                        text = item["message"]["text"].lower().strip()
                        
                        if any(k in text for k in ["cek candle", "backtest", "scan", "cek momentum"]):
                            send_telegram_msg(chat_id, "📊 Bot trading sedang dalam mode monitoring. Fitur manual scan akan segera ditambahkan!")

                        elif any(k in text for k in ["harga", "cek xau", "running"]):
                            send_telegram_msg(chat_id, f"🪙 <b>HARGA XAUUSD (FOREXCOM)</b>\nRunning Price: <b>$2050.00</b> (Demo Mode)")

                        elif "analisis" in text or "probabilitas" in text or "analisa" in text:
                            report = get_probability_analysis()
                            send_telegram_msg(chat_id, report)

                        elif "winrate" in text or "/stats" in text:
                            wr, total = get_winrate()
                            send_telegram_msg(chat_id, f"📈 <b>STATISTIK SAAT INI</b>\nWinrate: <b>{wr}%</b>\nTotal Evaluasi: <b>{total} trade</b>")
                            
                        elif "reset" in text or "/reset" in text:
                            c.execute("DELETE FROM trades")
                            conn.commit()
                            send_telegram_msg(chat_id, "🗑️ <b>Database history berhasil dibersihkan!</b>")
                        
                        elif "start" in text or "/start" in text:
                            send_telegram_msg(chat_id, """
🚀 <b>WELCOME TO TRADING BOT!</b>

Fitur tersedia:
• /stats - Lihat winrate & statistik
• /analisis - Analisis probabilitas
• Kirim pesan dengan kata kunci:
  - "winrate" → Lihat statistik
  - "analisis" → Analisis data
  - "reset" → Hapus history

Bot sedang monitoring 24/7...
                            """)

                    elif "callback_query" in item:
                        cb = item["callback_query"]
                        cb_id = cb["id"]
                        data = cb["data"]
                        
                        try:
                            action, trade_id = data.split("_")
                            update_trade_result(int(trade_id), action)
                            
                            new_wr, total = get_winrate()
                            answer_callback(cb_id, f"Tercatat {action}!")
                            send_telegram_msg(cb["message"]["chat"]["id"], f"✅ Trade #{trade_id} diperbarui manual: <b>{action}</b>. Winrate: <b>{new_wr}%</b> ({total} trade).")
                        except:
                            pass
                        
        except Exception as e:
            print(f"Polling error: {e}", flush=True)
            time.sleep(2)
            
        time.sleep(1)

if __name__ == "__main__":
    print("🤖 Trading Bot Started!", flush=True)
    print(f"Token: {TELEGRAM_TOKEN[:10]}..." if TELEGRAM_TOKEN else "⚠️ No token set")
    print(f"Chat ID: {DEFAULT_CHAT_ID if DEFAULT_CHAT_ID else '⚠️ No chat ID set'}")
    
    telegram_polling()
