"""
backtest.py — Grid Bot Backtester
===================================
Fetches historical OHLCV data from Coinbase and simulates the adaptive grid
strategy with volume + multi-timeframe filters. Outputs a full performance report.

Usage:
    python backtest.py [--days 30] [--levels 30] [--atr-mult 3.0] [--trade-size 10]

Requirements:
    pip install aiohttp numpy pandas tabulate
"""

import asyncio
import argparse
import json
import time
from datetime import datetime, timezone
import aiohttp
import numpy as np
import pandas as pd

try:
    from tabulate import tabulate
    HAS_TABULATE = True
except ImportError:
    HAS_TABULATE = False

HEADERS = {"User-Agent": "AlgoBot-Backtest/1.0"}
COINBASE_CANDLES = "https://api.exchange.coinbase.com/products/BTC-USD/candles"


# ── Fetch historical candles ──────────────────────────────────────────────────

async def fetch_candles(granularity: int, days: int) -> pd.DataFrame:
    """
    Fetch up to `days` days of candles at `granularity` seconds per candle.
    Coinbase returns max 300 candles per request, so we page backwards.
    Columns: time, low, high, open, close, volume
    """
    end_ts = int(time.time())
    start_ts = end_ts - (days * 86400)
    all_candles = []

    async with aiohttp.ClientSession() as session:
        cursor = end_ts
        while cursor > start_ts:
            window_start = max(cursor - 300 * granularity, start_ts)
            params = {
                "granularity": granularity,
                "start": datetime.fromtimestamp(window_start, tz=timezone.utc).isoformat(),
                "end": datetime.fromtimestamp(cursor, tz=timezone.utc).isoformat(),
            }
            try:
                async with session.get(COINBASE_CANDLES, params=params, headers=HEADERS) as r:
                    if r.status == 200:
                        data = await r.json()
                        if not data:
                            break
                        all_candles.extend(data)
                        cursor = int(data[-1][0]) - granularity
                    elif r.status == 429:
                        await asyncio.sleep(5)
                        continue
                    else:
                        print(f"HTTP {r.status} fetching candles")
                        break
            except Exception as e:
                print(f"Fetch error: {e}")
                break
            await asyncio.sleep(0.3)  # respect rate limits

    if not all_candles:
        return pd.DataFrame()

    df = pd.DataFrame(all_candles, columns=["time", "low", "high", "open", "close", "volume"])
    df = df.drop_duplicates("time").sort_values("time").reset_index(drop=True)
    df = df[df["time"] >= start_ts]
    for col in ["low", "high", "open", "close", "volume"]:
        df[col] = df[col].astype(float)
    return df


# ── Indicators (same logic as main.py, vectorised for speed) ─────────────────

def compute_ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def compute_indicators_series(df: pd.DataFrame) -> pd.DataFrame:
    """Add indicator columns to a candle DataFrame."""
    c = df["close"]
    v = df["volume"]

    # EMAs
    df["ema9"]  = compute_ema(c, 9)
    df["ema21"] = compute_ema(c, 21)
    df["ema12"] = compute_ema(c, 12)
    df["ema26"] = compute_ema(c, 26)

    # MACD
    df["macd"]        = df["ema12"] - df["ema26"]
    df["macd_signal"] = compute_ema(df["macd"], 9)
    df["macd_hist"]   = df["macd"] - df["macd_signal"]

    # Bollinger Bands (20)
    df["bb_mid"]   = c.rolling(20).mean()
    df["bb_std"]   = c.rolling(20).std()
    df["bb_upper"] = df["bb_mid"] + 2 * df["bb_std"]
    df["bb_lower"] = df["bb_mid"] - 2 * df["bb_std"]

    # RSI (14)
    delta   = c.diff()
    gain    = delta.clip(lower=0)
    loss    = (-delta).clip(lower=0)
    avg_g   = gain.rolling(14).mean()
    avg_l   = loss.rolling(14).mean()
    rs      = avg_g / avg_l.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))
    df["rsi"] = df["rsi"].fillna(50)

    # ATR (14) — true range from OHLC
    df["tr"]  = np.maximum(df["high"] - df["low"],
                np.maximum(abs(df["high"] - c.shift(1)),
                           abs(df["low"]  - c.shift(1))))
    df["atr"] = df["tr"].rolling(14).mean()

    # Trend classification (1m timeframe)
    df["trend"] = "NEUTRAL"
    df.loc[df["ema9"] > df["ema21"] * 1.001, "trend"] = "BULLISH"
    df.loc[df["ema9"] < df["ema21"] * 0.999, "trend"] = "BEARISH"

    # Volume SMA (20) and ratio
    df["vol_sma"]   = v.rolling(20).mean()
    df["vol_ratio"] = v / df["vol_sma"].replace(0, np.nan)
    df["vol_ratio"] = df["vol_ratio"].fillna(1.0)

    return df


