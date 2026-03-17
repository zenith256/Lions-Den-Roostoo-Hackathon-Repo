import requests
import time
import hmac
import hashlib
import numpy as np

# --- API Configuration ---
BASE_URL = "https://mock-api.roostoo.com"
API_KEY = "4y6EVbS2YU5rsfJEKfUIJaCtjDLgqPsZL3qF5soEo5UlpYdMgrDAApCuMrBz2V42"  
SECRET_KEY = "fZ0wXe6I31cblu5keg24Hq5guCf0wvaUUOtfbeFsyW23aBflffpt23aChFltDQzc" 


# ------------------------------
# Utility Functions
# ------------------------------

def _get_timestamp():
    """Return a 13-digit millisecond timestamp as string."""
    return str(int(time.time() * 1000))


def _get_signed_headers(payload: dict = {}):
    """
    Generate signed headers and totalParams for RCL_TopLevelCheck endpoints.
    """
    payload['timestamp'] = _get_timestamp()
    sorted_keys = sorted(payload.keys())
    total_params = "&".join(f"{k}={payload[k]}" for k in sorted_keys)

    signature = hmac.new(
        SECRET_KEY.encode('utf-8'),
        total_params.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    headers = {
        'RST-API-KEY': API_KEY,
        'MSG-SIGNATURE': signature
    }

    return headers, payload, total_params


# ------------------------------
# Public Endpoints
# ------------------------------

def check_server_time():
    """Check API server time."""
    url = f"{BASE_URL}/v3/serverTime"
    try:
        res = requests.get(url)
        res.raise_for_status()
        return res.json()
    except requests.exceptions.RequestException as e:
        print(f"Error checking server time: {e}")
        return None


def get_exchange_info():
    """Get exchange trading pairs and info."""
    url = f"{BASE_URL}/v3/exchangeInfo"
    try:
        res = requests.get(url)
        res.raise_for_status()
        return res.json()
    except requests.exceptions.RequestException as e:
        print(f"Error getting exchange info: {e}")
        return None


def get_ticker(pair=None):
    """Get ticker for one or all pairs."""
    url = f"{BASE_URL}/v3/ticker"
    params = {'timestamp': _get_timestamp()}
    if pair:
        params['pair'] = pair
    try:
        res = requests.get(url, params=params)
        res.raise_for_status()
        return res.json()
    except requests.exceptions.RequestException as e:
        print(f"Error getting ticker: {e}")
        return None


# ------------------------------
# Signed Endpoints
# ------------------------------

def get_balance():
    """Get wallet balances (RCL_TopLevelCheck)."""
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


def get_pending_count():
    """Get total pending order count."""
    url = f"{BASE_URL}/v3/pending_count"
    headers, payload, _ = _get_signed_headers({})
    try:
        res = requests.get(url, headers=headers, params=payload)
        res.raise_for_status()
        return res.json()
    except requests.exceptions.RequestException as e:
        print(f"Error getting pending count: {e}")
        print(f"Response text: {e.response.text if e.response else 'N/A'}")
        return None


def place_order(pair_or_coin, side, quantity, price=None, order_type=None):
    """
    Place a LIMIT or MARKET order.
    """
    url = f"{BASE_URL}/v3/place_order"
    pair = f"{pair_or_coin}/USD" if "/" not in pair_or_coin else pair_or_coin

    if order_type is None:
        order_type = "LIMIT" if price is not None else "MARKET"

    if order_type == 'LIMIT' and price is None:
        print("Error: LIMIT orders require 'price'.")
        return None

    payload = {
        'pair': pair,
        'side': side.upper(),
        'type': order_type.upper(),
        'quantity': str(quantity)
    }
    if order_type == 'LIMIT':
        payload['price'] = str(price)

    headers, _, total_params = _get_signed_headers(payload)
    headers['Content-Type'] = 'application/x-www-form-urlencoded'

    try:
        res = requests.post(url, headers=headers, data=total_params)
        res.raise_for_status()
        return res.json()
    except requests.exceptions.RequestException as e:
        print(f"Error placing order: {e}")
        print(f"Response text: {e.response.text if e.response else 'N/A'}")
        return None


def query_order(order_id=None, pair=None, pending_only=None):
    """Query order history or pending orders."""
    url = f"{BASE_URL}/v3/query_order"
    payload = {}
    if order_id:
        payload['order_id'] = str(order_id)
    elif pair:
        payload['pair'] = pair
        if pending_only is not None:
            payload['pending_only'] = 'TRUE' if pending_only else 'FALSE'

    headers, _, total_params = _get_signed_headers(payload)
    headers['Content-Type'] = 'application/x-www-form-urlencoded'

    try:
        res = requests.post(url, headers=headers, data=total_params)
        res.raise_for_status()
        return res.json()
    except requests.exceptions.RequestException as e:
        print(f"Error querying order: {e}")
        print(f"Response text: {e.response.text if e.response else 'N/A'}")
        return None


def cancel_order(order_id=None, pair=None):
    """Cancel specific or all pending orders."""
    url = f"{BASE_URL}/v3/cancel_order"
    payload = {}
    if order_id:
        payload['order_id'] = str(order_id)
    elif pair:
        payload['pair'] = pair

    headers, _, total_params = _get_signed_headers(payload)
    headers['Content-Type'] = 'application/x-www-form-urlencoded'

    try:
        res = requests.post(url, headers=headers, data=total_params)
        res.raise_for_status()
        return res.json()
    except requests.exceptions.RequestException as e:
        print(f"Error canceling order: {e}")
        print(f"Response text: {e.response.text if e.response else 'N/A'}")
        return None



# --- Strategy Parameters ---
SYMBOL = "SOL"
PAIR = f"{SYMBOL}/USD"
WINDOW = 60
Z_THRESH = 2.5
QUANTITY = 1  

# This will store our price history locally
price_history = []

def run_zscore_bot():
    global price_history
    print(f"Starting Z-Score Bot for {PAIR}...")

    while True:
        try:
            # 1. Fetch current data
            ticker = get_ticker(PAIR)
            if not ticker or "Data" not in ticker:
                time.sleep(5)
                continue

            current_price = float(ticker["Data"][PAIR]["LastPrice"])
            price_history.append(current_price)

            # Keep only the last 'WINDOW' items
            if len(price_history) > WINDOW:
                price_history.pop(0)

            # 2. Check if we have enough data to calculate Z-Score
            if len(price_history) < WINDOW:
              remaining = WINDOW - len(price_history)
              print(f"Collecting data... {len(price_history)}/{WINDOW} (Est. {remaining * 10 / 60:.1f} mins left)")
              time.sleep(10)
              continue

            # 3. Calculate Z-Score
            mu = np.mean(price_history)
            sigma = np.std(price_history)
            
            if sigma == 0:
                continue
                
            z_score = (current_price - mu) / sigma
            print(f"Price: {current_price:.2f} | Z-Score: {z_score:.2f}")

            # 4. Execution Logic
            balance = get_balance()
            asset_balance = next((float(b['Free']) for b in balance['Data'] if b['Asset'] == SYMBOL), 0.0)

            # Entry: Z-Score indicates oversold
            if z_score < -Z_THRESH and asset_balance < 0.1: # '0.1' is a safety margin
                print("!!! BUY SIGNAL TRIGGERED !!!")
                place_order(SYMBOL, "BUY", QUANTITY)

            # Exit: Price returned to mean (Z-Score >= 0)
            elif z_score >= 0 and asset_balance >= QUANTITY:
                print("!!! EXIT SIGNAL TRIGGERED (Mean Reversion) !!!")
                place_order(SYMBOL, "SELL", QUANTITY)

        except Exception as e:
            print(f"Strategy Error: {e}")

        # 5. Frequency: Roostoo mock API usually updates every few seconds
        time.sleep(10)


# ------------------------------
# Quick Demo Section
# ------------------------------
if __name__ == "__main__":
  run_zscore_bot()
