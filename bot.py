import sqlite3
import requests
import time
import urllib3
import re
import threading
import pandas as pd
import numpy as np
from datetime import datetime
from tvdatafeed import TvDatafeed, Interval

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ===== KONFIGURASI =====
TELEGRAM_TOKEN = "8463256728:AAHNe-wU4DcXnVmiYlnNp5k0dyDsNIdiVI8"
DEFAULT_CHAT_ID = "" 

# Pengaturan Symbol & Exchange TradingView (FOREXCOM)
TV_SYMBOL = "XAUUSD"
TV_EXCHANGE = "FOREXCOM"
TIMEFRAME = Interval.in_5_minute

# Inisialisasi TradingView Datafeed
tv = TvDatafeed()

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
    requests.post(url, data={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, verify=False)

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
    requests.post(url, json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML", "reply_markup": keyboard}, verify=False)

def answer_callback(callback_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery"
    requests.post(url, json={"callback_query_id": callback_id, "text": text}, verify=False)

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

    # Ambil baris candle yang baru ditutup
    latest_candle = df.iloc[-2]
    high_p = latest_candle['high']
    low_p = latest_candle['low']

    for trade in pending_trades:
        trade_id, direction, entry, tp, sl = trade
        
        if direction == "BUY":
            # Jika High menyentuh TP lebih dulu
            if high_p >= tp:
                update_trade_result(trade_id, "WIN", tp)
                wr, total = get_winrate()
                send_telegram_msg(DEFAULT_CHAT_ID, f"🎯 <b>AUTO-EVALUATION: WIN! (#{trade_id})</b>\nTrade BUY menyentuh TP ({tp:.2f}).\nWinrate Baru: <b>{wr}%</b> ({total} trade).")
            # Jika Low menyentuh SL
            elif low_p <= sl:
                update_trade_result(trade_id, "LOSS", sl)
                wr, total = get_winrate()
                send_telegram_msg(DEFAULT_CHAT_ID, f"🛑 <b>AUTO-EVALUATION: LOSS (#{trade_id})</b>\nTrade BUY menyentuh SL ({sl:.2f}).\nWinrate Baru: <b>{wr}%</b> ({total} trade).")
                
        elif direction == "SELL":
            # Jika Low menyentuh TP
            if low_p <= tp:
                update_trade_result(trade_id, "WIN", tp)
                wr, total = get_winrate()
                send_telegram_msg(DEFAULT_CHAT_ID, f"🎯 <b>AUTO-EVALUATION: WIN! (#{trade_id})</b>\nTrade SELL menyentuh TP ({tp:.2f}).\nWinrate Baru: <b>{wr}%</b> ({total} trade).")
            # Jika High menyentuh SL
            elif high_p >= sl:
                update_trade_result(trade_id, "LOSS", sl)
                wr, total = get_winrate()
                send_telegram_msg(DEFAULT_CHAT_ID, f"🛑 <b>AUTO-EVALUATION: LOSS (#{trade_id})</b>\nTrade SELL menyentuh SL ({sl:.2f}).\nWinrate Baru: <b>{wr}%</b> ({total} trade).")

# ===== REAL-TIME AUTO MONITORING ENGINE =====
def real_time_monitor():
    global last_pre_alert_time, last_closed_candle_time
    print("Real-time Monitor Running...")
    
    while True:
        try:
            if DEFAULT_CHAT_ID:
                df = tv.get_hist(symbol=TV_SYMBOL, exchange=TV_EXCHANGE, interval=TIMEFRAME, n_bars=20)
                
                if df is not None and not df.empty:
                    df['RSI'] = calculate_rsi(df['close'], 14)
                    
                    # Evaluation Engine: Cek Trade PENDING
                    evaluate_pending_trades(df)

                    # 1. CANDLE SEDANG BERJALAN (Running Candle)
                    running_row = df.iloc[-1]
                    running_time = df.index[-1]
                    
                    running_open = running_row['open']
                    running_close = running_row['close']
                    running_body = abs(running_close - running_open) * 100
                    
                    now_seconds = time.time()
                    seconds_into_candle = int(now_seconds) % 300
                    seconds_remaining = 300 - seconds_into_candle
                    
                    # Alert Pre-Close (20 detik sebelum close)
                    if seconds_remaining <= 20 and running_body >= 650:
                        if last_pre_alert_time != running_time:
                            last_pre_alert_time = running_time
                            direction = "BULLISH 🟢" if running_close > running_open else "BEARISH 🔴"
                            
                            alert_msg = f"""
⚠️ <b>PRE-CLOSE ALERT! ({TV_EXCHANGE})</b>
⏳ Candle M5 akan close dalam <b>{seconds_remaining} detik</b>!

* **Arah Sementara**: <b>{direction}</b>
* **Ukuran Body Running**: <code>{running_body:.1f} Pips</code> (≥ 650)
* **Harga Running**: {running_close:.2f}
                            """
                            send_telegram_msg(DEFAULT_CHAT_ID, alert_msg)

                    # 2. CANDLE TERAKHIR YANG SUDAH CLOSE
                    closed_row = df.iloc[-2]
                    closed_time = df.index[-2]
                    
                    if last_closed_candle_time != closed_time:
                        last_closed_candle_time = closed_time
                        
                        c_open, c_close = closed_row['open'], closed_row['close']
                        c_high, c_low = closed_row['high'], closed_row['low']
                        c_rsi = closed_row['RSI']
                        
                        c_range = c_high - c_low
                        c_body = abs(c_close - c_open)
                        
                        if c_range > 0:
                            c_body_pips = c_body * 100
                            c_wick_pct = ((c_range - c_body) / c_range) * 100
                            
                            if c_body_pips >= 650 and c_wick_pct <= 30.0:
                                time_str = closed_time.strftime('%d %b - %H:%M WIB')
                                
                                if c_close > c_open:
                                    direction = "BUY"
                                    fibo_50 = c_low + (c_range * 0.5)
                                    sl = c_low - 1.0
                                    tp = c_close + 4.0
                                else:
                                    direction = "SELL"
                                    fibo_50 = c_high - (c_range * 0.5)
                                    sl = c_high + 1.0
                                    tp = c_close - 4.0

                                send_signal_with_buttons(
                                    DEFAULT_CHAT_ID, "XAUUSD", direction, c_close, sl, tp,
                                    fibo_50, c_rsi, c_body_pips, c_wick_pct, time_str
                                )

        except Exception as e:
            print(f"Error pada monitor thread: {e}")
            
        time.sleep(3)

# ===== MANUAL SCAN FUNCTION =====
def scan_past_candles_tv(chat_id, limit=60):
    send_telegram_msg(chat_id, f"🔍 <b>[{TV_EXCHANGE}] Memindai {limit} candle M5...</b>")
    try:
        df = tv.get_hist(symbol=TV_SYMBOL, exchange=TV_EXCHANGE, interval=TIMEFRAME, n_bars=limit+15)
        
        if df is None or df.empty:
            send_telegram_msg(chat_id, f"❌ Gagal mengambil data dari TradingView ({TV_EXCHANGE}).")
            return

        df['RSI'] = calculate_rsi(df['close'], 14)
        df['Body'] = (df['close'] - df['open']).abs()
        df['Total_Range'] = df['high'] - df['low']

        recent_df = df.iloc[-(limit+1):-1]
        found_count = 0

        for idx, row in recent_df.iterrows():
            open_p, close_p = row['open'], row['close']
            high_p, low_p = row['high'], row['low']
            rsi_val, body_val, total_range = row['RSI'], row['Body'], row['Total_Range']
            
            if total_range == 0:
                continue

            body_pips = body_val * 100
            total_wick_val = total_range - body_val
            wick_percentage = (total_wick_val / total_range) * 100

            full_datetime = idx.strftime('%d %b - %H:%M WIB')

            if body_pips >= 650 and wick_percentage <= 30.0:
                if close_p > open_p:
                    direction = "BUY"
                    fibo_50 = low_p + (total_range * 0.5)
                    sl = low_p - 1.0
                    tp = close_p + 4.0
                else:
                    direction = "SELL"
                    fibo_50 = high_p - (total_range * 0.5)
                    sl = high_p + 1.0
                    tp = close_p - 4.0

                send_signal_with_buttons(
                    chat_id, "XAUUSD", direction, close_p, sl, tp, 
                    fibo_50, rsi_val, body_pips, wick_percentage, full_datetime
                )
                found_count += 1
                time.sleep(0.5)

        if found_count == 0:
            send_telegram_msg(chat_id, f"ℹ️ Dari {limit} candle FOREXCOM, tidak ada yang memenuhi syarat Body ≥ 650 pips & Wick ≤ 30%.")
        else:
            send_telegram_msg(chat_id, f"✅ <b>Selesai!</b> Ditemukan {found_count} momentum candle dari FOREXCOM.")

    except Exception as e:
        send_telegram_msg(chat_id, f"❌ Error TradingView: {e}")

# ===== TELEGRAM BOT POLL LOOP =====
def telegram_polling():
    global DEFAULT_CHAT_ID
    print("Telegram Bot Active...")
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
                            numbers = re.findall(r'\d+', text)
                            limit_val = int(numbers[0]) if numbers else 60
                            scan_past_candles_tv(chat_id, limit=limit_val)

                        elif any(k in text for k in ["harga", "cek xau", "running"]):
                            df_live = tv.get_hist(symbol=TV_SYMBOL, exchange=TV_EXCHANGE, interval=Interval.in_1_minute, n_bars=1)
                            if df_live is not None and not df_live.empty:
                                p = df_live['close'].iloc[-1]
                                send_telegram_msg(chat_id, f"🪙 <b>HARGA XAUUSD (FOREXCOM)</b>\nRunning Price: <b>${p:.2f}</b>")
                            else:
                                send_telegram_msg(chat_id, "⚠️ Gagal mengambil harga dari FOREXCOM.")

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

                    elif "callback_query" in item:
                        cb = item["callback_query"]
                        cb_id = cb["id"]
                        data = cb["data"]
                        
                        action, trade_id = data.split("_")
                        update_trade_result(int(trade_id), action)
                        
                        new_wr, total = get_winrate()
                        answer_callback(cb_id, f"Tercatat {action}!")
                        send_telegram_msg(cb["message"]["chat"]["id"], f"✅ Trade #{trade_id} diperbarui manual: <b>{action}</b>. Winrate: <b>{new_wr}%</b> ({total} trade).")
                        
        except Exception:
            time.sleep(2)
            
        time.sleep(1)

if __name__ == "__main__":
    monitor_thread = threading.Thread(target=real_time_monitor, daemon=True)
    monitor_thread.start()
    
    telegram_polling()
