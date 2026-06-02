"""
Signal notifier — pushes an AI trade decision to Telegram.

Reuses the existing trading_bot.TelegramSender. Token/chat are resolved in
priority order so it works with whatever you already have configured:

  1. SENTIMENT_TELEGRAM_BOT_TOKEN + SENTIMENT_TELEGRAM_CHAT_ID  (dedicated — best)
  2. FIREHOSE_BOT_TOKEN + FIREHOSE_CHANNEL_ID                   (private signal feed)
  3. BOT_TOKEN + CHANNEL_ID                                     (your main bot/channel)

Fail-safe: if nothing is configured or the send fails, returns False and the
engine keeps running. Notifications are NOT a trade — they describe an advisory
decision that was never auto-executed.
"""
from __future__ import annotations

import html
import os
from typing import Any, Dict, List, Optional, Tuple

_ACTION_EMOJI = {"LONG": "🟢", "SHORT": "🔴", "REDUCE": "🟠", "FLAT": "⚪"}


def _resolve() -> Tuple[Optional[str], Optional[str]]:
    """(token, chat_id) for the destination, by priority.

    An explicit SENTIMENT_TELEGRAM_CHAT_ID wins and is paired with the best
    available token (its own, else your main BOT_TOKEN, else firehose) — so you
    can target a specific group by ID without also pasting a token, as long as
    that bot is a member of the group. Then full sentiment override, then the
    firehose feed, then the main bot/channel.
    """
    sent_tok = os.environ.get("SENTIMENT_TELEGRAM_BOT_TOKEN")
    sent_chat = os.environ.get("SENTIMENT_TELEGRAM_CHAT_ID")

    cfg_main_tok = cfg_main_chat = cfg_fire_tok = cfg_fire_chat = None
    try:
        from trading_bot.config import CONFIG
        cfg_main_tok, cfg_main_chat = CONFIG.TELEGRAM_BOT_TOKEN, CONFIG.CHANNEL_ID
        cfg_fire_tok, cfg_fire_chat = CONFIG.FIREHOSE_BOT_TOKEN, CONFIG.FIREHOSE_CHANNEL_ID
    except Exception:
        pass

    if sent_chat:                       # explicit target group/chat by ID
        token = sent_tok or cfg_main_tok or cfg_fire_tok
        if token:
            return token, sent_chat
    if sent_tok and sent_chat:
        return sent_tok, sent_chat
    if cfg_fire_tok and cfg_fire_chat:
        return cfg_fire_tok, cfg_fire_chat
    if cfg_main_tok and cfg_main_chat:
        return cfg_main_tok, cfg_main_chat
    return None, None


def is_noteworthy(record: Dict[str, Any]) -> bool:
    """Only ping for actionable calls — skip plain FLAT chop (still logged)."""
    action = (record.get("decision") or "").upper()
    if action in ("LONG", "SHORT", "REDUCE"):
        return True
    return bool(record.get("override_reason"))  # FLAT only if it's an override


def format_signal(record: Dict[str, Any], reasons: Optional[List[str]] = None) -> str:
    a = (record.get("decision") or "FLAT").upper()
    emoji = _ACTION_EMOJI.get(a, "⚪")

    def esc(x: Any) -> str:
        return html.escape(str(x))

    def num(x: Any) -> str:
        try:
            return f"{float(x):,.2f}"
        except Exception:
            return "—"

    lines = [
        f"{emoji} <b>XAUUSD SIGNAL — {esc(a)}</b> ({esc(record.get('confidence', '—'))})",
        f"GSS <b>{esc(record.get('gss_total'))}</b> · {esc(record.get('regime'))} "
        f"· ${num(record.get('price'))}",
    ]
    if a in ("LONG", "SHORT"):
        ez = record.get("entry_zone", {}) or {}
        lines.append(
            f"Size {esc(record.get('position_size_pct'))}% · "
            f"entry {num(ez.get('min'))}–{num(ez.get('max'))} · "
            f"SL {num(record.get('stop_loss'))} · "
            f"TP1 {num(record.get('take_profit_1'))} / TP2 {num(record.get('take_profit_2'))}")
    elif a == "REDUCE":
        lines.append(f"Size {esc(record.get('position_size_pct'))}% — trim exposure")
    if record.get("rationale"):
        lines.append(f"💡 {esc(record['rationale'])}")
    if record.get("override_reason"):
        lines.append(f"⚠ override: {esc(record['override_reason'])}")
    if reasons:
        lines.append(f"<i>trigger: {esc('; '.join(reasons))}</i>")
    lines.append("<i>Advisory — not auto-executed.</i>")
    return "\n".join(lines)


def notify_decision(record: Dict[str, Any], reasons: Optional[List[str]] = None) -> bool:
    """Send the signal to Telegram. Returns True if sent, False otherwise."""
    token, chat_id = _resolve()
    if not token or not chat_id:
        return False
    try:
        from trading_bot.telegram_sender import TelegramSender
        sender = TelegramSender(token=token, chat_id=chat_id)
        return sender.send_message(format_signal(record, reasons)) is not None
    except Exception:
        return False


def notify_text(html_message: str) -> bool:
    """Send a raw (already HTML-formatted) message to the resolved destination."""
    token, chat_id = _resolve()
    if not token or not chat_id:
        return False
    try:
        from trading_bot.telegram_sender import TelegramSender
        return TelegramSender(token=token, chat_id=chat_id).send_message(html_message) is not None
    except Exception:
        return False


def is_configured() -> bool:
    tok, chat = _resolve()
    return bool(tok and chat)
