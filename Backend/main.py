import os
import json
import asyncio
import logging
import time
import math
import hmac
import hashlib
import base64
from collections import deque
from datetime import datetime, timezone
import aiohttp
import numpy as np
import pandas as pd
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("GridBot")

HEADERS = {"User-Agent": "AlgoBot/1.0"}
STATE_FILE = "portfolio.json"

# Coinbase Advanced Trade API credentials
CB_API_KEY_NAME = os.getenv("COINBASE_API_KEY_NAME", "")
CB_PRIVATE_KEY  = os.getenv("COINBASE_PRIVATE_KEY", "").replace("\\n", "\n")
CB_REST_URL     = "https://api.coinbase.com"

# ── Coinbase Advanced Trade API Auth ─────────────────────────────────────────

def _cb_jwt_token(method: str, path: str) -> str:
    """Generate a short-lived JWT for Coinbase Advanced Trade API."""
    import jwt as pyjwt  # pip install PyJWT cryptography
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    private_key = load_pem_private_key(CB_PRIVATE_KEY.encode(), password=None)
    now = int(time.time())
    payload = {
        "sub": CB_API_KEY_NAME,
        "iss": "coinbase-cloud",
        "nbf": now,
        "exp": now + 120,
        "uri": f"{method} api.coinbase.com{path}",
    }
    token = pyjwt.encode(payload, private_key, algorithm="ES256",
                         headers={"kid": CB_API_KEY_NAME, "nonce": str(now)})
    return token


async def cb_get_account() -> dict:
    """Fetch real BTC/USD balances from Coinbase."""
    path = "/api/v3/brokerage/accounts"
    token = _cb_jwt_token("GET", path)
    async with aiohttp.ClientSession() as session:
        async with session.get(
            CB_REST_URL + path,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        ) as r:
            data = await r.json()
            accounts = data.get("accounts", [])
            result = {"BTC": 0.0, "USD": 0.0}
            for a in accounts:
                currency = a.get("currency", "")
                balance = float(a.get("available_balance", {}).get("value", 0))
                if currency == "BTC":
                    result["BTC"] = balance
                elif currency == "USD":
                    result["USD"] = balance
            return result


async def cb_place_order(action: str, base_size: float = None, quote_size: float = None) -> dict:
    """
    Place a market order on Coinbase.
    action: 'BUY' | 'SELL'
    quote_size: USD amount for BUY (market buy by spend)
    base_size: BTC amount for SELL (market sell by quantity)
    Returns order dict or raises.
    """
    import uuid
    path = "/api/v3/brokerage/orders"
    token = _cb_jwt_token("POST", path)

    order_config = {}
    if action == "BUY" and quote_size:
        order_config = {"market_market_ioc": {"quote_size": f"{quote_size:.2f}"}}
    elif action == "SELL" and base_size:
        order_config = {"market_market_ioc": {"base_size": f"{base_size:.8f}"}}
    else:
        raise ValueError(f"Invalid order params: {action} base={base_size} quote={quote_size}")

    body = {
        "client_order_id": str(uuid.uuid4()),
        "product_id": "BTC-USD",
        "side": action,
        "order_configuration": order_config,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            CB_REST_URL + path,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body,
        ) as r:
            data = await r.json()
            if not data.get("success"):
                raise Exception(f"Order failed: {data}")
            return data.get("order", data.get("success_response", {}))


async def cb_get_order(order_id: str) -> dict:
    """Fetch order status from Coinbase."""
    path = f"/api/v3/brokerage/orders/historical/{order_id}"
    token = _cb_jwt_token("GET", path)
    async with aiohttp.ClientSession() as session:
        async with session.get(
            CB_REST_URL + path,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        ) as r:
            data = await r.json()
            return data.get("order", {})


def check_daily_loss_limit() -> bool:
    """Returns True if daily loss limit is hit (block trading)."""
    if not state["settings"].get("live_trading"):
        return False
    tracker = state["daily_loss_tracker"]
    today = datetime.now().strftime("%Y-%m-%d")
    if tracker["date"] != today:
        tracker["date"] = today
        tracker["start_value"] = state["portfolio"]["total_value"]
        tracker["loss_today"] = 0.0
    tracker["loss_today"] = tracker["start_value"] - state["portfolio"]["total_value"]
    limit = state["settings"].get("daily_loss_limit_usd", 20.0)
    if tracker["loss_today"] >= limit:
        logger.warning(f"🛑 Daily loss limit hit: ${tracker['loss_today']:.2f} >= ${limit}")
        return True
    return False


# ── End Coinbase API ──────────────────────────────────────────────────────────


