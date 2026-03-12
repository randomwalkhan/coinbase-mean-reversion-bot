from __future__ import annotations

import unittest

import pandas as pd

from coinbase_bot.perp_strategy import evaluate_perp_entry


class PerpStrategyTests(unittest.TestCase):
    def test_btc_pullback_entry_is_generated_for_valid_setup(self) -> None:
        frame = pd.DataFrame(
            [
                {
                    "close": 100.8,
                    "low": 99.8,
                    "ema20": 100.1,
                    "ema50": 99.5,
                    "rsi": 58.0,
                    "atr": 1.2,
                    "atr_pct": 0.012,
                    "volume_ratio": 1.1,
                    "trend_fast_4h": 105.0,
                    "trend_slow_4h": 102.0,
                    "trend_up_4h": 1.0,
                }
            ]
        )
        decision = evaluate_perp_entry("BTC-PERP-INTX", frame, leverage=2.0, reference_price=100.7)
        self.assertEqual(decision.action, "BUY")
        self.assertEqual(decision.strategy_name, "btc_4h_pullback")

    def test_eth_pullback_entry_is_blocked_when_volume_is_too_low(self) -> None:
        frame = pd.DataFrame(
            [
                {
                    "close": 100.6,
                    "low": 100.0,
                    "ema8": 101.0,
                    "ema21": 100.2,
                    "ema55": 99.4,
                    "rsi": 57.0,
                    "adx": 28.0,
                    "atr": 1.0,
                    "atr_pct": 0.01,
                    "volume_ratio": 0.9,
                    "trend_fast_1h": 103.0,
                    "trend_slow_1h": 101.0,
                    "trend_up_1h": 1.0,
                }
            ]
        )
        decision = evaluate_perp_entry("ETH-PERP-INTX", frame, leverage=2.0, reference_price=100.5)
        self.assertEqual(decision.action, "HOLD")
        self.assertIn("volume", decision.reason)

    def test_eth_pullback_entry_is_generated_for_valid_setup(self) -> None:
        frame = pd.DataFrame(
            [
                {
                    "close": 101.1,
                    "low": 100.3,
                    "ema8": 101.5,
                    "ema21": 100.9,
                    "ema55": 100.0,
                    "rsi": 56.0,
                    "adx": 27.0,
                    "atr": 1.1,
                    "atr_pct": 0.011,
                    "volume_ratio": 1.4,
                    "trend_fast_1h": 103.0,
                    "trend_slow_1h": 101.5,
                    "trend_up_1h": 1.0,
                }
            ]
        )
        decision = evaluate_perp_entry("ETH-PERP-INTX", frame, leverage=2.0, reference_price=101.0)
        self.assertEqual(decision.action, "BUY")
        self.assertEqual(decision.strategy_name, "eth_1h_pullback")


if __name__ == "__main__":
    unittest.main()
