import requests
import time
import hmac
import hashlib
import pandas as pd
import numpy as np
import warnings
import os
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()
warnings.filterwarnings("ignore")

# --- API Configuration ---
BASE_URL = "https://mock-api.roostoo.com"
ROOSTOO_API_KEY = os.getenv("ROOSTOO_API_KEY")
ROOSTOO_SECRET_KEY = os.getenv("ROOSTOO_SECRET_KEY")

TARGET_PAIR = "TRX/USD"

# --- Telegram Configuration ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = "982514963"

# --- Allocation Constants ---
WINDOW = 2
REGIME_A_WEIGHT = 0.98
REGIME_B_WEIGHT = 0.01
LOOP_INTERVAL = 900

# Regime B (Trend Follow) Parameters
TP_PCT = 1.0060
SL_PCT = 0.9850
MAX_HOLD = 24


# ------------------------------
# API Core Functions
# ------------------------------
def _get_timestamp():
    return str(int(time.time() * 1000))


def _get_signed_headers(payload: dict = {}):
    payload["timestamp"] = _get_timestamp()
    sorted_keys = sorted(payload.keys())
    total_params = "&".join(f"{k}={payload[k]}" for k in sorted_keys)

    signature = hmac.new(
        ROOSTOO_SECRET_KEY.encode("utf-8"), total_params.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    headers = {"RST-API-KEY": ROOSTOO_API_KEY, "MSG-SIGNATURE": signature}

    return headers, payload, total_params



def send_tele(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(
            url, data={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=10
        )
    except:
        pass

def get_balance():
    url = f"{BASE_URL}/v3/balance"
    headers, payload, _ = _get_signed_headers({})
    try:
        res = requests.get(url, headers=headers, params=payload)
        res.raise_for_status()
        return res.json()
    except requests.exceptions.RequestException as e:
        print(f"Error getting balance: {e}")
        print(f"Response text: {e.response.text if e.response else 'N/A'}")
        return None


def get_ticker(pair):
    url = f"{BASE_URL}/v3/ticker"
    params = {"timestamp": _get_timestamp()}
    if pair:
        params["pair"] = pair
    try:
        res = requests.get(url, params=params, timeout=10)
        if res.status_code != 200:
            print(f"Server Error: {res.status_code}")
            return None
        return res.json()
    except Exception as e:
        print(f"Request failed: {e}")
        return None


def place_order(pair, side, quantity, price=None, order_type="LIMIT"):
    payload = {
        "pair": pair,
        "side": side.upper(),
        "type": order_type.upper(),
        "quantity": str(round(quantity, 4)),
    }
    if order_type.upper() == "LIMIT":
        payload["price"] = str(round(price, 6))
    headers, _, total_params = _get_signed_headers(payload)
    headers["Content-Type"] = "application/x-www-form-urlencoded"
    return requests.post(
        f"{BASE_URL}/v3/place_order", headers=headers, data=total_params
    ).json()


def cancel_all_orders(pair):
    headers, _, total_params = _get_signed_headers({"pair": pair})
    headers["Content-Type"] = "application/x-www-form-urlencoded"
    return requests.post(
        f"{BASE_URL}/v3/cancel_order", headers=headers, data=total_params
    ).json()


# ------------------------------
# Execution Logic
# ------------------------------
def run_trading_bot():
    print(f"System Online: {TARGET_PAIR} | Dual-Regime Active")
    send_tele(f"Bot Active: Monitoring {TARGET_PAIR}")
    price_history = []
    regime_b_entry = 0
    regime_b_bars = 0

    while True:
        try:
            ticker = get_ticker(TARGET_PAIR)
            
            if not ticker or "Data" not in ticker:
                print("Invalid API response. Sleeping 10s...")
                time.sleep(10)
                continue 
                
            current_p = float(ticker["Data"][TARGET_PAIR]["LastPrice"])
            
            price_history.append(current_p) 

            if len(price_history) < WINDOW:
                print(f"Warm-up: {len(price_history)}/{WINDOW} | Price: {current_p}")
                time.sleep(2) # You can change this to 1 or 2 to warm up faster!
                continue

            df = pd.Series(price_history)
            # 1. Indicator Calculations
            lp = np.log(df)
            v1 = (lp - lp.shift(2)).rolling(160).var().replace(0, 1e-8)
            v2 = (lp - lp.shift(20)).rolling(160).var().replace(0, 1e-8)
            hurst = (0.5 * np.log(v2 / v1) / np.log(10)).iloc[-1]

            s_fast, s_slow = (
                df.rolling(5).mean().iloc[-1],
                df.rolling(20).mean().iloc[-1],
            )
            s_fast_p, s_slow_p = (
                df.rolling(5).mean().iloc[-2],
                df.rolling(20).mean().iloc[-2],
            )
            s_trend = df.rolling(200).mean().iloc[-1]

            # 2. Account State
            bal = get_balance()

            usd_data = bal.get("USD", {})
            usd_total = float(usd_data.get("Free", 0))
            
            asset_name = TARGET_PAIR.split("/")[0] 
            asset_data = bal.get(asset_name, {})
            asset_qty = float(asset_data.get("Free", 0))
            
            pos_val = asset_qty * current_p

            cancel_all_orders(TARGET_PAIR)
            time.sleep(1)

            # --- REGIME A: Mean Reversion  ---
            if hurst < 0.45:
                g_levels = [
                    df.rolling(160).quantile(q).iloc[-1]
                    for q in np.linspace(0.03, 0.001, 6)
                ]
                exit_lvl = df.rolling(160).quantile(0.70).iloc[-1]

                if pos_val < (usd_total * 0.1):
                    order_val = (usd_total * REGIME_A_WEIGHT) / 6
                    for lvl in g_levels:
                        place_order(TARGET_PAIR, "BUY", order_val / lvl, price=lvl)
                        time.sleep(0.5)
                elif pos_val > (usd_total * 0.5):
                    if current_p >= exit_lvl:
                        place_order(TARGET_PAIR, "SELL", asset_qty, order_type="MARKET")
                    else:
                        place_order(TARGET_PAIR, "SELL", asset_qty, price=exit_lvl)

            # --- REGIME B: Trend Follow  ---
            if 0.5 < pos_val < (usd_total * 0.1):  # Managing existing small trade
                regime_b_bars += 1
                if (
                    current_p >= (regime_b_entry * TP_PCT)
                    or current_p <= (regime_b_entry * SL_PCT)
                    or regime_b_bars >= MAX_HOLD
                ):
                    place_order(TARGET_PAIR, "SELL", asset_qty, order_type="MARKET")
                    send_tele(f"Regime B Exit: {current_p}")
                    regime_b_bars = 0

            elif pos_val < 2.0:  # Looking for new entry
                if current_p > s_trend:
                    if s_fast > s_slow and s_fast_p <= s_slow_p:
                        b_qty = (usd_total * REGIME_B_WEIGHT) / current_p
                        place_order(TARGET_PAIR, "BUY", b_qty, order_type="MARKET")
                        regime_b_entry, regime_b_bars = current_p, 0
                        send_tele(f"Regime B Entry: {current_p}")

            print(f"P: {current_p} | H: {hurst:.2f} | USD: {usd_total:.2f}")
            if len(price_history) > 1000:
                price_history.pop(0)
            time.sleep(LOOP_INTERVAL)

        except Exception as e:
            print(f"Error: {e}")
            time.sleep(60)


if __name__ == "__main__":
    run_trading_bot()