state = {
    "settings": {
        "strategy": "ADAPTIVE_GRID",     # ADAPTIVE_GRID | STANDARD_GRID
        "grid_upper": 0.0,               # 0 = auto-compute from ATR
        "grid_lower": 0.0,               # 0 = auto-compute from ATR
        "grid_levels": 30,               # Number of grid lines
        "trade_size_usd": 10.00,         # Base trade size per grid cross
        "auto_range": True,              # Auto-calculate grid bounds from ATR
        "atr_multiplier": 3.0,           # How many ATRs wide the grid is
        "trend_filter": True,            # Only buy in uptrend, sell in downtrend
        "volatility_scaling": True,      # Scale trade size with volatility
        "max_trade_size_usd": 30.00,     # Cap on volatility-scaled trade size
        "cooldown_seconds": 30,          # Min seconds between trades on same level
        "drawdown_limit_pct": 15.0,      # Max % drawdown before circuit breaker
        "trailing_take_profit": True,    # Use trailing TP on positions
        "trailing_tp_pct": 1.5,          # Trailing TP activation %
        "profit_lock_pct": 0.5,          # Lock in profit when trailing
        "rebalance_interval": 300,       # Seconds between grid rebalance checks
        "max_open_positions": 10,        # Max grid levels with open buys
        "fee_rate": 0.006,               # Coinbase taker fee (0.6%)
        "slippage_rate": 0.0005,         # Simulated slippage (0.05%)
        "volume_filter": True,           # Require above-average volume for trades
        "mtf_trend_filter": True,        # Use 5m candles for multi-timeframe confirmation
        "live_trading": False,           # LIVE MODE — places real orders on Coinbase
        "live_order_type": "market",     # market | limit
        "daily_loss_limit_usd": 20.0,   # Hard stop: max USD loss per day in live mode
    },
    "grid_state": {
        "initialized": False,
        "levels": [],
        "current_index": -1,
        "filled_levels": {},             # {level_idx: {"qty": x, "entry_price": y, "time": t}}
        "last_trade_time": {},           # {level_idx: timestamp} for cooldown
        "last_rebalance": 0,
        "range_high": 0.0,
        "range_low": 0.0,
    },
    "prices": {"BTC/USD": 0.0},
    "history": {"BTC/USD": []},          # 1-min closes
    "volume_history": {"BTC/USD": []},   # 1-min volumes (aligned with history)
    "history_5m": {"BTC/USD": []},       # 5-min candles for multi-timeframe
    "volume_5m": {"BTC/USD": []},        # 5-min volumes
    "indicators": {
        "BTC/USD": {
            "rsi": 50.0,
            "bb_upper": 0.0, "bb_mid": 0.0, "bb_lower": 0.0,
            "macd": 0.0, "macd_signal": 0.0, "macd_histogram": 0.0,
            "volatility": 0.0,
            "atr": 0.0,
            "ema_fast": 0.0, "ema_slow": 0.0,
            "trend": "NEUTRAL",          # BULLISH | BEARISH | NEUTRAL (1m)
            "trend_strength": 0.0,       # 0.0 to 1.0
            "trend_5m": "NEUTRAL",       # 5m timeframe trend
            "trend_strength_5m": 0.0,
            "mtf_agreement": False,      # True when 1m and 5m trend agree
            "volume_sma": 0.0,
            "volume_ratio": 1.0,         # Current volume / SMA (>1 = above average)
            "stoch_rsi": 50.0,
        }
    },
    "macro": {"BTC/USD": {}},
    "logs": [],
    "stats": {
        "total_trades": 0,
        "winning_trades": 0,
        "losing_trades": 0,
        "win_rate": 0.0,
        "avg_win": 0.0,
        "avg_loss": 0.0,
        "largest_win": 0.0,
        "largest_loss": 0.0,
        "profit_factor": 0.0,
        "max_drawdown": 0.0,
        "peak_value": 0.0,
        "session_start": "",
        "total_volume": 0.0,
    },
    "portfolio": {
        "initial_balance": 200.00,
        "cash": 200.00,
        "holdings": {"BTC/USD": 0.0},
        "cost_basis": {"BTC/USD": 0.0},
        "total_value": 200.00,
        "total_profit": 0.00,
        "total_fees": 0.00,
        "realized_pnl": 0.00,
        "unrealized_pnl": 0.00,
    },
    "circuit_breaker": {
        "active": False,
        "triggered_at": 0,
        "cooldown_until": 0,
        "reason": "",
    },
    "last_history_update": 0,
    "last_5m_update": 0,
    "last_volume_update": 0,
    "current_volume_tick": 0.0,         # Accumulating volume within current candle
    "current_volume_5m_tick": 0.0,
    "tick_count": 0,                     # Ticks in current 1m candle
    "tick_count_5m": 0,                  # Ticks in current 5m candle
    "tick_history": [],                  # Rolling history of ticks per 1m candle (last 20)
    "tick_history_5m": [],               # Rolling history of ticks per 5m candle (last 20)
    "price_chart": [],                   # 1m candle closes for charting [{time, price}] — up to 10080 (1 week)
    "equity_history": [],                # Portfolio value over time [{time, value, pnl}]
    "trade_markers": [],                 # Recent trades for chart overlay [{time, action, price}]
    "live_orders": {},                   # {level_idx: coinbase_order_id} for live mode tracking
    "daily_loss_tracker": {
        "date": "",
        "start_value": 0.0,
        "loss_today": 0.0,
    },
}

# --- PERSISTENCE ---
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                saved_data = json.load(f)
                state["portfolio"].update(saved_data.get("portfolio", {}))
                saved_settings = saved_data.get("settings", {})
                for k, v in saved_settings.items():
                    if k in state["settings"]:
                        state["settings"][k] = v
                state["stats"].update(saved_data.get("stats", {}))
                state["grid_state"].update(saved_data.get("grid_state", {}))
            logger.info("💾 Loaded previous portfolio, settings & stats from disk.")
        except Exception as e:
            logger.error(f"Failed to load state: {e}")

def save_state():
    try:
        serializable_grid = {k: v for k, v in state["grid_state"].items()}
        with open(STATE_FILE, 'w') as f:
            json.dump({
                "portfolio": state["portfolio"],
                "settings": state["settings"],
                "stats": state["stats"],
                "grid_state": serializable_grid,
            }, f, indent=4)
    except Exception as e:
        logger.error(f"Failed to save state: {e}")


class ConnectionManager:
    def __init__(self):
        self.active_connections = []

    async def connect(self, ws):
        await ws.accept()
        self.active_connections.append(ws)
        await ws.send_json(get_broadcast_payload())

    def disconnect(self, ws):
        if ws in self.active_connections:
            self.active_connections.remove(ws)

    async def broadcast_state(self):
        payload = get_broadcast_payload()
        for c in list(self.active_connections):
            try:
                await c.send_json(payload)
            except Exception:
                self.active_connections.remove(c)


def get_broadcast_payload():
    return {
        "prices": state["prices"],
        "portfolio": state["portfolio"],
        "indicators": state["indicators"],
        "macro": state["macro"],
        "logs": state["logs"],
        "settings": state["settings"],
        "stats": state["stats"],
        "grid_state": {
            "initialized": state["grid_state"]["initialized"],
            "current_index": state["grid_state"]["current_index"],
            "levels_count": len(state["grid_state"]["levels"]),
            "range_high": state["grid_state"]["range_high"],
            "range_low": state["grid_state"]["range_low"],
            "open_positions": len(state["grid_state"]["filled_levels"]),
        },
        "circuit_breaker": state["circuit_breaker"],
        "price_chart": state["price_chart"][-10080:],   # up to 1 week of 1m candles
        "equity_history": state["equity_history"][-10080:],
        "trade_markers": state["trade_markers"][-500:],
    }


manager = ConnectionManager()

