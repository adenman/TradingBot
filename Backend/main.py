import os
import json
import asyncio
import logging
import time
import math
from collections import deque
from datetime import datetime
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

# --- STATE MANAGEMENT ---
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
    "history": {"BTC/USD": []},
    "history_5m": {"BTC/USD": []},       # 5-min candles for multi-timeframe
    "indicators": {
        "BTC/USD": {
            "rsi": 50.0,
            "bb_upper": 0.0, "bb_mid": 0.0, "bb_lower": 0.0,
            "macd": 0.0, "macd_signal": 0.0, "macd_histogram": 0.0,
            "volatility": 0.0,
            "atr": 0.0,
            "ema_fast": 0.0, "ema_slow": 0.0,
            "trend": "NEUTRAL",          # BULLISH | BEARISH | NEUTRAL
            "trend_strength": 0.0,       # 0.0 to 1.0
            "volume_sma": 0.0,
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
}

# --- PERSISTENCE ---
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                saved_data = json.load(f)
                state["portfolio"].update(saved_data.get("portfolio", {}))
                # Merge settings carefully - keep new defaults for keys not in saved
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
        serializable_grid = {
            k: v for k, v in state["grid_state"].items()
        }
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
    """Build a clean payload to send to the frontend (avoids sending huge arrays)."""
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
    save_state()
    await manager.broadcast_state()


def calculate_fees(amount_usd):
    """Coinbase Advanced Trade taker fee."""
    return round(float(amount_usd * state["settings"]["fee_rate"]), 4)


def calculate_atr(prices, period=14):
    """Average True Range from close prices (approximation without high/low)."""
    if len(prices) < period + 1:
        return 0.0
    diffs = np.abs(np.diff(prices[-(period + 1):]))
    return float(np.mean(diffs))


def calculate_stoch_rsi(prices, rsi_period=14, stoch_period=14):
    """Stochastic RSI for overbought/oversold with more sensitivity than RSI."""
    if len(prices) < rsi_period + stoch_period:
        return 50.0

    delta = np.diff(prices)
    gains = np.where(delta > 0, delta, 0.0)
    losses = np.where(delta < 0, -delta, 0.0)

    # Calculate rolling RSI values
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


def calculate_all_indicators(prices):
    """Full indicator suite for dashboard + trading decisions."""
    if len(prices) < 30:
        return state["indicators"]["BTC/USD"]

    arr = np.array(prices, dtype=float)

    # --- RSI (Wilder's smoothed) ---
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

    # --- Bollinger Bands (20-period) ---
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

    # --- EMAs for trend (9 fast, 21 slow) ---
    ema_fast = float(s.ewm(span=9, adjust=False).mean().iloc[-1])
    ema_slow = float(s.ewm(span=21, adjust=False).mean().iloc[-1])

    # --- Trend determination ---
    if ema_fast > ema_slow * 1.001:
        trend = "BULLISH"
        trend_strength = min((ema_fast - ema_slow) / ema_slow * 100, 1.0)
    elif ema_fast < ema_slow * 0.999:
        trend = "BEARISH"
        trend_strength = min((ema_slow - ema_fast) / ema_slow * 100, 1.0)
    else:
        trend = "NEUTRAL"
        trend_strength = 0.0

    # --- ATR ---
    atr = calculate_atr(arr)

    # --- Volatility (mean absolute change of recent ticks) ---
    volatility = float(np.mean(np.abs(np.diff(arr[-15:])))) if len(arr) >= 15 else 0.0

    # --- Stochastic RSI ---
    stoch_rsi = calculate_stoch_rsi(arr)

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
        "trend": trend,
        "trend_strength": float(trend_strength),
        "stoch_rsi": float(stoch_rsi),
        "volume_sma": 0.0,  # Placeholder until we add volume data
    }

# --- ADAPTIVE GRID ENGINE ---

