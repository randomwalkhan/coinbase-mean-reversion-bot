from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from coinbase_bot.indicators import compute_adx, compute_atr, compute_rsi, normalize_candles


@dataclass(frozen=True)
class PerpSignalDecision:
    action: str
    reason: str
    entry_price: float | None = None
    stop_price: float | None = None
    take_profit_price: float | None = None
    leverage: float | None = None
    strategy_name: str | None = None
    indicators: dict[str, float] = field(default_factory=dict)


def _hold(reason: str, indicators: dict[str, float] | None = None) -> PerpSignalDecision:
    return PerpSignalDecision(action="HOLD", reason=reason, indicators=indicators or {})


def build_perp_signal_frame(candles: pd.DataFrame) -> pd.DataFrame:
    frame = normalize_candles(candles)
    frame["ema8"] = frame["close"].ewm(span=8, adjust=False).mean()
    frame["ema20"] = frame["close"].ewm(span=20, adjust=False).mean()
    frame["ema21"] = frame["close"].ewm(span=21, adjust=False).mean()
    frame["ema50"] = frame["close"].ewm(span=50, adjust=False).mean()
    frame["ema55"] = frame["close"].ewm(span=55, adjust=False).mean()
    frame["rsi"] = compute_rsi(frame["close"], 14)
    frame["adx"] = compute_adx(frame, 14)
    frame["atr"] = compute_atr(frame, 14)
    frame["atr_pct"] = frame["atr"] / frame["close"]

    volume_median = frame["volume"].rolling(20).median()
    frame["volume_ratio"] = frame["volume"] / volume_median.replace(0.0, pd.NA)

    one_hour = _trend_frame(frame, "1h")
    four_hour = _trend_frame(frame, "4h")

    frame = pd.merge_asof(
        frame.sort_values("start"),
        one_hour[["start", "trend_fast_1h", "trend_slow_1h", "trend_up_1h"]].sort_values("start"),
        on="start",
        direction="backward",
    )
    frame = pd.merge_asof(
        frame.sort_values("start"),
        four_hour[["start", "trend_fast_4h", "trend_slow_4h", "trend_up_4h"]].sort_values("start"),
        on="start",
        direction="backward",
    )
    return frame


def _trend_frame(frame: pd.DataFrame, rule: str) -> pd.DataFrame:
    label = rule.lower()
    resampled = (
        frame.set_index("start")
        .resample(rule)
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
        .reset_index()
    )
    resampled[f"trend_fast_{label}"] = resampled["close"].ewm(span=20, adjust=False).mean()
    resampled[f"trend_slow_{label}"] = resampled["close"].ewm(span=50, adjust=False).mean()
    resampled[f"trend_up_{label}"] = (
        resampled[f"trend_fast_{label}"] > resampled[f"trend_slow_{label}"]
    ).astype(float)
    return resampled


def evaluate_perp_entry(
    product_id: str,
    signal_frame: pd.DataFrame,
    leverage: float,
    reference_price: float | None = None,
) -> PerpSignalDecision:
    if signal_frame.empty:
        return _hold("no candles")

    latest = signal_frame.iloc[-1]
    if product_id == "BTC-PERP-INTX":
        return _evaluate_btc_pullback(latest, leverage, reference_price)
    if product_id == "ETH-PERP-INTX":
        return _evaluate_eth_pullback(latest, leverage, reference_price)
    return _hold("unsupported perp product")