# --- UTILS & INDICATORS ---
async def log_event(message: str):
    ts = datetime.now().strftime("%H:%M:%S")
    formatted = f"[{ts}] {message}"
    logger.info(message)
    state["logs"].insert(0, formatted)
    if len(state["logs"]) > 100:
        state["logs"].pop()
    await manager.broadcast_state()


def calculate_fees(amount_usd):
    return round(float(amount_usd * state["settings"]["fee_rate"]), 4)


def calculate_atr(prices, period=14):
    if len(prices) < period + 1:
        return 0.0
    diffs = np.abs(np.diff(prices[-(period + 1):]))
    return float(np.mean(diffs))


def calculate_stoch_rsi(prices, rsi_period=14, stoch_period=14):
    if len(prices) < rsi_period + stoch_period:
        return 50.0

    delta = np.diff(prices)
    gains = np.where(delta > 0, delta, 0.0)
    losses = np.where(delta < 0, -delta, 0.0)

    rsi_values = []
    for i in range(rsi_period, len(delta) + 1):
        avg_gain = np.mean(gains[i - rsi_period:i])
        avg_loss = np.mean(losses[i - rsi_period:i])
        if avg_loss == 0:
            rsi_values.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_values.append(100.0 - (100.0 / (1.0 + rs)))

    if len(rsi_values) < stoch_period:
        return 50.0

    recent_rsi = rsi_values[-stoch_period:]
    rsi_min = min(recent_rsi)
    rsi_max = max(recent_rsi)
    if rsi_max == rsi_min:
        return 50.0
    stoch = ((rsi_values[-1] - rsi_min) / (rsi_max - rsi_min)) * 100.0
    return float(np.clip(stoch, 0, 100))


def calculate_trend(arr_series):
    """Returns (trend_str, trend_strength) from a price series."""
    s = pd.Series(arr_series)
    ema_fast = float(s.ewm(span=9, adjust=False).mean().iloc[-1])
    ema_slow = float(s.ewm(span=21, adjust=False).mean().iloc[-1])
    if ema_fast > ema_slow * 1.001:
        trend = "BULLISH"
        strength = min((ema_fast - ema_slow) / ema_slow * 100, 1.0)
    elif ema_fast < ema_slow * 0.999:
        trend = "BEARISH"
        strength = min((ema_slow - ema_fast) / ema_slow * 100, 1.0)
    else:
        trend = "NEUTRAL"
        strength = 0.0
    return trend, float(strength), float(ema_fast), float(ema_slow)


def calculate_volume_sma(volumes, period=20):
    """Volume SMA and ratio of current volume to average."""
    if len(volumes) < 2:
        return 0.0, 1.0
    arr = np.array(volumes[-period:], dtype=float)
    sma = float(np.mean(arr))
    current = float(volumes[-1]) if volumes else 0.0
    ratio = current / sma if sma > 0 else 1.0
    return round(sma, 4), round(ratio, 3)


def calculate_all_indicators(prices, volumes=None):
    """Full indicator suite including volume and multi-timeframe trend."""
    if len(prices) < 30:
        return state["indicators"]["BTC/USD"]

    arr = np.array(prices, dtype=float)

    # --- RSI ---
    delta = np.diff(arr)
    gain = delta.clip(min=0)
    loss = -delta.clip(max=0)
    period = 14
    if len(gain) >= period:
        avg_gain = np.mean(gain[-period:])
        avg_loss = np.mean(loss[-period:])
        rsi = 100.0 if avg_loss == 0 else 100.0 - (100.0 / (1.0 + (avg_gain / avg_loss)))
    else:
        rsi = 50.0

    # --- Bollinger Bands ---
    recent20 = arr[-20:]
    sma20 = float(np.mean(recent20))
    std20 = float(np.std(recent20))
    bb_upper = sma20 + (2.0 * std20)
    bb_lower = sma20 - (2.0 * std20)

    # --- MACD ---
    s = pd.Series(arr)
    ema12 = s.ewm(span=12, adjust=False).mean()
    ema26 = s.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    macd_signal = macd_line.ewm(span=9, adjust=False).mean()
    macd_histogram = macd_line - macd_signal

    # --- 1m Trend ---
    trend_1m, strength_1m, ema_fast, ema_slow = calculate_trend(arr)

    # --- 5m Trend (multi-timeframe) ---
    prices_5m = state["history_5m"].get("BTC/USD", [])
    if len(prices_5m) >= 10:
        trend_5m, strength_5m, _, _ = calculate_trend(np.array(prices_5m, dtype=float))
    else:
        trend_5m, strength_5m = "NEUTRAL", 0.0

    # Multi-timeframe agreement
    mtf_agreement = (trend_1m == trend_5m) and trend_1m != "NEUTRAL"

    # --- ATR ---
    atr = calculate_atr(arr)

    # --- Volatility ---
    volatility = float(np.mean(np.abs(np.diff(arr[-15:])))) if len(arr) >= 15 else 0.0

    # --- Stochastic RSI ---
    stoch_rsi = calculate_stoch_rsi(arr)

    # --- Volume ---
    vol_sma, vol_ratio = 0.0, 1.0
    if volumes and len(volumes) >= 2:
        vol_sma, vol_ratio = calculate_volume_sma(volumes)

    return {
        "rsi": float(rsi),
        "bb_upper": float(bb_upper),
        "bb_mid": float(sma20),
        "bb_lower": float(bb_lower),
        "macd": float(macd_line.iloc[-1]),
        "macd_signal": float(macd_signal.iloc[-1]),
        "macd_histogram": float(macd_histogram.iloc[-1]),
        "volatility": float(volatility),
        "atr": float(atr),
        "ema_fast": float(ema_fast),
        "ema_slow": float(ema_slow),
        "trend": trend_1m,
        "trend_strength": float(strength_1m),
        "trend_5m": trend_5m,
        "trend_strength_5m": float(strength_5m),
        "mtf_agreement": mtf_agreement,
        "volume_sma": float(vol_sma),
        "volume_ratio": float(vol_ratio),
        "stoch_rsi": float(stoch_rsi),
    }

# --- ADAPTIVE GRID ENGINE ---