def compute_dynamic_grid_range(current_price, atr):
    """Auto-calculate grid range from ATR so the grid always surrounds the price."""
    multiplier = state["settings"]["atr_multiplier"]
    half_range = atr * multiplier
    # Ensure minimum range of 1% of price
    min_range = current_price * 0.01
    half_range = max(half_range, min_range)
    return current_price - half_range, current_price + half_range


def initialize_grid(current_price):
    """Calculate price levels for the grid. Can use auto-range or manual bounds."""
    settings = state["settings"]
    atr = state["indicators"]["BTC/USD"].get("atr", 0)

    if settings["auto_range"] and atr > 0:
        lower, upper = compute_dynamic_grid_range(current_price, atr)
    else:
        upper = settings["grid_upper"] if settings["grid_upper"] > 0 else current_price * 1.03
        lower = settings["grid_lower"] if settings["grid_lower"] > 0 else current_price * 0.97

    # Safety: ensure valid range
    if upper <= lower:
        upper = current_price * 1.03
        lower = current_price * 0.97

    levels_count = settings["grid_levels"]
    grid_array = np.linspace(lower, upper, levels_count).tolist()
    state["grid_state"]["levels"] = grid_array
    state["grid_state"]["range_high"] = upper
    state["grid_state"]["range_low"] = lower

    # Find closest grid line to current price
    closest_idx = min(range(len(grid_array)), key=lambda i: abs(grid_array[i] - current_price))
    state["grid_state"]["current_index"] = closest_idx
    state["grid_state"]["initialized"] = True
    state["grid_state"]["last_rebalance"] = time.time()

    step_size = grid_array[1] - grid_array[0] if len(grid_array) > 1 else 0
    logger.info(f"🕸️ Grid Active: {levels_count} levels from ${lower:.2f}-${upper:.2f}. Step: ${step_size:.2f}")


def should_rebalance_grid(current_price):
    """Check if the grid needs to be recentered around the price."""
    gs = state["grid_state"]
    if not gs["initialized"] or not state["settings"]["auto_range"]:
        return False

    now = time.time()
    interval = state["settings"]["rebalance_interval"]
    if now - gs["last_rebalance"] < interval:
        return False

    # Rebalance if price is in the outer 15% of the grid range
    grid_range = gs["range_high"] - gs["range_low"]
    if grid_range <= 0:
        return True
    position_in_range = (current_price - gs["range_low"]) / grid_range

    if position_in_range < 0.15 or position_in_range > 0.85:
        return True

    return False


def get_trade_size(current_price):
    """Scale trade size based on volatility and position in Bollinger Bands."""
    base_size = state["settings"]["trade_size_usd"]
    if not state["settings"]["volatility_scaling"]:
        return base_size

    ind = state["indicators"]["BTC/USD"]
    bb_upper = ind.get("bb_upper", 0)
    bb_lower = ind.get("bb_lower", 0)
    bb_mid = ind.get("bb_mid", 0)

    if bb_upper <= bb_lower or bb_mid == 0:
        return base_size

    # Scale up when price is near BB extremes (mean-reversion opportunity)
    bb_range = bb_upper - bb_lower
    distance_from_mid = abs(current_price - bb_mid)
    bb_position = distance_from_mid / (bb_range / 2) if bb_range > 0 else 0

    # 1.0x at middle, up to 2.0x at BB edges
    scale_factor = 1.0 + min(bb_position, 1.0)

    # Also scale with volatility: higher vol = slightly larger trades (more profit per grid)
    atr = ind.get("atr", 0)
    if atr > 0 and bb_mid > 0:
        vol_ratio = atr / bb_mid * 1000  # Normalized
        vol_scale = min(1.0 + vol_ratio * 0.5, 1.5)
        scale_factor *= vol_scale

    scaled_size = base_size * scale_factor
    return min(scaled_size, state["settings"]["max_trade_size_usd"])


def check_cooldown(level_idx):
    """Prevent whipsaw: enforce minimum time between trades on the same grid level."""
    last_time = state["grid_state"]["last_trade_time"].get(str(level_idx), 0)
    return (time.time() - last_time) >= state["settings"]["cooldown_seconds"]


