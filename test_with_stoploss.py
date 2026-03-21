import requests
import time
import hmac
import hashlib
import numpy as np
import os
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

# --- API Configuration ---
BASE_URL = "https://mock-api.roostoo.com"
API_KEY = os.getenv("ROOSTOO_API_KEY")
SECRET_KEY = os.getenv("ROOSTOO_SECRET_KEY")

# ------------------------------
# Utility Functions & Endpoints
# ------------------------------
# (Keeping all your API connection functions exactly the same to ensure it works)

def _get_timestamp():
    return str(int(time.time() * 1000))

def _get_signed_headers(payload: dict = {}):
    payload["timestamp"] = _get_timestamp()
    sorted_keys = sorted(payload.keys())
    total_params = "&".join(f"{k}={payload[k]}" for k in sorted_keys)
    signature = hmac.new(
        SECRET_KEY.encode("utf-8"), total_params.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    headers = {"RST-API-KEY": API_KEY, "MSG-SIGNATURE": signature}
    return headers, payload, total_params

def check_server_time():
    url = f"{BASE_URL}/v3/serverTime"
    try:
        res = requests.get(url)
        res.raise_for_status()
        return res.json()
    except requests.exceptions.RequestException as e:
        return None

def get_ticker(pair=None):
    url = f"{BASE_URL}/v3/ticker"
    params = {"timestamp": _get_timestamp()}
    if pair:
        params["pair"] = pair
    try:
        res = requests.get(url, params=params)
        res.raise_for_status()
        return res.json()
    except requests.exceptions.RequestException as e:
        return None

def get_balance():
    url = f"{BASE_URL}/v3/balance"
    headers, payload, _ = _get_signed_headers({})
    try:
        res = requests.get(url, headers=headers, params=payload)
        res.raise_for_status()
        return res.json()
    except requests.exceptions.RequestException as e:
        return None

def place_order(pair_or_coin, side, quantity, price=None, order_type=None):
    url = f"{BASE_URL}/v3/place_order"
    pair = f"{pair_or_coin}/USD" if "/" not in pair_or_coin else pair_or_coin
    if order_type is None:
        order_type = "LIMIT" if price is not None else "MARKET"
    payload = {
        "pair": pair,
        "side": side.upper(),
        "type": order_type.upper(),
        "quantity": str(quantity),
    }
    if order_type == "LIMIT":
        payload["price"] = str(price)
    headers, _, total_params = _get_signed_headers(payload)
    headers["Content-Type"] = "application/x-www-form-urlencoded"
    try:
        res = requests.post(url, headers=headers, data=total_params)
        res.raise_for_status()
        return res.json()
    except requests.exceptions.RequestException as e:
        return None

# ------------------------------
# Strategy Parameters & State
# ------------------------------
SYMBOL = "SOL"
PAIR = f"{SYMBOL}/USD"
WINDOW = 10
Z_THRESH = 2.5
PORTION = 0.001  # Increased slightly to ensure orders clear $10 minimums

# --- NEW: Stop Loss Parameters ---
TRAILING_STOP_PCT = 0.015  # 1.5% Trailing Stop

# --- NEW: Bot State Memory ---
price_history = []
active_trade = False
entry_price = 0.0
highest_price_since_entry = 0.0


def run_zscore_bot():
    global price_history, active_trade, entry_price, highest_price_since_entry
    print(f"Starting Robust Z-Score Bot for {PAIR}...")
    print(f"Protection: {TRAILING_STOP_PCT*100}% Trailing Stop Loss Active")

    while True:
        try:
            # 1. Fetch current data
            ticker = get_ticker(PAIR)
            if not ticker or "Data" not in ticker:
                time.sleep(10)
                continue

            current_price = float(ticker["Data"][PAIR]["LastPrice"])
            price_history.append(current_price)

            if len(price_history) > WINDOW:
                price_history.pop(0)

            if len(price_history) < WINDOW:
                remaining = WINDOW - len(price_history)
                print(f"Collecting data... {len(price_history)}/{WINDOW} (Est. {remaining * 10 / 60:.1f} mins left)")
                time.sleep(10)
                continue

            # 2. Calculate Statistics
            mu = np.mean(price_history)
            sigma = np.std(price_history)
            if sigma == 0:
                continue
            z_score = (current_price - mu) / sigma

            # 3. Check Wallet
            balance = get_balance()
            if not balance or not balance.get("Success"):
                time.sleep(5)
                continue
            
            wallet = balance.get("SpotWallet", {})
            usd_balance = float(wallet.get("USD", {}).get("Free", 0))
            asset_balance = float(wallet.get(SYMBOL, {}).get("Free", 0))

            # --- STATE MANAGEMENT ---
            # If we hold the asset, keep track of the highest price it reaches
            if asset_balance > 0.01:
                if current_price > highest_price_since_entry:
                    highest_price_since_entry = current_price
            else:
                # If we don't hold the asset, reset our trade memory
                active_trade = False
                highest_price_since_entry = 0.0

            # --- DISPLAY DASHBOARD ---
            if asset_balance > 0.01:
                stop_price = highest_price_since_entry * (1 - TRAILING_STOP_PCT)
                print(f"Hold: {asset_balance:.2f} {SYMBOL} | Price: ${current_price:.2f} | Z: {z_score:.2f} | Stop Loss @ ${stop_price:.2f}")
            else:
                print(f"Wallet: {usd_balance:.2f} USD | Price: ${current_price:.2f} | Z-Score: {z_score:.2f}")


            # --- EXECUTION LOGIC ---
            
            # ENTRY (BUY)
            if z_score < -Z_THRESH and asset_balance < 0.01:
                usd_to_spend = usd_balance * PORTION
                if usd_to_spend > 10:
                    buy_quantity = round((usd_to_spend * 0.999) / current_price, 4)
                    print(f"!!! BUY SIGNAL: Acquiring {buy_quantity} {SYMBOL} !!!")
                    
                    response = place_order(SYMBOL, "BUY", buy_quantity)
                    if response and response.get("Success"):
                        # Save state memory for the Stop Loss
                        active_trade = True
                        entry_price = current_price
                        highest_price_since_entry = current_price
                        log_trade("BUY", SYMBOL, buy_quantity, current_price, "Entry")

            # EXIT (SELL) - Two possible triggers now
            elif asset_balance > 0.01:
                
                # Trigger 1: Take Profit (Mean Reversion successful)
                if z_score >= 0:
                    print(f"!!! TAKE PROFIT: Price returned to mean. Selling all {SYMBOL} !!!")
                    response = place_order(SYMBOL, "SELL", asset_balance)
                    if response and response.get("Success"):
                        log_trade("SELL", SYMBOL, asset_balance, current_price, "Take_Profit")
                
                # Trigger 2: Trailing Stop Loss (Protection)
                elif current_price < (highest_price_since_entry * (1 - TRAILING_STOP_PCT)):
                    print(f"!!! TRAILING STOP HIT: Price dropped {(TRAILING_STOP_PCT*100)}% from peak. Cutting losses !!!")
                    response = place_order(SYMBOL, "SELL", asset_balance)
                    if response and response.get("Success"):
                        log_trade("SELL", SYMBOL, asset_balance, current_price, "Stop_Loss")

        except Exception as e:
            print(f"Strategy Error: {e}")

        time.sleep(10)

def log_trade(side, symbol, quantity, price, reason=""):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {side.upper()} {quantity} {symbol} @ ${price:.2f} | Reason: {reason}\n"
    with open("trade_history_v2.txt", "a") as f:
        f.write(log_entry)
    print(f"Logged: {log_entry.strip()}")

if __name__ == "__main__":
    run_zscore_bot()