def compute_dynamic_grid_range(current_price, atr):
    multiplier = state["settings"]["atr_multiplier"]
    half_range = atr * multiplier
    min_range = current_price * 0.01
    half_range = max(half_range, min_range)
    return current_price - half_range, current_price + half_range


def initialize_grid(current_price):
    settings = state["settings"]
    atr = state["indicators"]["BTC/USD"].get("atr", 0)

    if settings["auto_range"] and atr > 0:
        lower, upper = compute_dynamic_grid_range(current_price, atr)
    else:
        upper = settings["grid_upper"] if settings["grid_upper"] > 0 else current_price * 1.03
        lower = settings["grid_lower"] if settings["grid_lower"] > 0 else current_price * 0.97

    if upper <= lower:
        upper = current_price * 1.03
        lower = current_price * 0.97

    levels_count = settings["grid_levels"]
    grid_array = np.linspace(lower, upper, levels_count).tolist()
    state["grid_state"]["levels"] = grid_array
    state["grid_state"]["range_high"] = upper
    state["grid_state"]["range_low"] = lower

    closest_idx = min(range(len(grid_array)), key=lambda i: abs(grid_array[i] - current_price))
    state["grid_state"]["current_index"] = closest_idx
    state["grid_state"]["initialized"] = True
    state["grid_state"]["last_rebalance"] = time.time()

    step_size = grid_array[1] - grid_array[0] if len(grid_array) > 1 else 0
    logger.info(f"🕸️ Grid Active: {levels_count} levels from ${lower:.2f}-${upper:.2f}. Step: ${step_size:.2f}")


def should_rebalance_grid(current_price):
    gs = state["grid_state"]
    if not gs["initialized"] or not state["settings"]["auto_range"]:
        return False

    now = time.time()
    interval = state["settings"]["rebalance_interval"]
    if now - gs["last_rebalance"] < interval:
        return False

    grid_range = gs["range_high"] - gs["range_low"]
    if grid_range <= 0:
        return True
    position_in_range = (current_price - gs["range_low"]) / grid_range

    return position_in_range < 0.15 or position_in_range > 0.85


def get_trade_size(current_price):
    base_size = state["settings"]["trade_size_usd"]
    if not state["settings"]["volatility_scaling"]:
        return base_size

    ind = state["indicators"]["BTC/USD"]
    bb_upper = ind.get("bb_upper", 0)
    bb_lower = ind.get("bb_lower", 0)
    bb_mid = ind.get("bb_mid", 0)

    if bb_upper <= bb_lower or bb_mid == 0:
        return base_size

    bb_range = bb_upper - bb_lower
    distance_from_mid = abs(current_price - bb_mid)
    bb_position = distance_from_mid / (bb_range / 2) if bb_range > 0 else 0
    scale_factor = 1.0 + min(bb_position, 1.0)

    atr = ind.get("atr", 0)
    if atr > 0 and bb_mid > 0:
        vol_ratio = atr / bb_mid * 1000
        vol_scale = min(1.0 + vol_ratio * 0.5, 1.5)
        scale_factor *= vol_scale

    scaled_size = base_size * scale_factor
    return min(scaled_size, state["settings"]["max_trade_size_usd"])


def check_cooldown(level_idx):
    last_time = state["grid_state"]["last_trade_time"].get(str(level_idx), 0)
    return (time.time() - last_time) >= state["settings"]["cooldown_seconds"]


def check_trend_filter(action):
    """
    Grid-aware trend filter:
    - Grid bots PROFIT by selling into strength and buying into weakness.
    - Only block in extreme scenarios (clear capitulation/falling knife).
    - MTF agreement = grid is working as intended, allow all trades freely.
    - Volume filter uses real tick-based volume ratio.
    """
    if not state["settings"]["trend_filter"]:
        return True

    ind = state["indicators"]["BTC/USD"]
    trend_1m = ind.get("trend", "NEUTRAL")
    trend_5m = ind.get("trend_5m", "NEUTRAL")
    stoch_rsi = ind.get("stoch_rsi", 50)
    mtf_agreement = ind.get("mtf_agreement", False)
    volume_ratio = ind.get("volume_ratio", 1.0)

    # Volume filter: only apply when we have enough tick history (real data)
    if state["settings"].get("volume_filter", True):
        if len(state.get("tick_history", [])) >= 5 and volume_ratio < 0.6:
            return False

    # MTF agreement: grid is working as intended — allow all trades freely
    if state["settings"].get("mtf_trend_filter", True) and mtf_agreement:
        return True

    if action == "BUY":
        # Only block clear falling knife: BOTH TFs bearish AND not yet oversold
        if trend_1m == "BEARISH" and trend_5m == "BEARISH" and stoch_rsi > 60:
            return False
        return True

    elif action == "SELL":
        # Only block in extreme capitulation dump (price in freefall, don't sell into hole)
        if trend_1m == "BEARISH" and trend_5m == "BEARISH" and stoch_rsi < 20:
            return False
        return True

    return True


def check_circuit_breaker():
    cb = state["circuit_breaker"]

    if cb["active"]:
        if time.time() > cb["cooldown_until"]:
            cb["active"] = False
            cb["reason"] = ""
            logger.info("🟢 Circuit breaker reset. Trading resumed.")
            return False
        return True

    total_value = state["portfolio"]["total_value"]
    initial = state["portfolio"]["initial_balance"]
    peak = state["stats"]["peak_value"]

    if initial <= 0:
        return False

    drawdown_from_peak = ((peak - total_value) / peak) * 100 if peak > 0 else 0
    drawdown_from_initial = ((initial - total_value) / initial) * 100
    max_dd = max(drawdown_from_peak, drawdown_from_initial)
    state["stats"]["max_drawdown"] = round(max_dd, 2)

    if max_dd >= state["settings"]["drawdown_limit_pct"]:
        cb["active"] = True
        cb["triggered_at"] = time.time()
        cb["cooldown_until"] = time.time() + 300
        cb["reason"] = f"Drawdown {max_dd:.1f}% exceeded {state['settings']['drawdown_limit_pct']}% limit"
        logger.warning(f"🔴 CIRCUIT BREAKER: {cb['reason']}")
        return True

    return False