def check_trend_filter(action):
    """Use EMA crossover + MACD to filter trades with the trend."""
    if not state["settings"]["trend_filter"]:
        return True

    ind = state["indicators"]["BTC/USD"]
    trend = ind.get("trend", "NEUTRAL")
    macd_hist = ind.get("macd_histogram", 0)
    stoch_rsi = ind.get("stoch_rsi", 50)

    if action == "BUY":
        # Allow buys when: trend is not strongly bearish, OR stoch RSI is oversold
        if trend == "BEARISH" and stoch_rsi > 25:
            return False  # Don't catch falling knives
        return True

    elif action == "SELL":
        # Allow sells when: trend is not strongly bullish, OR stoch RSI is overbought
        if trend == "BULLISH" and stoch_rsi < 75:
            return False  # Don't sell into a pump
        return True

    return True


def check_circuit_breaker():
    """Stop trading if drawdown exceeds limit."""
    cb = state["circuit_breaker"]

    # If already active, check if cooldown expired (5 min recovery window)
    if cb["active"]:
        if time.time() > cb["cooldown_until"]:
            cb["active"] = False
            cb["reason"] = ""
            logger.info("🟢 Circuit breaker reset. Trading resumed.")
            return False
        return True

    # Check drawdown
    total_value = state["portfolio"]["total_value"]
    initial = state["portfolio"]["initial_balance"]
    peak = state["stats"]["peak_value"]

    if initial <= 0:
        return False

    # Drawdown from peak
    if peak > 0:
        drawdown_from_peak = ((peak - total_value) / peak) * 100
    else:
        drawdown_from_peak = 0

    # Drawdown from initial
    drawdown_from_initial = ((initial - total_value) / initial) * 100

    max_dd = max(drawdown_from_peak, drawdown_from_initial)
    state["stats"]["max_drawdown"] = round(max_dd, 2)

    if max_dd >= state["settings"]["drawdown_limit_pct"]:
        cb["active"] = True
        cb["triggered_at"] = time.time()
        cb["cooldown_until"] = time.time() + 300  # 5 min pause
        cb["reason"] = f"Drawdown {max_dd:.1f}% exceeded {state['settings']['drawdown_limit_pct']}% limit"
        logger.warning(f"🔴 CIRCUIT BREAKER: {cb['reason']}")
        return True

    return False


def update_stats(action, pnl, trade_usd):
    """Track win/loss statistics for performance monitoring."""
    stats = state["stats"]
    stats["total_trades"] += 1
    stats["total_volume"] = round(stats["total_volume"] + trade_usd, 2)

    if action == "SELL" and pnl != 0:
        if pnl > 0:
            stats["winning_trades"] += 1
            stats["largest_win"] = max(stats["largest_win"], pnl)
        else:
            stats["losing_trades"] += 1
            stats["largest_loss"] = min(stats["largest_loss"], pnl)

    total = stats["winning_trades"] + stats["losing_trades"]
    stats["win_rate"] = round((stats["winning_trades"] / total * 100) if total > 0 else 0, 1)

    # Update peak value
    if state["portfolio"]["total_value"] > stats["peak_value"]:
        stats["peak_value"] = round(state["portfolio"]["total_value"], 2)