def add_5m_trend(df_1m: pd.DataFrame, df_5m: pd.DataFrame) -> pd.DataFrame:
    """Merge 5m EMA trend into the 1m DataFrame."""
    df_5m = df_5m.copy()
    df_5m["ema9_5m"]  = compute_ema(df_5m["close"], 9)
    df_5m["ema21_5m"] = compute_ema(df_5m["close"], 21)
    df_5m["trend_5m"] = "NEUTRAL"
    df_5m.loc[df_5m["ema9_5m"] > df_5m["ema21_5m"] * 1.001, "trend_5m"] = "BULLISH"
    df_5m.loc[df_5m["ema9_5m"] < df_5m["ema21_5m"] * 0.999, "trend_5m"] = "BEARISH"

    # Forward-fill 5m trend into 1m bars using merge_asof
    df_5m_trim = df_5m[["time", "trend_5m"]].rename(columns={"time": "time_5m"})
    df_1m = df_1m.sort_values("time")
    df_5m_trim = df_5m_trim.sort_values("time_5m")

    df_merged = pd.merge_asof(
        df_1m,
        df_5m_trim,
        left_on="time",
        right_on="time_5m",
        direction="backward"
    )
    df_merged["trend_5m"] = df_merged["trend_5m"].fillna("NEUTRAL")
    return df_merged


# ── Grid simulation ───────────────────────────────────────────────────────────

