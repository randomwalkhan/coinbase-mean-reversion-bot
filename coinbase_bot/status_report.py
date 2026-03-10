from __future__ import annotations

import argparse
import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from coinbase_bot.config import load_config
from coinbase_bot.exchange import CoinbaseAdvancedClient
from coinbase_bot.state import BotState


LOGGER = logging.getLogger("coinbase_bot.status")


def _normalize_phone_number(raw: str) -> str:
    digits = "".join(character for character in raw if character.isdigit())
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return raw.strip()


def _tail_scan_results(log_path: Path, product_ids: list[str]) -> dict[str, str]:
    if not log_path.exists():
        return {}

    latest: dict[str, str] = {}
    lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()[-300:]
    for line in lines:
        for product_id in product_ids:
            marker = f"{product_id}: "
            if marker in line:
                latest[product_id] = line.split(marker, 1)[1].strip()
    return latest


def _latest_fill_summary(client: CoinbaseAdvancedClient, product_id: str) -> str:
    fills = client.get_fills(product_id, limit=5)
    if not fills:
        return "none"

    fill = fills[0]
    side = str(fill.get("side", "")).upper() or "?"
    size = float(fill.get("size", 0.0))
    price = float(fill.get("price", 0.0))
    trade_time = str(fill.get("trade_time") or fill.get("trade_time_iso") or fill.get("sequence_timestamp") or "")
    trade_time = trade_time.replace("T", " ").replace("Z", "")
    trade_time = trade_time[:16] if trade_time else "unknown"
    return f"{side} {size:.6f} @ {price:.2f} ({trade_time})"


def _format_positions(state: BotState) -> str:
    if not state.positions:
        return "none"

    rows = []
    for product_id, position in state.positions.items():
        rows.append(
            f"{product_id} @ {position.entry_price:.2f} "
            f"tp {position.take_profit_price:.2f} "
            f"sl {position.stop_price:.2f}"
        )
    return "; ".join(rows)


def _build_report() -> str:
    load_dotenv()
    config = load_config()
    client = CoinbaseAdvancedClient(require_auth=True)
    state = BotState.load(Path("state/live_state.json"))

    status_products_raw = os.getenv("STATUS_REPORT_PRODUCTS", "BTC-USD,ETH-USD")
    status_products = [item.strip().upper() for item in status_products_raw.split(",") if item.strip()]
    tracked_currencies = sorted({config.quote_currency, *[product.split("-")[0] for product in status_products]})
    balances = client.get_balances(tracked_currencies)

    prices = []
    for product_id in status_products:
        product = client.get_product(product_id)
        prices.append(f"{product_id} {product.price:,.2f}")

    scan_results = _tail_scan_results(Path("logs/live.log"), status_products)

    lines = [
        f"Coinbase bot {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "Prices: " + " | ".join(prices),
        "Balances: " + " | ".join(
            f"{currency} {balances.get(currency, 0.0):,.6f}" if currency != config.quote_currency else
            f"{currency} {balances.get(currency, 0.0):,.2f}"
            for currency in tracked_currencies
        ),
        f"Open positions: {_format_positions(state)}",
        f"Today realized PnL: {state.realized_pnl_by_day.get(datetime.now().date().isoformat(), 0.0):,.2f}",
        "Latest scan: " + " | ".join(
            f"{product_id} {scan_results.get(product_id, 'no recent scan line')}" for product_id in status_products
        ),
        "Recent fills: " + " | ".join(
            f"{product_id} {_latest_fill_summary(client, product_id)}" for product_id in status_products
        ),
    ]
    return "\n".join(lines)


def send_imessage(target: str, message: str) -> None:
    normalized_target = _normalize_phone_number(target)
    script = """
on run argv
    set targetHandle to item 1 of argv
    set messageText to item 2 of argv
    tell application "Messages"
        set targetService to 1st service whose service type = iMessage
        set targetBuddy to buddy targetHandle of targetService
        send messageText to targetBuddy
    end tell
end run
"""
    subprocess.run(
        ["osascript", "-", normalized_target, message],
        input=script,
        text=True,
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Send a Coinbase bot status report over iMessage.")
    parser.add_argument("--no-send", action="store_true", help="Print the message instead of sending it.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    message = _build_report()
    if args.no_send:
        print(message)
        return

    target = os.getenv("IMESSAGE_TARGET", "").strip()
    if not target:
        raise SystemExit("Missing IMESSAGE_TARGET in .env")

    send_imessage(target, message)
    LOGGER.info("Status report sent to %s", _normalize_phone_number(target))


if __name__ == "__main__":
    main()