async def execute_trade(symbol, action, current_price, trade_usd_amount, level_idx=None):
    """Execute a trade with proper fee calculation and stats tracking."""
    slippage = current_price * state["settings"]["slippage_rate"]
    exec_price = current_price + slippage if action == "BUY" else current_price - slippage

    pnl = 0.0

    if action == "BUY":
        if state["portfolio"]["cash"] < trade_usd_amount:
            return False

        # Check max open positions
        if len(state["grid_state"]["filled_levels"]) >= state["settings"]["max_open_positions"]:
            return False

        fee = calculate_fees(trade_usd_amount)
        qty = round(float((trade_usd_amount - fee) / exec_price), 8)

        state["portfolio"]["cash"] = round(state["portfolio"]["cash"] - trade_usd_amount, 2)
        state["portfolio"]["total_fees"] = round(state["portfolio"]["total_fees"] + fee, 4)
        state["portfolio"]["holdings"][symbol] = round(state["portfolio"]["holdings"][symbol] + qty, 8)

        # Weighted average cost basis
        old_qty = state["portfolio"]["holdings"][symbol] - qty
        old_cost = old_qty * state["portfolio"]["cost_basis"].get(symbol, 0)
        new_cost = qty * exec_price
        total_qty = state["portfolio"]["holdings"][symbol]
        if total_qty > 0:
            state["portfolio"]["cost_basis"][symbol] = round((old_cost + new_cost) / total_qty, 2)

        # Track this fill at the grid level
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
        return True

    elif action == "SELL":
        # Try to sell from a specific grid level fill
        fill = None
        fill_key = None
        if level_idx is not None:
            fill_key = str(level_idx)
            fill = state["grid_state"]["filled_levels"].get(fill_key)

        if fill:
            qty_to_sell = fill["qty"]
            entry_cost = fill["cost"]
        else:
            # Fallback: sell equivalent USD worth
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

        # Remove filled level
        if fill_key and fill_key in state["grid_state"]["filled_levels"]:
            del state["grid_state"]["filled_levels"][fill_key]

        if level_idx is not None:
            state["grid_state"]["last_trade_time"][str(level_idx)] = time.time()

        update_stats("SELL", pnl, sale_value)
        pnl_emoji = "✅" if pnl >= 0 else "❌"
        await log_event(f"🤝 SELL ${sale_value:.2f} @ ${exec_price:.2f} (Fee: ${fee:.4f}) P&L: {pnl_emoji}${pnl:.2f} [Grid #{level_idx}]")
        return True

    return False


async def evaluate_grid(symbol, price):
    """
    The core grid strategy with all enhancements:
    - Trend filtering
    - Cooldown protection
    - Volatility-scaled sizing
    - Dynamic grid rebalancing
    - Circuit breaker
    """
    # Circuit breaker check
    if check_circuit_breaker():
        return

    gs = state["grid_state"]

    # Initialize or rebalance grid
    if not gs["initialized"]:
        initialize_grid(price)
        await log_event(f"🕸️ Grid initialized: ${gs['range_low']:.0f} - ${gs['range_high']:.0f} ({len(gs['levels'])} levels)")
        return

    if should_rebalance_grid(price):
        # Preserve filled levels relative positions before rebalancing
        old_levels = gs["levels"]
        initialize_grid(price)
        await log_event(f"🔄 Grid rebalanced around ${price:.0f}: ${gs['range_low']:.0f} - ${gs['range_high']:.0f}")
        return

    levels = gs["levels"]
    curr_idx = gs["current_index"]

    if not levels or curr_idx < 0 or curr_idx >= len(levels):
        initialize_grid(price)
        return

    trade_size = get_trade_size(price)

    # Check multiple levels (in case price jumped past several in one tick)
    trades_this_tick = 0
    max_trades_per_tick = 3  # Cap to prevent burst trading

    # --- Price moved UP: check for SELL signals ---
    while curr_idx < len(levels) - 1 and trades_this_tick < max_trades_per_tick:
        level_above = levels[curr_idx + 1]
        if price < level_above:
            break

        next_idx = curr_idx + 1

        # Check if we have a fill at or below this level to sell
        sell_level = None
        for filled_idx_str in list(state["grid_state"]["filled_levels"].keys()):
            filled_idx = int(filled_idx_str)
            if filled_idx <= curr_idx:  # Sell fills from lower levels
                sell_level = filled_idx
                break

        if sell_level is not None:
            if check_cooldown(next_idx) and check_trend_filter("SELL"):
                success = await execute_trade(symbol, "SELL", price, trade_size, level_idx=sell_level)
                if success:
                    trades_this_tick += 1

        state["grid_state"]["current_index"] = next_idx
        curr_idx = next_idx

    # --- Price moved DOWN: check for BUY signals ---
    curr_idx = state["grid_state"]["current_index"]  # Refresh after potential changes
    while curr_idx > 0 and trades_this_tick < max_trades_per_tick:
        level_below = levels[curr_idx - 1]
        if price > level_below:
            break

        next_idx = curr_idx - 1

        # Only buy if we don't already have a fill at this level
        if str(next_idx) not in state["grid_state"]["filled_levels"]:
            if check_cooldown(next_idx) and check_trend_filter("BUY"):
                if state["portfolio"]["cash"] >= trade_size:
                    success = await execute_trade(symbol, "BUY", price, trade_size, level_idx=next_idx)
                    if success:
                        trades_this_tick += 1

        state["grid_state"]["current_index"] = next_idx
        curr_idx = next_idx

