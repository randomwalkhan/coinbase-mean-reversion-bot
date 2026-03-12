from __future__ import annotations

import argparse
import logging
import time
import uuid
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

from coinbase_bot.config import PerpBotConfig, load_perp_config
from coinbase_bot.exchange import CoinbaseAdvancedClient, floor_to_increment
from coinbase_bot.perp_strategy import build_perp_signal_frame, evaluate_perp_entry
from coinbase_bot.state import BotState, OpenPosition, utc_now


LOGGER = logging.getLogger("coinbase_perp_bot")


def _setup_logging(mode: str) -> Path:
    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"perp_{mode}.log"

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=1_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    root_logger.addHandler(stream_handler)
    root_logger.addHandler(file_handler)
    return log_path


def _extract_order_id(payload: dict) -> str | None:
    for path in [
        ("success_response", "order_id"),
        ("order_id",),
        ("order", "order_id"),
    ]:
        cursor = payload
        try:
            for key in path:
                cursor = cursor[key]
            if cursor:
                return str(cursor)
        except (KeyError, TypeError):
            continue
    return None


def _format_indicators(indicators: dict[str, float]) -> str:
    if not indicators:
        return ""
    parts = []
    for key, value in indicators.items():
        if abs(value) >= 100:
            parts.append(f"{key}={value:.2f}")
        else:
            parts.append(f"{key}={value:.3f}")
    return " | " + ", ".join(parts)


def _extract_amount(payload: object) -> float:
    if isinstance(payload, dict):
        if "value" in payload:
            return float(payload.get("value", 0.0))
        return 0.0
    if payload is None:
        return 0.0
    return float(payload)


def _state_path_for_mode(base_path: Path, live: bool) -> Path:
    if live:
        return base_path
    return base_path.with_name("perp_dry_run_state.json")


def _resolve_portfolio_uuid(client: CoinbaseAdvancedClient, config: PerpBotConfig) -> str | None:
    if config.portfolio_uuid:
        return config.portfolio_uuid
    permissions = client.get_key_permissions()
    raw = permissions.get("portfolio_uuid")
    return str(raw) if raw else None


def _load_portfolio_summary(client: CoinbaseAdvancedClient, portfolio_uuid: str) -> tuple[float, float]:
    payload = client.get_perps_portfolio_summary(portfolio_uuid)
    summary = payload.get("summary", {})
    total_balance = _extract_amount(summary.get("total_balance"))
    buying_power = _extract_amount(summary.get("buying_power"))
    return total_balance, buying_power


def _position_map(client: CoinbaseAdvancedClient, portfolio_uuid: str) -> dict[str, dict]:
    positions = client.list_perps_positions(portfolio_uuid)
    mapping: dict[str, dict] = {}
    for position in positions:
        product_id = str(position.get("product_id") or position.get("symbol") or "").upper()
        if not product_id:
            continue
        try:
            net_size = abs(float(position.get("net_size", 0.0)))
        except (TypeError, ValueError):
            net_size = 0.0
        if net_size <= 0:
            continue
        mapping[product_id] = position
    return mapping


def _close_dry_run_position(state: BotState, position: OpenPosition, exit_price: float, reason: str) -> None:
    pnl = position.planned_quote_size * ((exit_price - position.entry_price) / position.entry_price)
    state.close_position(position.product_id, pnl, utc_now())
    LOGGER.info(
        "Perp dry-run exit %s at %.2f (%s), pnl %.2f",
        position.product_id,
        exit_price,
        reason,
        pnl,
    )


def _reconcile_dry_run_position(state: BotState, position: OpenPosition, signal_frame) -> bool:
    entry_time = datetime.fromisoformat(position.entry_time)
    since_entry = signal_frame[signal_frame["start"] >= entry_time]
    if since_entry.empty:
        return False

    for _, candle in since_entry.iterrows():
        if candle["low"] <= position.stop_price:
            _close_dry_run_position(state, position, position.stop_price, "stop_hit")
            return True
        if candle["high"] >= position.take_profit_price:
            _close_dry_run_position(state, position, position.take_profit_price, "take_profit_hit")
            return True

    if position.close_deadline:
        deadline = datetime.fromisoformat(position.close_deadline)
        latest_time = signal_frame.iloc[-1]["start"].to_pydatetime()
        if latest_time >= deadline:
            _close_dry_run_position(state, position, float(signal_frame.iloc[-1]["close"]), "max_hold")
            return True

    return False


