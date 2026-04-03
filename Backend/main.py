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
logger = logging.getLogger("TrendBot")

HEADERS = {"User-Agent": "AlgoBot/1.0"}
STATE_FILE = "portfolio.json"

# Coinbase Advanced Trade API credentials
CB_API_KEY_NAME = os.getenv("COINBASE_API_KEY_NAME", "")
CB_PRIVATE_KEY  = os.getenv("COINBASE_PRIVATE_KEY", "").replace("\\n", "\n")
CB_REST_URL     = "https://api.coinbase.com"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

async def tg_notify(message: str):
    """Send a Telegram message notification."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        async with aiohttp.ClientSession() as session:
            await session.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"})
    except Exception as e:
        logger.error(f"Telegram notify failed: {e}")

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
        "live_trading": False,
        "live_order_type": "market",
        "daily_loss_limit_usd": 20.0,
        "trend_stop_loss_pct": 3.0,
        "trend_take_profit_pct": 6.0,
        "trend_position_size_pct": 0.8,
        "trend_min_strength": 0.3,
        "trend_cooldown_seconds": 300,
        "trend_allocation_usd": 1000.0,
        "fee_rate": 0.006,
        "slippage_rate": 0.0005,
        "drawdown_limit_pct": 15.0,
    },
    "prices": {"BTC/USD": 0.0},
    "history": {"BTC/USD": []},          # 1-min closes
    "volume_history": {"BTC/USD": []},   # 1-min volumes
    "history_5m": {"BTC/USD": []},       # 5-min candles
    "volume_5m": {"BTC/USD": []},
    "indicators": {
        "BTC/USD": {
            "rsi": 50.0,
            "bb_upper": 0.0, "bb_mid": 0.0, "bb_lower": 0.0,
            "macd": 0.0, "macd_signal": 0.0, "macd_histogram": 0.0,
            "volatility": 0.0,
            "atr": 0.0,
            "ema_fast": 0.0, "ema_slow": 0.0,
            "trend": "NEUTRAL",
            "trend_strength": 0.0,
            "trend_5m": "NEUTRAL",
            "trend_strength_5m": 0.0,
            "mtf_agreement": False,
            "volume_sma": 0.0,
            "volume_ratio": 1.0,
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
        "initial_balance": 1000.00,
        "cash": 1000.00,
        "total_value": 1000.00,
        "total_profit": 0.00,
        "total_fees": 0.00,
        "trend_holdings": 0.0,
        "trend_cost_basis": 0.0,
        "trend_realized_pnl": 0.0,
        "trend_unrealized_pnl": 0.0,
    },
    "trend_state": {
        "position": None,
        "last_trade_time": 0,
        "signal": "NONE",
        "consecutive_signals": 0,
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
    "current_volume_tick": 0.0,
    "current_volume_5m_tick": 0.0,
    "tick_count": 0,
    "tick_count_5m": 0,
    "tick_history": [],
    "tick_history_5m": [],
    "price_chart": [],
    "equity_history": [],
    "trade_markers": [],
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
                pf = saved_data.get("portfolio", {})
                # Migrate old split-cash portfolios
                if "trend_holdings" not in pf and "trend_cash" in pf:
                    pf["cash"] = pf.get("trend_cash", 500.0) + pf.get("grid_cash", 500.0)
                state["portfolio"].update(pf)
                # Remove legacy keys
                for legacy in ("grid_cash", "trend_cash", "holdings", "cost_basis", "unrealized_pnl", "realized_pnl"):
                    state["portfolio"].pop(legacy, None)

                saved_settings = saved_data.get("settings", {})
                for k, v in saved_settings.items():
                    if k in state["settings"]:
                        state["settings"][k] = v
                state["stats"].update(saved_data.get("stats", {}))
                saved_trend = saved_data.get("trend_state", {})
                for k, v in saved_trend.items():
                    state["trend_state"][k] = v
            logger.info("💾 Loaded previous portfolio, settings & stats from disk.")
        except Exception as e:
            logger.error(f"Failed to load state: {e}")

def save_state():
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump({
                "portfolio": state["portfolio"],
                "settings": state["settings"],
                "stats": state["stats"],
                "trend_state": state["trend_state"],
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
                if c in self.active_connections:
                    self.active_connections.remove(c)


def get_broadcast_payload():
    ts = state["trend_state"]
    return {
        "prices": state["prices"],
        "portfolio": state["portfolio"],
        "indicators": state["indicators"],
        "macro": state["macro"],
        "logs": state["logs"],
        "settings": state["settings"],
        "stats": state["stats"],
        "circuit_breaker": state["circuit_breaker"],
        "price_chart": state["price_chart"][-10080:],
        "equity_history": state["equity_history"][-10080:],
        "trade_markers": state["trade_markers"][-500:],
        "trend_state": {
            "signal": ts["signal"],
            "consecutive_signals": ts["consecutive_signals"],
            "position": ts["position"],
        },
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
    """Returns (trend_str, trend_strength, ema_fast, ema_slow) from a price series."""
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
    if len(volumes) < 2:
        return 0.0, 1.0
    arr = np.array(volumes[-period:], dtype=float)
    sma = float(np.mean(arr))
    current = float(volumes[-1]) if volumes else 0.0
    ratio = current / sma if sma > 0 else 1.0
    return round(sma, 4), round(ratio, 3)


def calculate_all_indicators(prices, volumes=None):
    if len(prices) < 30:
        return state["indicators"]["BTC/USD"]

    arr = np.array(prices, dtype=float)

    # RSI
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

    # Bollinger Bands
    recent20 = arr[-20:]
    sma20 = float(np.mean(recent20))
    std20 = float(np.std(recent20))
    bb_upper = sma20 + (2.0 * std20)
    bb_lower = sma20 - (2.0 * std20)

    # MACD
    s = pd.Series(arr)
    ema12 = s.ewm(span=12, adjust=False).mean()
    ema26 = s.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    macd_signal = macd_line.ewm(span=9, adjust=False).mean()
    macd_histogram = macd_line - macd_signal

    # 1m Trend
    trend_1m, strength_1m, ema_fast, ema_slow = calculate_trend(arr)

    # 5m Trend
    prices_5m = state["history_5m"].get("BTC/USD", [])
    if len(prices_5m) >= 10:
        trend_5m, strength_5m, _, _ = calculate_trend(np.array(prices_5m, dtype=float))
    else:
        trend_5m, strength_5m = "NEUTRAL", 0.0

    mtf_agreement = (trend_1m == trend_5m) and trend_1m != "NEUTRAL"

    # ATR
    atr = calculate_atr(arr)

    # Volatility
    volatility = float(np.mean(np.abs(np.diff(arr[-15:])))) if len(arr) >= 15 else 0.0

    # Stochastic RSI
    stoch_rsi = calculate_stoch_rsi(arr)

    # Volume
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
        asyncio.create_task(tg_notify(f"🚨 <b>CIRCUIT BREAKER</b>\n{cb['reason']}\nTrading paused for 5 minutes."))
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
            n = stats["winning_trades"]
            stats["avg_win"] = round(((stats["avg_win"] * (n - 1)) + pnl) / n, 4)
        else:
            stats["losing_trades"] += 1
            stats["largest_loss"] = min(stats["largest_loss"], pnl)
            n = stats["losing_trades"]
            stats["avg_loss"] = round(((stats["avg_loss"] * (n - 1)) + pnl) / n, 4)

    total = stats["winning_trades"] + stats["losing_trades"]
    stats["win_rate"] = round((stats["winning_trades"] / total * 100) if total > 0 else 0, 1)

    gross_wins = stats["avg_win"] * stats["winning_trades"] if stats["winning_trades"] > 0 else 0
    gross_losses = abs(stats["avg_loss"]) * stats["losing_trades"] if stats["losing_trades"] > 0 else 0
    stats["profit_factor"] = round(gross_wins / gross_losses, 2) if gross_losses > 0 else (float("inf") if gross_wins > 0 else 0.0)

    if state["portfolio"]["total_value"] > stats["peak_value"]:
        stats["peak_value"] = round(state["portfolio"]["total_value"], 2)


# ── Trend Trading Engine ──────────────────────────────────────────────────────

def get_trend_signal():
    ind = state["indicators"]["BTC/USD"]
    trend_1m = ind.get("trend", "NEUTRAL")
    trend_5m = ind.get("trend_5m", "NEUTRAL")
    mtf_agreement = ind.get("mtf_agreement", False)
    strength = ind.get("trend_strength", 0.0)
    rsi = ind.get("rsi", 50.0)
    macd_hist = ind.get("macd_histogram", 0.0)
    min_strength = state["settings"].get("trend_min_strength", 0.3)
    in_position = state["trend_state"]["position"] is not None

    if in_position:
        if (trend_1m == "BEARISH" or
                (trend_5m == "BEARISH" and not mtf_agreement) or
                rsi > 78 or
                macd_hist < 0):
            return "EXIT"

    if (trend_1m == "BULLISH" and
            trend_5m == "BULLISH" and
            mtf_agreement and
            strength >= min_strength and
            rsi < 70 and
            macd_hist > 0):
        return "LONG"

    return "NONE"


async def evaluate_trend(symbol, price):
    if check_circuit_breaker():
        return

    ts = state["trend_state"]
    settings = state["settings"]
    portfolio = state["portfolio"]
    live = settings.get("live_trading", False)

    signal = get_trend_signal()
    ts["signal"] = signal

    now = time.time()
    position = ts["position"]

    # Update trailing stop and unrealized PnL if in position
    if position:
        if price > position["high_water_mark"]:
            position["high_water_mark"] = price
            trail_stop = position["high_water_mark"] * (1 - 1.5 / 100)
            position["stop_loss"] = max(position["stop_loss"], trail_stop)

        portfolio["trend_unrealized_pnl"] = round(
            (price - position["entry_price"]) * position["qty"], 2
        )

        # Check exit conditions
        exit_reason = None
        if price <= position["stop_loss"]:
            exit_reason = "Stop Loss 🛑"
        elif price >= position["take_profit"]:
            exit_reason = "Take Profit 🎯"
        elif signal == "EXIT":
            exit_reason = "Signal 📉"

        if exit_reason:
            qty = position["qty"]
            cost = position["cost"]

            if live:
                if check_daily_loss_limit():
                    await log_event("🛑 Daily loss limit — SELL blocked.")
                    return
                try:
                    order = await cb_place_order("SELL", base_size=qty)
                    order_id = order.get("order_id", "unknown")
                    await asyncio.sleep(1.5)
                    accounts = await cb_get_account()
                    portfolio["cash"] = round(accounts["USD"], 2)
                    portfolio["trend_holdings"] = round(accounts["BTC"], 8)
                    fee = calculate_fees(qty * price)
                    sale_value = round(qty * price - fee, 2)
                    pnl = round(sale_value - cost, 2)
                    portfolio["total_fees"] = round(portfolio["total_fees"] + fee, 4)
                    portfolio["trend_realized_pnl"] = round(portfolio.get("trend_realized_pnl", 0.0) + pnl, 2)
                    portfolio["trend_unrealized_pnl"] = 0.0
                    ts["position"] = None
                    ts["last_trade_time"] = now
                    pnl_emoji = "✅" if pnl >= 0 else "❌"
                    await log_event(f"📉 LIVE TREND EXIT ({exit_reason}) @ ${price:.2f} | P&L: {pnl_emoji}${pnl:.2f} | Order: {order_id[:8]}…")
                    await tg_notify(f"📉 <b>LIVE TREND EXIT</b> ({exit_reason})\n@ ${price:.2f} | P&L: {pnl_emoji} ${pnl:.2f}\nTotal Trend P&L: ${portfolio['trend_realized_pnl']:.2f}")
                    update_stats("SELL", pnl, qty * price)
                    state["trade_markers"].append({"time": datetime.now().strftime("%m/%d %H:%M"), "action": "SELL", "price": round(price, 2)})
                except Exception as e:
                    await log_event(f"❌ LIVE SELL FAILED: {e}")
                return

            # Paper exit
            fee = round(qty * price * settings["fee_rate"], 4)
            sale_value = round(qty * price - fee, 2)
            pnl = round(sale_value - cost, 2)

            portfolio["cash"] = round(portfolio["cash"] + sale_value, 2)
            portfolio["trend_holdings"] = round(max(0.0, portfolio.get("trend_holdings", 0.0) - qty), 8)
            portfolio["trend_realized_pnl"] = round(portfolio.get("trend_realized_pnl", 0.0) + pnl, 2)
            portfolio["total_fees"] = round(portfolio["total_fees"] + fee, 4)
            portfolio["trend_unrealized_pnl"] = 0.0

            ts["position"] = None
            ts["last_trade_time"] = now

            pnl_emoji = "✅" if pnl >= 0 else "❌"
            await log_event(f"📉 TREND EXIT ({exit_reason}) @ ${price:.2f} | P&L: {pnl_emoji}${pnl:.2f} | Total Trend P&L: ${portfolio['trend_realized_pnl']:.2f}")
            await tg_notify(f"📉 <b>TREND EXIT</b> ({exit_reason})\n@ ${price:.2f} | P&L: {pnl_emoji} ${pnl:.2f}\nTotal Trend P&L: ${portfolio['trend_realized_pnl']:.2f}")
            update_stats("SELL", pnl, qty * price)
            state["trade_markers"].append({"time": datetime.now().strftime("%m/%d %H:%M"), "action": "SELL", "price": round(price, 2)})
            return

    # Entry logic
    if signal == "LONG" and position is None:
        cooldown = settings.get("trend_cooldown_seconds", 300)
        if now - ts["last_trade_time"] < cooldown:
            return

        ts["consecutive_signals"] = ts.get("consecutive_signals", 0) + 1
        if ts["consecutive_signals"] < 2:
            return

        trade_usd = settings["trend_allocation_usd"] * settings["trend_position_size_pct"]
        trade_usd = min(trade_usd, portfolio["cash"])
        if trade_usd < 10:
            return

        if live:
            if check_daily_loss_limit():
                await log_event("🛑 Daily loss limit — BUY blocked.")
                return
            try:
                order = await cb_place_order("BUY", quote_size=trade_usd)
                order_id = order.get("order_id", "unknown")
                await asyncio.sleep(1.5)
                accounts = await cb_get_account()
                portfolio["cash"] = round(accounts["USD"], 2)
                portfolio["trend_holdings"] = round(accounts["BTC"], 8)
                fee = calculate_fees(trade_usd)
                qty = round((trade_usd - fee) / price, 8)
                portfolio["total_fees"] = round(portfolio["total_fees"] + fee, 4)
                stop_loss = round(price * (1 - settings["trend_stop_loss_pct"] / 100), 2)
                take_profit = round(price * (1 + settings["trend_take_profit_pct"] / 100), 2)
                ts["position"] = {
                    "entry_price": price, "qty": qty, "cost": trade_usd,
                    "stop_loss": stop_loss, "take_profit": take_profit,
                    "entry_time": now, "high_water_mark": price,
                }
                ts["last_trade_time"] = now
                ts["consecutive_signals"] = 0
                await log_event(f"📈 LIVE TREND BUY ${trade_usd:.2f} @ ${price:.2f} | SL: ${stop_loss:.2f} | TP: ${take_profit:.2f} | Order: {order_id[:8]}…")
                await tg_notify(f"📈 <b>LIVE TREND BUY</b> ${trade_usd:.2f} @ ${price:.2f}\nSL: ${stop_loss:.2f} | TP: ${take_profit:.2f}")
                update_stats("BUY", 0, trade_usd)
                state["trade_markers"].append({"time": datetime.now().strftime("%m/%d %H:%M"), "action": "BUY", "price": round(price, 2)})
            except Exception as e:
                await log_event(f"❌ LIVE BUY FAILED: {e}")
            return

        # Paper entry
        fee = round(trade_usd * settings["fee_rate"], 4)
        qty = round((trade_usd - fee) / price, 8)
        stop_loss = round(price * (1 - settings["trend_stop_loss_pct"] / 100), 2)
        take_profit = round(price * (1 + settings["trend_take_profit_pct"] / 100), 2)

        portfolio["cash"] = round(portfolio["cash"] - trade_usd, 2)
        portfolio["trend_holdings"] = round(portfolio.get("trend_holdings", 0.0) + qty, 8)
        portfolio["total_fees"] = round(portfolio["total_fees"] + fee, 4)

        ts["position"] = {
            "entry_price": price, "qty": qty, "cost": trade_usd,
            "stop_loss": stop_loss, "take_profit": take_profit,
            "entry_time": now, "high_water_mark": price,
        }
        ts["last_trade_time"] = now
        ts["consecutive_signals"] = 0

        await log_event(f"📈 TREND BUY ${trade_usd:.2f} @ ${price:.2f} | SL: ${stop_loss:.2f} | TP: ${take_profit:.2f}")
        await tg_notify(f"📈 <b>TREND BUY</b> ${trade_usd:.2f} @ ${price:.2f}\nSL: ${stop_loss:.2f} | TP: ${take_profit:.2f}")
        update_stats("BUY", 0, trade_usd)
        state["trade_markers"].append({"time": datetime.now().strftime("%m/%d %H:%M"), "action": "BUY", "price": round(price, 2)})
    else:
        if signal != "LONG":
            ts["consecutive_signals"] = 0


async def process_price_update(symbol, price, volume=0.0):
    price = float(price)
    volume = float(volume)
    state["prices"][symbol] = price

    now = time.time()

    state["current_volume_tick"] = state.get("current_volume_tick", 0.0) + volume
    state["current_volume_5m_tick"] = state.get("current_volume_5m_tick", 0.0) + volume

    state["tick_count"] = state.get("tick_count", 0) + 1
    state["tick_count_5m"] = state.get("tick_count_5m", 0) + 1

    # Update 1-min candle history
    if now - state.get("last_history_update", 0) >= 60:
        state["history"][symbol].append(price)
        state["volume_history"][symbol].append(state["current_volume_tick"])
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

    # Tick-based volume ratio
    tick_hist = state.get("tick_history", [])
    if len(tick_hist) >= 3:
        avg_ticks = float(np.mean(tick_hist[-20:]))
        current_ticks = float(state.get("tick_count", 0))
        tick_vol_ratio = (current_ticks / avg_ticks) if avg_ticks > 0 else 1.0
    else:
        tick_vol_ratio = 1.0

    candle_closed = (now - state.get("last_history_update", 0)) < 2.0 and len(state["history"][symbol]) > 0

    # Recalculate indicators
    state["indicators"][symbol] = calculate_all_indicators(
        state["history"][symbol],
        volumes=state["volume_history"][symbol]
    )
    state["indicators"][symbol]["volume_ratio"] = round(tick_vol_ratio, 3)

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

        save_state()

    # Portfolio value tracking
    trend_h_val = round(float(state["portfolio"].get("trend_holdings", 0.0) * price), 2)
    state["portfolio"]["total_value"] = round(float(state["portfolio"]["cash"] + trend_h_val), 2)
    state["portfolio"]["total_profit"] = round(float(state["portfolio"]["total_value"] - state["portfolio"]["initial_balance"]), 2)

    if state["portfolio"]["total_value"] > state["stats"].get("peak_value", 0):
        state["stats"]["peak_value"] = round(state["portfolio"]["total_value"], 2)

    await evaluate_trend(symbol, price)
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
                    backoff = 2

                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                data = json.loads(msg.data)
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

                    # Boolean toggles
                    if "live_trading" in payload:
                        state["settings"]["live_trading"] = bool(payload["live_trading"])

                    # Numeric settings
                    for key in (
                        "trend_stop_loss_pct", "trend_take_profit_pct",
                        "trend_position_size_pct", "trend_min_strength",
                        "trend_allocation_usd", "trend_cooldown_seconds",
                        "daily_loss_limit_usd",
                    ):
                        if key in payload:
                            state["settings"][key] = float(payload[key])

                    save_state()
                    await log_event(f"⚙️ Settings Updated")
                    await manager.broadcast_state()
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        manager.disconnect(ws)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