def update_stats(action, pnl, trade_usd):
    stats = state["stats"]
    stats["total_trades"] += 1
    stats["total_volume"] = round(stats["total_volume"] + trade_usd, 2)

    if action == "SELL" and pnl != 0:
        if pnl > 0:
            stats["winning_trades"] += 1
            stats["largest_win"] = max(stats["largest_win"], pnl)
            # Rolling avg win
            n = stats["winning_trades"]
            stats["avg_win"] = round(((stats["avg_win"] * (n - 1)) + pnl) / n, 4)
        else:
            stats["losing_trades"] += 1
            stats["largest_loss"] = min(stats["largest_loss"], pnl)
            # Rolling avg loss
            n = stats["losing_trades"]
            stats["avg_loss"] = round(((stats["avg_loss"] * (n - 1)) + pnl) / n, 4)

    total = stats["winning_trades"] + stats["losing_trades"]
    stats["win_rate"] = round((stats["winning_trades"] / total * 100) if total > 0 else 0, 1)

    # Profit factor = gross wins / gross losses
    gross_wins = stats["avg_win"] * stats["winning_trades"] if stats["winning_trades"] > 0 else 0
    gross_losses = abs(stats["avg_loss"]) * stats["losing_trades"] if stats["losing_trades"] > 0 else 0
    stats["profit_factor"] = round(gross_wins / gross_losses, 2) if gross_losses > 0 else (float("inf") if gross_wins > 0 else 0.0)

    if state["portfolio"]["total_value"] > stats["peak_value"]:
        stats["peak_value"] = round(state["portfolio"]["total_value"], 2)


async def execute_trade(symbol, action, current_price, trade_usd_amount, level_idx=None):
    # ── Live Trading Mode ─────────────────────────────────────────────────────
    if state["settings"].get("live_trading"):
        if check_daily_loss_limit():
            await log_event("🛑 Daily loss limit reached — trade blocked.")
            return False
        try:
            if action == "BUY":
                order = await cb_place_order("BUY", quote_size=trade_usd_amount)
                order_id = order.get("order_id", "unknown")
                # Get actual fill from Coinbase account
                await asyncio.sleep(1.5)  # let order settle
                accounts = await cb_get_account()
                btc_bal = accounts["BTC"]
                usd_bal = accounts["USD"]
                exec_price = current_price  # approximate; real fill in order details
                qty = round(trade_usd_amount / exec_price, 8)
                fee = calculate_fees(trade_usd_amount)

                state["portfolio"]["cash"] = round(usd_bal, 2)
                state["portfolio"]["holdings"][symbol] = round(btc_bal, 8)
                state["portfolio"]["total_fees"] = round(state["portfolio"]["total_fees"] + fee, 4)
                if level_idx is not None:
                    state["grid_state"]["filled_levels"][str(level_idx)] = {
                        "qty": qty, "entry_price": exec_price,
                        "cost": trade_usd_amount, "time": time.time(),
                    }
                    state["grid_state"]["last_trade_time"][str(level_idx)] = time.time()
                    state["live_orders"][str(level_idx)] = order_id

                update_stats("BUY", 0, trade_usd_amount)
                await log_event(f"🟢 LIVE BUY ${trade_usd_amount:.2f} @ ~${exec_price:.2f} | Order: {order_id[:8]}… [Grid #{level_idx}]")
                state["trade_markers"].append({"time": datetime.now().strftime("%m/%d %H:%M"), "action": "BUY", "price": round(exec_price, 2)})
                if len(state["trade_markers"]) > 500: state["trade_markers"].pop(0)
                return True

            elif action == "SELL":
                fill_key = str(level_idx) if level_idx is not None else None
                fill = state["grid_state"]["filled_levels"].get(fill_key) if fill_key else None
                qty_to_sell = fill["qty"] if fill else round(trade_usd_amount / current_price, 8)
                entry_cost = fill["cost"] if fill else trade_usd_amount

                order = await cb_place_order("SELL", base_size=qty_to_sell)
                order_id = order.get("order_id", "unknown")
                await asyncio.sleep(1.5)
                accounts = await cb_get_account()
                usd_bal = accounts["USD"]
                btc_bal = accounts["BTC"]
                exec_price = current_price
                sale_value = round(qty_to_sell * exec_price, 2)
                fee = calculate_fees(sale_value)
                pnl = round(sale_value - fee - entry_cost, 2)

                state["portfolio"]["cash"] = round(usd_bal, 2)
                state["portfolio"]["holdings"][symbol] = round(btc_bal, 8)
                state["portfolio"]["total_fees"] = round(state["portfolio"]["total_fees"] + fee, 4)
                state["portfolio"]["realized_pnl"] = round(state["portfolio"]["realized_pnl"] + pnl, 2)
                if fill_key and fill_key in state["grid_state"]["filled_levels"]:
                    del state["grid_state"]["filled_levels"][fill_key]
                if fill_key and fill_key in state["live_orders"]:
                    del state["live_orders"][fill_key]
                if level_idx is not None:
                    state["grid_state"]["last_trade_time"][str(level_idx)] = time.time()

                update_stats("SELL", pnl, sale_value)
                pnl_emoji = "✅" if pnl >= 0 else "❌"
                await log_event(f"🔴 LIVE SELL ${sale_value:.2f} @ ~${exec_price:.2f} P&L: {pnl_emoji}${pnl:.2f} | Order: {order_id[:8]}… [Grid #{level_idx}]")
                state["trade_markers"].append({"time": datetime.now().strftime("%m/%d %H:%M"), "action": "SELL", "price": round(exec_price, 2)})
                if len(state["trade_markers"]) > 500: state["trade_markers"].pop(0)
                return True

        except Exception as e:
            await log_event(f"❌ LIVE ORDER FAILED: {e}")
            return False

    # ── Paper Trading Mode ────────────────────────────────────────────────────
    slippage = current_price * state["settings"]["slippage_rate"]
    exec_price = current_price + slippage if action == "BUY" else current_price - slippage

    pnl = 0.0

    if action == "BUY":
        if state["portfolio"]["cash"] < trade_usd_amount:
            return False
        if len(state["grid_state"]["filled_levels"]) >= state["settings"]["max_open_positions"]:
            return False

        fee = calculate_fees(trade_usd_amount)
        qty = round(float((trade_usd_amount - fee) / exec_price), 8)

        state["portfolio"]["cash"] = round(state["portfolio"]["cash"] - trade_usd_amount, 2)
        state["portfolio"]["total_fees"] = round(state["portfolio"]["total_fees"] + fee, 4)
        state["portfolio"]["holdings"][symbol] = round(state["portfolio"]["holdings"][symbol] + qty, 8)

        old_qty = state["portfolio"]["holdings"][symbol] - qty
        old_cost = old_qty * state["portfolio"]["cost_basis"].get(symbol, 0)
        new_cost = qty * exec_price
        total_qty = state["portfolio"]["holdings"][symbol]
        if total_qty > 0:
            state["portfolio"]["cost_basis"][symbol] = round((old_cost + new_cost) / total_qty, 2)

        if level_idx is not None:
            state["grid_state"]["filled_levels"][str(level_idx)] = {
                "qty": qty,
                "entry_price": exec_price,
                "cost": trade_usd_amount,
                "time": time.time(),
            }
            state["grid_state"]["last_trade_time"][str(level_idx)] = time.time()

        update_stats("BUY", 0, trade_usd_amount)
        await log_event(f"💰 BUY ${trade_usd_amount:.2f} @ ${exec_price:.2f} (Fee: ${fee:.4f}) [Grid #{level_idx}]")
        state["trade_markers"].append({"time": datetime.now().strftime("%m/%d %H:%M"), "action": "BUY", "price": round(exec_price, 2)})
        if len(state["trade_markers"]) > 500: state["trade_markers"].pop(0)
        return True

    elif action == "SELL":
        fill = None
        fill_key = None
        if level_idx is not None:
            fill_key = str(level_idx)
            fill = state["grid_state"]["filled_levels"].get(fill_key)

        if fill:
            qty_to_sell = fill["qty"]
            entry_cost = fill["cost"]
        else:
            qty_to_sell = round(float(trade_usd_amount / exec_price), 8)
            entry_cost = trade_usd_amount

        if state["portfolio"]["holdings"][symbol] < qty_to_sell:
            return False

        sale_value = round(float(qty_to_sell * exec_price), 2)
        fee = calculate_fees(sale_value)
        net_sale = sale_value - fee
        pnl = round(net_sale - entry_cost, 2)

        state["portfolio"]["cash"] = round(state["portfolio"]["cash"] + net_sale, 2)
        state["portfolio"]["total_fees"] = round(state["portfolio"]["total_fees"] + fee, 4)
        state["portfolio"]["holdings"][symbol] = round(state["portfolio"]["holdings"][symbol] - qty_to_sell, 8)
        state["portfolio"]["realized_pnl"] = round(state["portfolio"]["realized_pnl"] + pnl, 2)

        if state["portfolio"]["holdings"][symbol] <= 1e-8:
            state["portfolio"]["holdings"][symbol] = 0.0
            state["portfolio"]["cost_basis"][symbol] = 0.0

        if fill_key and fill_key in state["grid_state"]["filled_levels"]:
            del state["grid_state"]["filled_levels"][fill_key]

        if level_idx is not None:
            state["grid_state"]["last_trade_time"][str(level_idx)] = time.time()

        update_stats("SELL", pnl, sale_value)
        pnl_emoji = "✅" if pnl >= 0 else "❌"
        await log_event(f"🤝 SELL ${sale_value:.2f} @ ${exec_price:.2f} (Fee: ${fee:.4f}) P&L: {pnl_emoji}${pnl:.2f} [Grid #{level_idx}]")
        state["trade_markers"].append({"time": datetime.now().strftime("%m/%d %H:%M"), "action": "SELL", "price": round(exec_price, 2)})
        if len(state["trade_markers"]) > 500: state["trade_markers"].pop(0)
        return True

    return False