class GridBacktest:
    def __init__(self, config: dict):
        self.config = config
        self.cash = config["initial_balance"]
        self.holdings = 0.0
        self.cost_basis = 0.0
        self.total_fees = 0.0
        self.realized_pnl = 0.0
        self.peak_value = config["initial_balance"]
        self.max_drawdown = 0.0
        self.circuit_breaker_until = 0

        self.grid_levels = []
        self.filled_levels: dict[int, dict] = {}
        self.last_trade_time: dict[int, float] = {}
        self.current_index = -1

        self.trades = []
        self.equity_curve = []

    # ── Grid ──────────────────────────────────────────────────────────────────

    def init_grid(self, price: float, atr: float):
        mult = self.config["atr_multiplier"]
        half = max(atr * mult, price * 0.01)
        lower, upper = price - half, price + half
        n = self.config["grid_levels"]
        self.grid_levels = np.linspace(lower, upper, n).tolist()
        self.current_index = min(range(n), key=lambda i: abs(self.grid_levels[i] - price))

    def should_rebalance(self, price: float) -> bool:
        if not self.grid_levels:
            return False
        lo, hi = self.grid_levels[0], self.grid_levels[-1]
        rng = hi - lo
        if rng <= 0:
            return True
        pos = (price - lo) / rng
        return pos < 0.15 or pos > 0.85

    # ── Filters ───────────────────────────────────────────────────────────────

    def _trend_filter(self, action: str, row) -> bool:
        if not self.config["trend_filter"]:
            return True

        trend_1m  = row.get("trend", "NEUTRAL")
        trend_5m  = row.get("trend_5m", "NEUTRAL")
        stoch_rsi = row.get("rsi", 50)          # using RSI as proxy
        vol_ratio = row.get("vol_ratio", 1.0)

        if self.config.get("volume_filter") and vol_ratio < 0.6:
            return False

        if action == "BUY":
            if trend_1m == "BEARISH" and trend_5m == "BEARISH" and stoch_rsi > 35:
                return False
            if trend_1m == "BEARISH" and stoch_rsi > 35:
                return False
        elif action == "SELL":
            if trend_1m == "BULLISH" and trend_5m == "BULLISH" and stoch_rsi < 65:
                return False
            if trend_1m == "BULLISH" and stoch_rsi < 65:
                return False
        return True

    def _check_cooldown(self, level_idx: int, ts: float) -> bool:
        last = self.last_trade_time.get(level_idx, 0)
        return (ts - last) >= self.config["cooldown_seconds"]

    def _check_circuit_breaker(self, ts: float, total_value: float) -> bool:
        if ts < self.circuit_breaker_until:
            return True
        initial = self.config["initial_balance"]
        dd_from_peak = (self.peak_value - total_value) / self.peak_value * 100 if self.peak_value > 0 else 0
        dd_from_init = (initial - total_value) / initial * 100
        max_dd = max(dd_from_peak, dd_from_init)
        if max_dd > self.config["drawdown_limit_pct"]:
            self.circuit_breaker_until = ts + 300
            return True
        return False

    # ── Trade execution ───────────────────────────────────────────────────────

    def _get_trade_size(self, price: float, row) -> float:
        base = self.config["trade_size_usd"]
        if not self.config.get("volatility_scaling", True):
            return base
        bb_upper = row.get("bb_upper", price)
        bb_lower = row.get("bb_lower", price)
        bb_mid   = row.get("bb_mid", price)
        if bb_upper <= bb_lower or bb_mid == 0:
            return base
        bb_range = bb_upper - bb_lower
        dist = abs(price - bb_mid)
        pos = dist / (bb_range / 2) if bb_range > 0 else 0
        scale = 1.0 + min(pos, 1.0)
        return min(base * scale, self.config["max_trade_size_usd"])

    def _execute_buy(self, price: float, trade_usd: float, level_idx: int, ts: float):
        if self.cash < trade_usd:
            return False
        if len(self.filled_levels) >= self.config["max_open_positions"]:
            return False
        slip = price * self.config["slippage_rate"]
        exec_price = price + slip
        fee = trade_usd * self.config["fee_rate"]
        qty = (trade_usd - fee) / exec_price
        self.cash -= trade_usd
        self.total_fees += fee
        self.holdings += qty
        old_qty = self.holdings - qty
        self.cost_basis = ((old_qty * self.cost_basis) + (qty * exec_price)) / self.holdings if self.holdings > 0 else exec_price
        self.filled_levels[level_idx] = {"qty": qty, "cost": trade_usd, "entry": exec_price}
        self.last_trade_time[level_idx] = ts
        self.trades.append({"ts": ts, "action": "BUY", "price": exec_price, "usd": trade_usd, "fee": fee, "pnl": 0})
        return True

    def _execute_sell(self, price: float, level_idx: int, ts: float):
        fill = self.filled_levels.get(level_idx)
        if not fill:
            return False
        if self.holdings < fill["qty"]:
            return False
        slip = price * self.config["slippage_rate"]
        exec_price = price - slip
        sale_value = fill["qty"] * exec_price
        fee = sale_value * self.config["fee_rate"]
        net = sale_value - fee
        pnl = net - fill["cost"]
        self.cash += net
        self.total_fees += fee
        self.holdings -= fill["qty"]
        self.holdings = max(self.holdings, 0.0)
        self.realized_pnl += pnl
        del self.filled_levels[level_idx]
        self.last_trade_time[level_idx] = ts
        self.trades.append({"ts": ts, "action": "SELL", "price": exec_price, "usd": sale_value, "fee": fee, "pnl": pnl})
        return True

    # ── Main simulation loop ──────────────────────────────────────────────────

    def run(self, df: pd.DataFrame) -> dict:
        df = df.reset_index(drop=True)
        grid_initialized = False
        last_rebalance = 0

        for i, row in df.iterrows():
            price = float(row["close"])
            ts    = float(row["time"])
            atr   = float(row.get("atr", price * 0.005)) or price * 0.005

            total_value = self.cash + self.holdings * price

            # Equity curve
            self.equity_curve.append({"ts": ts, "value": total_value, "price": price})

            # Peak / drawdown
            if total_value > self.peak_value:
                self.peak_value = total_value
            dd = (self.peak_value - total_value) / self.peak_value * 100 if self.peak_value > 0 else 0
            if dd > self.max_drawdown:
                self.max_drawdown = dd

            # Circuit breaker
            if self._check_circuit_breaker(ts, total_value):
                continue

            # Init / rebalance grid
            if not grid_initialized:
                self.init_grid(price, atr)
                grid_initialized = True
                last_rebalance = ts
                continue

            if (ts - last_rebalance >= self.config["rebalance_interval"] and
                    self.should_rebalance(price)):
                self.init_grid(price, atr)
                last_rebalance = ts
                continue

            if not self.grid_levels or self.current_index < 0:
                continue

            trade_size = self._get_trade_size(price, row)
            trades_this_bar = 0

            # Price up → SELL
            while self.current_index < len(self.grid_levels) - 1 and trades_this_bar < 3:
                if price < self.grid_levels[self.current_index + 1]:
                    break
                next_idx = self.current_index + 1
                sell_lvl = next((int(k) for k in self.filled_levels if int(k) <= self.current_index), None)
                if sell_lvl is not None:
                    if self._check_cooldown(next_idx, ts) and self._trend_filter("SELL", row):
                        if self._execute_sell(price, sell_lvl, ts):
                            trades_this_bar += 1
                self.current_index = next_idx

            # Price down → BUY
            while self.current_index > 0 and trades_this_bar < 3:
                if price > self.grid_levels[self.current_index - 1]:
                    break
                next_idx = self.current_index - 1
                if next_idx not in self.filled_levels:
                    if self._check_cooldown(next_idx, ts) and self._trend_filter("BUY", row):
                        if self._execute_buy(price, trade_size, next_idx, ts):
                            trades_this_bar += 1
                self.current_index = next_idx

        # Final liquidation at last price
        final_price = float(df.iloc[-1]["close"])
        final_value = self.cash + self.holdings * final_price
        return self._build_report(df, final_price, final_value)

    def _build_report(self, df: pd.DataFrame, final_price: float, final_value: float) -> dict:
        initial = self.config["initial_balance"]
        trades_df = pd.DataFrame(self.trades)
        sells = trades_df[trades_df["action"] == "SELL"] if not trades_df.empty else pd.DataFrame()

        wins   = sells[sells["pnl"] > 0]["pnl"] if not sells.empty else pd.Series(dtype=float)
        losses = sells[sells["pnl"] < 0]["pnl"] if not sells.empty else pd.Series(dtype=float)

        total_pnl  = final_value - initial
        total_ret  = total_pnl / initial * 100
        n_trades   = len(trades_df)
        n_sells    = len(sells)
        win_rate   = len(wins) / n_sells * 100 if n_sells > 0 else 0
        avg_win    = float(wins.mean()) if not wins.empty else 0
        avg_loss   = float(losses.mean()) if not losses.empty else 0
        profit_fac = abs(wins.sum() / losses.sum()) if not losses.empty and losses.sum() != 0 else float("inf")

        equity_df = pd.DataFrame(self.equity_curve)
        if not equity_df.empty:
            equity_df["ts"] = pd.to_datetime(equity_df["ts"], unit="s")

        days = (df["time"].iloc[-1] - df["time"].iloc[0]) / 86400
        daily_ret = (final_value / initial) ** (1 / max(days, 1)) - 1 if days > 0 else 0
        annualized = (1 + daily_ret) ** 365 - 1

        return {
            "summary": {
                "Initial Balance":  f"${initial:.2f}",
                "Final Value":      f"${final_value:.2f}",
                "Total P&L":        f"${total_pnl:.2f}",
                "Total Return":     f"{total_ret:.2f}%",
                "Annualized Return":f"{annualized * 100:.1f}%",
                "Max Drawdown":     f"{self.max_drawdown:.2f}%",
                "Total Fees":       f"${self.total_fees:.2f}",
                "Realized P&L":     f"${self.realized_pnl:.2f}",
                "Days Backtested":  f"{days:.1f}",
            },
            "trade_stats": {
                "Total Trades":     n_trades,
                "Sell Trades":      n_sells,
                "Win Rate":         f"{win_rate:.1f}%",
                "Avg Win":          f"${avg_win:.3f}",
                "Avg Loss":         f"${avg_loss:.3f}",
                "Profit Factor":    f"{profit_fac:.2f}",
                "Largest Win":      f"${float(wins.max()):.3f}" if not wins.empty else "$0",
                "Largest Loss":     f"${float(losses.min()):.3f}" if not losses.empty else "$0",
            },
            "equity_curve": equity_df,
            "trades": trades_df,
        }