async def process_price_update(symbol, price):
    price = float(price)
    state["prices"][symbol] = price

    now = time.time()

    # Update 1-min candle history
    if now - state.get("last_history_update", 0) >= 60:
        state["history"][symbol].append(price)
        if len(state["history"][symbol]) > 200:
            state["history"][symbol].pop(0)
        state["last_history_update"] = now
    else:
        if len(state["history"][symbol]) > 0:
            state["history"][symbol][-1] = price

    # Update 5-min candle history
    if now - state.get("last_5m_update", 0) >= 300:
        state["history_5m"][symbol].append(price)
        if len(state["history_5m"][symbol]) > 100:
            state["history_5m"][symbol].pop(0)
        state["last_5m_update"] = now
    else:
        if len(state["history_5m"][symbol]) > 0:
            state["history_5m"][symbol][-1] = price

    # Recalculate indicators
    state["indicators"][symbol] = calculate_all_indicators(state["history"][symbol])

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

    # Track peak value for drawdown calc
    if state["portfolio"]["total_value"] > state["stats"].get("peak_value", 0):
        state["stats"]["peak_value"] = round(state["portfolio"]["total_value"], 2)

    # Run the grid engine
    await evaluate_grid(symbol, price)
    await manager.broadcast_state()

# --- DATA WARMUP & STREAM ---
async def warmup_indicators(internal_symbol="BTC/USD"):
    await log_event(f"🔥 WARMING UP INDICATORS FOR {internal_symbol} (Coinbase)...")
    url = "https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=60"
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers=HEADERS) as response:
                if response.status == 200:
                    data = await response.json()
                    closes = [float(candle[4]) for candle in data]
                    closes.reverse()
                    state["history"][internal_symbol] = closes[-100:]
                    state["last_history_update"] = time.time()
                    state["indicators"][internal_symbol] = calculate_all_indicators(state["history"][internal_symbol])
                else:
                    logger.warning(f"Warmup failed: HTTP {response.status}")
        except Exception as e:
            logger.error(f"Warmup connection error: {e}")

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
        except: pass

async def stream_live_crypto():
    load_state() 
    await asyncio.sleep(1)
    await asyncio.gather(fetch_macro_context(), warmup_indicators())
    
    await log_event("📡 STARTING LIVE COINBASE DATA STREAM (GRID ACTIVE)...")
    url = "https://api.exchange.coinbase.com/products/BTC-USD/ticker"
    
    backoff = 2
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(url, headers=HEADERS) as response:
                    if response.status == 200:
                        data = await response.json()
                        await process_price_update("BTC/USD", float(data['price']))
                        backoff = 2 
                    elif response.status == 429:
                        await log_event(f"⚠️ Exchange Rate Limit Hit. Backing off for {backoff}s...")
                        await asyncio.sleep(backoff)
                        backoff = min(backoff * 2, 60)
            except Exception as e:
                logger.error(f"Stream error: {e}")
                await asyncio.sleep(backoff)
                
            await asyncio.sleep(2)

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
                    
                    # If upper/lower grid bounds change, force a grid re-initialization
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