async def evaluate_grid(symbol, price):
    if check_circuit_breaker():
        return

    gs = state["grid_state"]

    if not gs["initialized"]:
        initialize_grid(price)
        await log_event(f"🕸️ Grid initialized: ${gs['range_low']:.0f} - ${gs['range_high']:.0f} ({len(gs['levels'])} levels)")
        return

    if should_rebalance_grid(price):
        initialize_grid(price)
        await log_event(f"🔄 Grid rebalanced around ${price:.0f}: ${gs['range_low']:.0f} - ${gs['range_high']:.0f}")
        return

    levels = gs["levels"]
    curr_idx = gs["current_index"]

    if not levels or curr_idx < 0 or curr_idx >= len(levels):
        initialize_grid(price)
        return

    trade_size = get_trade_size(price)
    trades_this_tick = 0
    max_trades_per_tick = 3

    # --- Price moved UP: SELL signals ---
    while curr_idx < len(levels) - 1 and trades_this_tick < max_trades_per_tick:
        level_above = levels[curr_idx + 1]
        if price < level_above:
            break

        next_idx = curr_idx + 1
        sell_level = None
        for filled_idx_str in list(state["grid_state"]["filled_levels"].keys()):
            filled_idx = int(filled_idx_str)
            if filled_idx <= curr_idx:
                sell_level = filled_idx
                break

        if sell_level is not None:
            if check_cooldown(next_idx) and check_trend_filter("SELL"):
                success = await execute_trade(symbol, "SELL", price, trade_size, level_idx=sell_level)
                if success:
                    trades_this_tick += 1

        state["grid_state"]["current_index"] = next_idx
        curr_idx = next_idx

    # --- Price moved DOWN: BUY signals ---
    curr_idx = state["grid_state"]["current_index"]
    while curr_idx > 0 and trades_this_tick < max_trades_per_tick:
        level_below = levels[curr_idx - 1]
        if price > level_below:
            break

        next_idx = curr_idx - 1
        if str(next_idx) not in state["grid_state"]["filled_levels"]:
            if check_cooldown(next_idx) and check_trend_filter("BUY"):
                if state["portfolio"]["cash"] >= trade_size:
                    success = await execute_trade(symbol, "BUY", price, trade_size, level_idx=next_idx)
                    if success:
                        trades_this_tick += 1

        state["grid_state"]["current_index"] = next_idx
        curr_idx = next_idx

    # --- Trailing Take-Profit scan ---
    if state["settings"].get("trailing_take_profit", True):
        tp_pct = state["settings"].get("trailing_tp_pct", 1.5) / 100.0
        lock_pct = state["settings"].get("profit_lock_pct", 0.5) / 100.0

        for level_key in list(gs["filled_levels"].keys()):
            fill = gs["filled_levels"].get(level_key)
            if not fill:
                continue
            entry = fill.get("entry_price", 0)
            if entry <= 0:
                continue

            if not fill.get("trailing_active", False):
                if price >= entry * (1.0 + tp_pct):
                    fill["trailing_active"] = True
                    fill["trail_high"] = price
                    await log_event(f"🎯 Trailing TP activated for Grid #{level_key} @ ${price:.2f} (entry ${entry:.2f})")
            else:
                if price > fill.get("trail_high", 0):
                    fill["trail_high"] = price
                trail_high = fill.get("trail_high", price)
                if price <= trail_high * (1.0 - lock_pct):
                    await log_event(f"🔒 Trailing TP triggered for Grid #{level_key} @ ${price:.2f} (high ${trail_high:.2f})")
                    await execute_trade(symbol, "SELL", price, get_trade_size(price), level_idx=int(level_key))