def _should_exit_live(position: OpenPosition, current_price: float, now: datetime) -> str | None:
    if current_price <= position.stop_price:
        return "stop_hit"
    if current_price >= position.take_profit_price:
        return "take_profit_hit"
    if position.close_deadline and now >= datetime.fromisoformat(position.close_deadline):
        return "max_hold"
    return None


def run_cycle(config: PerpBotConfig, live: bool) -> None:
    state_path = _state_path_for_mode(config.state_path, live)
    state = BotState.load(state_path)
    client = CoinbaseAdvancedClient(
        api_key=config.api_key,
        api_secret=config.api_secret,
        require_auth=True,
    )
    now = utc_now()

    if not config.enabled:
        LOGGER.info("Perp bot is disabled via PERP_BOT_ENABLED")
        return

    portfolio_uuid = _resolve_portfolio_uuid(client, config)
    if not portfolio_uuid:
        LOGGER.warning("No perpetuals portfolio uuid available; skipping perp cycle")
        return

    try:
        total_balance, buying_power = _load_portfolio_summary(client, portfolio_uuid)
        exchange_positions = _position_map(client, portfolio_uuid)
    except Exception as exc:
        LOGGER.warning("Perp portfolio is not ready for API trading: %s", exc)
        return

    spot_cash_equivalent = client.get_available_balance("USD") + client.get_available_balance("USDC")
    account_cash_equivalent = spot_cash_equivalent + total_balance

    for product_id in config.product_ids:
        try:
            product = client.get_product(product_id)
            candles = client.fetch_candles(product_id, config.granularity, config.lookback_candles)
            signal_frame = build_perp_signal_frame(candles)
            state_position = state.position_for(product_id)
            exchange_position = exchange_positions.get(product_id)

            if state_position is not None or exchange_position is not None:
                if live and state_position is not None and exchange_position is not None:
                    current_price = _extract_amount(exchange_position.get("mark_price")) or float(product.price)
                    exit_reason = _should_exit_live(state_position, current_price, now)
                    if exit_reason is None:
                        LOGGER.info("Perp position already open for %s, skipping new entry", product_id)
                        continue

                    base_size_value = abs(float(exchange_position.get("net_size", 0.0)))
                    if base_size_value <= 0:
                        LOGGER.warning("Perp position for %s has no size; keeping state unchanged", product_id)
                        continue

                    base_size = floor_to_increment(base_size_value, product.base_increment)
                    if config.preview_live_orders:
                        preview = client.preview_market_order(
                            product_id=product_id,
                            side="SELL",
                            base_size=base_size,
                            leverage=str(config.leverage),
                            margin_type=config.margin_type,
                            retail_portfolio_id=portfolio_uuid,
                        )
                        LOGGER.info("Perp preview accepted for %s exit: %s", product_id, preview)
                    exit_client_order_id = str(uuid.uuid4())
                    response = client.create_market_order(
                        client_order_id=exit_client_order_id,
                        product_id=product_id,
                        side="SELL",
                        base_size=base_size,
                        leverage=str(config.leverage),
                        margin_type=config.margin_type,
                        retail_portfolio_id=portfolio_uuid,
                    )
                    pnl = state_position.planned_quote_size * (
                        (current_price - state_position.entry_price) / state_position.entry_price
                    )
                    state.close_position(product_id, pnl, now)
                    LOGGER.info("Closed live perp %s at %.2f (%s): %s", product_id, current_price, exit_reason, response)
                    continue

                if live and state_position is not None and exchange_position is None:
                    state.close_position(product_id, 0.0, now)
                    LOGGER.info("Perp state for %s was cleared because no exchange position remains", product_id)
                    continue

                if live and state_position is None and exchange_position is not None:
                    LOGGER.warning("Exchange has a live perp position for %s without local state; skipping", product_id)
                    continue

                if not live and state_position is not None:
                    was_closed = _reconcile_dry_run_position(state, state_position, signal_frame)
                    if not was_closed:
                        LOGGER.info("Perp dry-run position already open for %s, skipping new entry", product_id)
                    continue

            if state.open_position_count() >= config.max_open_positions:
                LOGGER.info("Perp max open positions reached, skipping %s", product_id)
                continue

            decision = evaluate_perp_entry(
                product_id=product_id,
                signal_frame=signal_frame,
                leverage=config.leverage,
                reference_price=product.price,
            )
            if decision.action != "BUY":
                LOGGER.info("%s: %s%s", product_id, decision.reason, _format_indicators(decision.indicators))
                continue

            target_notional = min(account_cash_equivalent * config.per_trade_notional_fraction, buying_power)
            if target_notional < max(config.min_quote_order_size, product.quote_min_size):
                LOGGER.info("%s: insufficient perp buying power for a new trade", product_id)
                continue

            quote_size = floor_to_increment(target_notional, product.quote_increment)
            client_order_id = str(uuid.uuid4())
            if live:
                if not config.allow_live_trading:
                    raise RuntimeError("COINBASE_ALLOW_PERP_LIVE_TRADING is false")
                if config.preview_live_orders:
                    preview = client.preview_market_order(
                        product_id=product_id,
                        side="BUY",
                        quote_size=quote_size,
                        leverage=str(config.leverage),
                        margin_type=config.margin_type,
                        retail_portfolio_id=portfolio_uuid,
                    )
                    LOGGER.info("Perp preview accepted for %s entry: %s", product_id, preview)
                response = client.create_market_order(
                    client_order_id=client_order_id,
                    product_id=product_id,
                    side="BUY",
                    quote_size=quote_size,
                    leverage=str(config.leverage),
                    margin_type=config.margin_type,
                    retail_portfolio_id=portfolio_uuid,
                )
                order_id = _extract_order_id(response)
                LOGGER.info("Live perp BUY submitted for %s: %s", product_id, response)
            else:
                order_id = None
                LOGGER.info(
                    "Perp dry-run BUY %s size %s leverage %.2f strategy %s",
                    product_id,
                    quote_size,
                    config.leverage,
                    decision.strategy_name,
                )

            state.open_position(
                OpenPosition(
                    product_id=product_id,
                    entry_time=now.isoformat(),
                    entry_price=float(decision.entry_price),
                    stop_price=float(decision.stop_price),
                    take_profit_price=float(decision.take_profit_price),
                    planned_quote_size=float(quote_size),
                    client_order_id=client_order_id,
                    exchange_order_id=order_id,
                    dry_run=not live,
                    close_deadline=(
                        now + timedelta(seconds=config.granularity_seconds * (16 if product_id.startswith("ETH-") else 20))
                    ).isoformat(),
                    side="LONG",
                    market_type="PERP",
                )
            )
            LOGGER.info(
                "Opened %s perp position on %s at %.2f, tp %.2f, stop %.2f, leverage %.2f, strategy %s",
                "live" if live else "dry-run",
                product_id,
                decision.entry_price,
                decision.take_profit_price,
                decision.stop_price,
                config.leverage,
                decision.strategy_name,
            )
        except Exception as exc:
            LOGGER.exception("Failed processing perp %s: %s", product_id, exc)

    state.save(state_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Coinbase perp trend pullback bot.")
    parser.add_argument("--mode", choices=["dry-run", "live"], default="dry-run")
    parser.add_argument("--loop", action="store_true", help="Run continuously instead of a single cycle.")
    parser.add_argument("--sleep-seconds", type=int, default=300, help="Delay between cycles when --loop is enabled.")
    args = parser.parse_args()

    log_path = _setup_logging(args.mode)
    LOGGER.info("Logging to %s", log_path)

    config = load_perp_config()
    if args.loop:
        while True:
            run_cycle(config, live=args.mode == "live")
            LOGGER.info("Sleeping for %s seconds before the next cycle", args.sleep_seconds)
            time.sleep(args.sleep_seconds)
    else:
        run_cycle(config, live=args.mode == "live")


if __name__ == "__main__":
    main()