# ── Main entry point ─────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="BTC Grid Bot Backtester")
    parser.add_argument("--days",       type=int,   default=30,    help="Days of history (default 30)")
    parser.add_argument("--levels",     type=int,   default=30,    help="Grid levels (default 30)")
    parser.add_argument("--atr-mult",   type=float, default=3.0,   help="ATR multiplier for grid range (default 3.0)")
    parser.add_argument("--trade-size", type=float, default=10.0,  help="Base trade size USD (default 10)")
    parser.add_argument("--balance",    type=float, default=200.0, help="Starting balance (default 200)")
    parser.add_argument("--no-mtf",     action="store_true",       help="Disable multi-timeframe filter")
    parser.add_argument("--no-volume",  action="store_true",       help="Disable volume filter")
    parser.add_argument("--output",     type=str,   default=None,  help="Save trades to CSV file")
    args = parser.parse_args()

    config = {
        "initial_balance":    args.balance,
        "grid_levels":        args.levels,
        "atr_multiplier":     args.atr_mult,
        "trade_size_usd":     args.trade_size,
        "max_trade_size_usd": args.trade_size * 3,
        "fee_rate":           0.006,
        "slippage_rate":      0.0005,
        "cooldown_seconds":   30,
        "drawdown_limit_pct": 15.0,
        "rebalance_interval": 300,
        "max_open_positions": 10,
        "trend_filter":       True,
        "volume_filter":      not args.no_volume,
        "mtf_trend_filter":   not args.no_mtf,
        "volatility_scaling": True,
    }

    print(f"\n📊 Fetching {args.days} days of BTC/USD 1m candles...")
    df_1m = await fetch_candles(60, args.days)
    if df_1m.empty:
        print("❌ Failed to fetch 1m data.")
        return

    print(f"   {len(df_1m)} 1m candles loaded.")
    print(f"📊 Fetching 5m candles for multi-timeframe filter...")
    df_5m = await fetch_candles(300, args.days)
    print(f"   {len(df_5m)} 5m candles loaded.")

    print("⚙️  Computing indicators...")
    df_1m = compute_indicators_series(df_1m)

    if not df_5m.empty and not args.no_mtf:
        df_1m = add_5m_trend(df_1m, df_5m)
    else:
        df_1m["trend_5m"] = "NEUTRAL"

    # Drop NaN rows (indicator warmup period)
    df_1m = df_1m.dropna(subset=["atr", "bb_mid"]).reset_index(drop=True)
    print(f"   {len(df_1m)} bars after indicator warmup.\n")

    print(f"🚀 Running backtest...")
    bt = GridBacktest(config)
    result = bt.run(df_1m)

    # ── Print report ──────────────────────────────────────────────────────────
    print("\n" + "=" * 55)
    print("  BACKTEST RESULTS")
    print("=" * 55)
    print(f"  Strategy : Adaptive Grid (ATR x{args.atr_mult})")
    print(f"  Period   : {args.days} days | Grid levels: {args.levels}")
    print(f"  Filters  : MTF={'off' if args.no_mtf else 'on'} | Volume={'off' if args.no_volume else 'on'}")
    print("=" * 55)

    summary_rows = list(result["summary"].items())
    trade_rows   = list(result["trade_stats"].items())

    if HAS_TABULATE:
        print(tabulate(summary_rows, headers=["Metric", "Value"], tablefmt="simple"))
        print()
        print(tabulate(trade_rows, headers=["Trade Metric", "Value"], tablefmt="simple"))
    else:
        for k, v in summary_rows + trade_rows:
            print(f"  {k:<22} {v}")

    print("=" * 55 + "\n")

    if args.output:
        result["trades"].to_csv(args.output, index=False)
        print(f"✅ Trades saved to {args.output}")

    # Quick parameter suggestion
    total_ret = float(result["summary"]["Total Return"].replace("%", ""))
    max_dd    = float(result["summary"]["Max Drawdown"].replace("%", ""))
    if total_ret < 0:
        print("💡 Tip: Negative return. Try --atr-mult 2.0 or --no-volume to loosen filters.")
    elif max_dd > 10:
        print(f"💡 Tip: Drawdown {max_dd:.1f}% is high. Try --atr-mult 2.5 or reducing --levels.")
    else:
        print(f"✅ Solid results. You can tighten filters or increase --trade-size for more activity.")


if __name__ == "__main__":
    asyncio.run(main())