def _evaluate_btc_pullback(
    latest: pd.Series,
    leverage: float,
    reference_price: float | None,
) -> PerpSignalDecision:
    required = [
        "close",
        "low",
        "ema20",
        "ema50",
        "rsi",
        "atr",
        "volume_ratio",
        "trend_fast_4h",
        "trend_slow_4h",
        "trend_up_4h",
    ]
    if latest[required].isna().any():
        return _hold("indicator warmup incomplete")

    entry_price = float(reference_price if reference_price is not None else latest["close"])
    indicators = {
        "close": float(latest["close"]),
        "low": float(latest["low"]),
        "ema20": float(latest["ema20"]),
        "ema50": float(latest["ema50"]),
        "rsi": float(latest["rsi"]),
        "atr_pct": float(latest["atr_pct"]),
        "volume_ratio": float(latest["volume_ratio"]),
        "trend_fast_4h": float(latest["trend_fast_4h"]),
        "trend_slow_4h": float(latest["trend_slow_4h"]),
    }

    if float(latest["trend_up_4h"]) < 1.0:
        return _hold("4h trend filter failed", indicators)
    if latest["close"] <= latest["ema50"]:
        return _hold("price is below the 15m anchor ema", indicators)
    if not (latest["low"] < latest["ema20"] and latest["close"] > latest["ema20"]):
        return _hold("pullback reclaim is not complete", indicators)
    if latest["rsi"] <= 52 or latest["rsi"] >= 66:
        return _hold("btc pullback momentum is not in range", indicators)
    if latest["volume_ratio"] < 0.9:
        return _hold("btc pullback volume filter failed", indicators)
    if entry_price > latest["close"] + (latest["atr"] * 0.25):
        return _hold("price already bounced too far after the pullback", indicators)

    stop_price = float(entry_price - (latest["atr"] * 1.0))
    take_profit_price = float(entry_price + (latest["atr"] * 1.6))
    return PerpSignalDecision(
        action="BUY",
        reason="btc 4h trend pullback matched",
        entry_price=entry_price,
        stop_price=stop_price,
        take_profit_price=take_profit_price,
        leverage=leverage,
        strategy_name="btc_4h_pullback",
        indicators=indicators,
    )


def _evaluate_eth_pullback(
    latest: pd.Series,
    leverage: float,
    reference_price: float | None,
) -> PerpSignalDecision:
    required = [
        "close",
        "low",
        "ema8",
        "ema21",
        "ema55",
        "rsi",
        "adx",
        "atr",
        "volume_ratio",
        "trend_fast_1h",
        "trend_slow_1h",
        "trend_up_1h",
    ]
    if latest[required].isna().any():
        return _hold("indicator warmup incomplete")

    entry_price = float(reference_price if reference_price is not None else latest["close"])
    indicators = {
        "close": float(latest["close"]),
        "low": float(latest["low"]),
        "ema8": float(latest["ema8"]),
        "ema21": float(latest["ema21"]),
        "ema55": float(latest["ema55"]),
        "rsi": float(latest["rsi"]),
        "adx": float(latest["adx"]),
        "atr_pct": float(latest["atr_pct"]),
        "volume_ratio": float(latest["volume_ratio"]),
        "trend_fast_1h": float(latest["trend_fast_1h"]),
        "trend_slow_1h": float(latest["trend_slow_1h"]),
    }

    if float(latest["trend_up_1h"]) < 1.0:
        return _hold("1h trend filter failed", indicators)
    if not (latest["ema8"] > latest["ema21"] > latest["ema55"]):
        return _hold("eth ema stack is not aligned", indicators)
    if latest["adx"] <= 22:
        return _hold("eth adx filter failed", indicators)
    if latest["rsi"] <= 50 or latest["rsi"] >= 63:
        return _hold("eth pullback momentum is not in range", indicators)
    if not (latest["low"] <= latest["ema21"] * 1.002 and latest["close"] > latest["ema21"]):
        return _hold("eth pullback reclaim is not complete", indicators)
    if latest["volume_ratio"] < 1.2:
        return _hold("eth pullback volume filter failed", indicators)
    if entry_price > latest["close"] + (latest["atr"] * 0.25):
        return _hold("price already bounced too far after the pullback", indicators)

    stop_price = float(entry_price - (latest["atr"] * 1.1))
    take_profit_price = float(entry_price + (latest["atr"] * 1.8))
    return PerpSignalDecision(
        action="BUY",
        reason="eth 1h trend pullback matched",
        entry_price=entry_price,
        stop_price=stop_price,
        take_profit_price=take_profit_price,
        leverage=leverage,
        strategy_name="eth_1h_pullback",
        indicators=indicators,
    )