async def process_price_update(symbol, price, volume=0.0):
    price = float(price)
    volume = float(volume)
    state["prices"][symbol] = price

    now = time.time()

    # Accumulate volume within candle period
    state["current_volume_tick"] = state.get("current_volume_tick", 0.0) + volume
    state["current_volume_5m_tick"] = state.get("current_volume_5m_tick", 0.0) + volume

    # Tick counters — real activity proxy (count WebSocket price updates per candle)
    state["tick_count"] = state.get("tick_count", 0) + 1
    state["tick_count_5m"] = state.get("tick_count_5m", 0) + 1

    # Update 1-min candle history
    if now - state.get("last_history_update", 0) >= 60:
        state["history"][symbol].append(price)
        state["volume_history"][symbol].append(state["current_volume_tick"])
        # Save tick count for this completed candle
        state["tick_history"].append(state["tick_count"])
        if len(state["tick_history"]) > 20:
            state["tick_history"].pop(0)
        state["tick_count"] = 0
        state["current_volume_tick"] = 0.0
        if len(state["history"][symbol]) > 200:
            state["history"][symbol].pop(0)
            state["volume_history"][symbol].pop(0)
        state["last_history_update"] = now
    else:
        if len(state["history"][symbol]) > 0:
            state["history"][symbol][-1] = price

    # Update 5-min candle history
    if now - state.get("last_5m_update", 0) >= 300:
        state["history_5m"][symbol].append(price)
        state["volume_5m"][symbol].append(state["current_volume_5m_tick"])
        state["tick_history_5m"].append(state["tick_count_5m"])
        if len(state["tick_history_5m"]) > 20:
            state["tick_history_5m"].pop(0)
        state["tick_count_5m"] = 0
        state["current_volume_5m_tick"] = 0.0
        if len(state["history_5m"][symbol]) > 100:
            state["history_5m"][symbol].pop(0)
            state["volume_5m"][symbol].pop(0)
        state["last_5m_update"] = now
    else:
        if len(state["history_5m"][symbol]) > 0:
            state["history_5m"][symbol][-1] = price

    # Compute real tick-based volume ratio
    tick_hist = state.get("tick_history", [])
    if len(tick_hist) >= 3:
        avg_ticks = float(np.mean(tick_hist[-20:]))
        current_ticks = float(state.get("tick_count", 0))
        tick_vol_ratio = (current_ticks / avg_ticks) if avg_ticks > 0 else 1.0
    else:
        tick_vol_ratio = 1.0

    # Track whether a new candle just closed this tick
    candle_closed = (now - state.get("last_history_update", 0)) < 2.0 and len(state["history"][symbol]) > 0

    # Recalculate indicators
    state["indicators"][symbol] = calculate_all_indicators(
        state["history"][symbol],
        volumes=state["volume_history"][symbol]
    )
    # Override volume_ratio with real tick-based value
    state["indicators"][symbol]["volume_ratio"] = round(tick_vol_ratio, 3)

    # Update price chart and equity history on candle close only
    if candle_closed:
        ts_str = datetime.now().strftime("%m/%d %H:%M")
        state["price_chart"].append({"time": ts_str, "price": round(price, 2)})
        if len(state["price_chart"]) > 10080:
            state["price_chart"].pop(0)

        state["equity_history"].append({
            "time": ts_str,
            "value": round(state["portfolio"]["total_value"], 2),
            "pnl": round(state["portfolio"]["total_profit"], 2),
        })
        if len(state["equity_history"]) > 10080:
            state["equity_history"].pop(0)

        # Periodic save (every candle close, ~60s)
        save_state()

    # Portfolio value tracking
    h_val = round(float(state["portfolio"]["holdings"][symbol] * price), 2)
    state["portfolio"]["total_value"] = round(float(state["portfolio"]["cash"] + h_val), 2)
    state["portfolio"]["total_profit"] = round(float(state["portfolio"]["total_value"] - state["portfolio"]["initial_balance"]), 2)

    # Unrealized P&L
    cost_basis = state["portfolio"]["cost_basis"].get(symbol, 0)
    if state["portfolio"]["holdings"][symbol] > 0 and cost_basis > 0:
        unrealized = (price - cost_basis) * state["portfolio"]["holdings"][symbol]
        state["portfolio"]["unrealized_pnl"] = round(unrealized, 2)
    else:
        state["portfolio"]["unrealized_pnl"] = 0.0

    if state["portfolio"]["total_value"] > state["stats"].get("peak_value", 0):
        state["stats"]["peak_value"] = round(state["portfolio"]["total_value"], 2)

    await evaluate_grid(symbol, price)
    await manager.broadcast_state()


