"""Channel analysis tool handlers: check_channel_access, read_channel_messages and read_channel_file (admin-only, enforced in dispatch)."""
import logging
from datetime import UTC, datetime

from bots.core.models import BotConfig
from channel_analysis.core import limits
from channel_analysis.core.errors import ChannelAnalysisError
from channel_analysis.core.models import ChannelAccess, ChannelMessage, ChannelSlice, FilePage
from channel_analysis.core.service import ChannelAnalysisService

log = logging.getLogger("channel_analysis.tools")


def _when(ts: str | float) -> str:
    return f"{datetime.fromtimestamp(float(ts), UTC):%Y-%m-%d %H:%M} UTC"


def _int_arg(args: dict, name: str) -> int:
    value = args[name]
    try:
        if isinstance(value, bool):
            raise ValueError(value)
        return int(value)
    except (TypeError, ValueError):
        raise ChannelAnalysisError(f"`{name}` must be a whole number.") from None


def _terms_arg(args: dict) -> tuple[str, ...]:
    value = args["contains"]
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list) and all(isinstance(term, str) for term in value):
        return tuple(value)
    raise ChannelAnalysisError("`contains` must be a list of strings.")


def _format_access(access: ChannelAccess) -> str:
    where = f"#{access.channel_name} ({access.channel_id})" if access.channel_name else access.channel_id or "that channel"
    if not access.problems:
        lines = [f"Access check for {where}: OK. I can read it and you may see it."]
    elif access.ok:
        lines = [f"Access check for {where}: OK to read messages, with a warning."]
    else:
        lines = [f"Access check for {where}: NOT OK. Do not read this channel until the blocking problems are fixed; tell the admin each one and how to fix it."]
    lines.extend(f"- {'BLOCKING' if p.blocking else 'warning'}: {p.message}" for p in access.problems)
    if access.empty_window:
        lines.append(f"- note: there are no messages in the last {limits.DEFAULT_LOOKBACK_DAYS} days.")
    return "\n".join(lines)


def _format_message(message: ChannelMessage) -> list[str]:
    head = f"[{_when(message.ts)}] {message.author}" + (f" · {message.link}" if message.link else "")
    lines = [head, message.text or "(no text)"]
    lines.extend(f"[file {f.id}: {f.name}, {f.filetype}, {max(f.size // 1024, 1)} KB]" for f in message.files)
    extras = []
    if message.reactions:
        extras.append("reactions: " + " ".join(f":{name}:×{count}" for name, count in message.reactions))
    if message.reply_count:
        extras.append(f"replies: {message.reply_count}")
    if extras:
        lines.append(" · ".join(extras))
    for reply in message.replies:
        lines.append(f"  ↳ [{_when(reply.ts)}] {reply.author}: {reply.text}".replace("\n", "\n    "))
    if message.omitted_replies:
        lines.append(f"  ↳ ({message.omitted_replies} more replies not shown)")
    return lines


def _format_slice(page: ChannelSlice) -> str:
    matched = f", matched any of: {', '.join(page.terms)}" if page.terms else ""
    lines = [
        f"Channel {page.channel_id} · window {_when(page.window_start)} to {_when(page.window_end)} · newest first",
        f"This page: {len(page.messages)} messages (scanned {page.scanned}{matched})",
        "",
    ]
    for message in page.messages:
        lines.extend(_format_message(message))
        lines.append("")
    if page.next_before:
        lines.append(f'More messages remain. Call read_channel_messages again with before="{page.next_before}" and the same other arguments.')
    else:
        lines.append("End of window: that was every matching message.")
    return "\n".join(lines)


def _format_file(page: FilePage) -> str:
    end = page.next_offset if page.next_offset is not None else page.total_chars
    lines = [f"File {page.name} ({page.filetype}, {max(page.size // 1024, 1)} KB) · characters {page.offset} to {end} of {page.total_chars}", "", page.text, ""]
    if page.next_offset is not None:
        lines.append(f"More of the file remains. Call read_channel_file again with offset={page.next_offset} and the same other arguments.")
    else:
        lines.append("End of file.")
    return "\n".join(lines)


def _check(args: dict, user_id: str | None, bot: BotConfig, service: ChannelAnalysisService) -> str:
    return _format_access(service.check_access(bot, str(args.get("channel") or ""), user_id))


def _read_arguments(args: dict) -> dict:
    """The optional arguments the model passed, and only those, so the use case owns every default."""
    given: dict = {name: _int_arg(args, name) for name in ("lookback_days", "end_days_ago") if args.get(name) is not None}
    if args.get("contains") is not None:
        given["contains"] = _terms_arg(args)
    if isinstance(args.get("include_replies"), bool):
        given["include_replies"] = args["include_replies"]
    if args.get("before"):
        given["before"] = str(args["before"])
    return given


def _read(args: dict, user_id: str | None, bot: BotConfig, service: ChannelAnalysisService) -> str:
    return _format_slice(service.read(bot, str(args.get("channel") or ""), user_id, **_read_arguments(args)))


def _read_file(args: dict, user_id: str | None, bot: BotConfig, service: ChannelAnalysisService) -> str:
    file_id = str(args.get("file_id") or "").strip()
    if not file_id:
        raise ChannelAnalysisError("Give me the file ID (F…) shown next to the file in a message.")
    offset = _int_arg(args, "offset") if args.get("offset") is not None else 0
    return _format_file(service.read_file(bot, str(args.get("channel") or ""), user_id, file_id, offset))


def dispatch_channel_analysis_tool(name: str, args: dict, user_id: str | None, bot: BotConfig, service: ChannelAnalysisService) -> str:
    """Run one channel analysis tool and return the text to hand back to the LLM: the result, a refusal or an error message.

    `user_id` is the requester, or None for a run with no requester (a scheduled question).
    """
    try:
        if name == "check_channel_access":
            return _check(args, user_id, bot, service)
        if name == "read_channel_messages":
            return _read(args, user_id, bot, service)
        if name == "read_channel_file":
            return _read_file(args, user_id, bot, service)
    except ChannelAnalysisError as exc:
        return str(exc)
    except Exception as exc:
        log.exception("Channel analysis tool %s failed", name)
        return f"Error executing {name}: {exc}"

    return f"Unknown channel analysis tool: {name}"