# --- DATA WARMUP ---
async def warmup_indicators(internal_symbol="BTC/USD"):
    await log_event(f"🔥 WARMING UP INDICATORS (1m + 5m candles)...")
    headers = HEADERS

    async with aiohttp.ClientSession() as session:
        # 1-min candles
        try:
            url_1m = "https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=60"
            async with session.get(url_1m, headers=headers) as r:
                if r.status == 200:
                    data = await r.json()
                    # candle: [time, low, high, open, close, volume]
                    data.reverse()
                    closes = [float(c[4]) for c in data[-100:]]
                    volumes = [float(c[5]) for c in data[-100:]]
                    state["history"][internal_symbol] = closes
                    state["volume_history"][internal_symbol] = volumes
                    state["last_history_update"] = time.time()
        except Exception as e:
            logger.error(f"1m warmup error: {e}")

        # 5-min candles
        try:
            url_5m = "https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=300"
            async with session.get(url_5m, headers=headers) as r:
                if r.status == 200:
                    data = await r.json()
                    data.reverse()
                    closes_5m = [float(c[4]) for c in data[-60:]]
                    volumes_5m = [float(c[5]) for c in data[-60:]]
                    state["history_5m"][internal_symbol] = closes_5m
                    state["volume_5m"][internal_symbol] = volumes_5m
                    state["last_5m_update"] = time.time()
        except Exception as e:
            logger.error(f"5m warmup error: {e}")

    state["indicators"][internal_symbol] = calculate_all_indicators(
        state["history"][internal_symbol],
        volumes=state["volume_history"][internal_symbol]
    )
    # Seed price chart from warmup history
    closes = state["history"][internal_symbol]
    now_ts = int(time.time())
    state["price_chart"] = [
        {"time": datetime.fromtimestamp(now_ts - (len(closes) - i) * 60).strftime("%m/%d %H:%M"), "price": round(p, 2)}
        for i, p in enumerate(closes[-10080:])
    ]
    await log_event(f"✅ Warmup complete. 1m trend: {state['indicators'][internal_symbol]['trend']} | 5m trend: {state['indicators'][internal_symbol]['trend_5m']}")


async def fetch_macro_context(internal_symbol="BTC/USD"):
    url = "https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=86400"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers=HEADERS) as response:
                if response.status == 200:
                    data = await response.json()
                    closes = [float(candle[4]) for candle in data]
                    if closes:
                        closes.reverse()
                        closes_90 = closes[-90:]
                        state["macro"][internal_symbol] = {
                            "high_90d": max(closes_90),
                            "low_90d": min(closes_90),
                            "trend_pct": ((closes_90[-1] - closes_90[0]) / closes_90[0]) * 100
                        }
        except:
            pass


# --- COINBASE WEBSOCKET STREAM ---
async def stream_live_crypto():
    """
    Connects to Coinbase Advanced Trade WebSocket for real-time tick data.
    Falls back to REST polling if WebSocket fails.
    """
    load_state()
    await asyncio.sleep(1)
    await asyncio.gather(fetch_macro_context(), warmup_indicators())
    await log_event("📡 CONNECTING TO COINBASE WEBSOCKET STREAM...")

    ws_url = "wss://advanced-trade-ws.coinbase.com"
    subscribe_msg = json.dumps({
        "type": "subscribe",
        "product_ids": ["BTC-USD"],
        "channel": "ticker"
    })

    backoff = 2
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(ws_url, heartbeat=30) as ws:
                    await ws.send_str(subscribe_msg)
                    await log_event("✅ WebSocket connected to Coinbase. Live tick data active.")
                    backoff = 2  # reset on successful connect

                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                data = json.loads(msg.data)
                                # Coinbase Advanced Trade ticker format
                                events = data.get("events", [])
                                for event in events:
                                    tickers = event.get("tickers", [])
                                    for ticker in tickers:
                                        if ticker.get("product_id") == "BTC-USD":
                                            price = float(ticker.get("price", 0))
                                            volume = float(ticker.get("volume_24_h", 0))
                                            if price > 0:
                                                await process_price_update("BTC/USD", price, volume / 86400)
                            except Exception as e:
                                logger.error(f"WS message parse error: {e}")
                        elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                            break

        except Exception as e:
            logger.error(f"WebSocket error: {e}. Reconnecting in {backoff}s...")
            await log_event(f"⚠️ WS disconnected. Falling back to REST for {backoff}s...")

            # Fallback REST polling during reconnect window
            try:
                async with aiohttp.ClientSession() as session:
                    for _ in range(backoff):
                        try:
                            async with session.get(
                                "https://api.exchange.coinbase.com/products/BTC-USD/ticker",
                                headers=HEADERS
                            ) as r:
                                if r.status == 200:
                                    d = await r.json()
                                    await process_price_update("BTC/USD", float(d["price"]))
                        except Exception:
                            pass
                        await asyncio.sleep(2)
            except Exception:
                pass

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


# --- API SETUP ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    t = asyncio.create_task(stream_live_crypto())
    yield
    t.cancel()
    save_state()

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            data = await ws.receive_text()
            try:
                payload = json.loads(data)
                if payload.get("type") == "UPDATE_SETTINGS":
                    if "cash" in payload:
                        new_cash = float(payload["cash"])
                        cash_diff = new_cash - state["portfolio"]["cash"]
                        state["portfolio"]["cash"] = round(new_cash, 2)
                        state["portfolio"]["initial_balance"] = round(state["portfolio"]["initial_balance"] + cash_diff, 2)

                    if "trade_size_usd" in payload:
                        state["settings"]["trade_size_usd"] = float(payload["trade_size_usd"])

                    reinit = False
                    if "grid_upper" in payload:
                        state["settings"]["grid_upper"] = float(payload["grid_upper"])
                        reinit = True
                    if "grid_lower" in payload:
                        state["settings"]["grid_lower"] = float(payload["grid_lower"])
                        reinit = True
                    if "grid_levels" in payload:
                        state["settings"]["grid_levels"] = int(payload["grid_levels"])
                        reinit = True

                    # Boolean toggles
                    for flag in ("volume_filter", "mtf_trend_filter", "trend_filter", "volatility_scaling", "auto_range", "live_trading"):
                        if flag in payload:
                            state["settings"][flag] = bool(payload[flag])

                    if reinit:
                        state["grid_state"]["initialized"] = False

                    save_state()
                    await log_event(f"⚙️ Settings Updated (Grid: {state['settings']['grid_lower']}-{state['settings']['grid_upper']} | Size: ${state['settings']['trade_size_usd']})")
                    await manager.broadcast_state()
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        manager.disconnect(ws)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
