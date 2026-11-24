"""Channel analysis tool handlers: check_channel_access, read_channel_messages, read_channel_file and analyze_channel (admin-only, enforced in dispatch)."""
import logging

from bots.core.models import BotConfig
from channel_analysis.core import limits
from channel_analysis.core.errors import ChannelAnalysisError
from channel_analysis.core.extraction import format_when as _when
from channel_analysis.core.models import Analysis, ChannelAccess, ChannelMessage, ChannelSlice, ExtractionField, ExtractionSpec, FilePage, GroupCount
from channel_analysis.core.service import ChannelAnalysisService

log = logging.getLogger("channel_analysis.tools")

# What a counts line shows for a missing value (the record's key keeps None, so it can't be mistaken for a value that reads the same).
MISSING = "(missing)"


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
        head, *rest = _format_message(reply)
        lines.append(f"  ↳ {head}")
        lines.extend(f"    {line}" for part in rest for line in part.split("\n"))
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


def _check(args: dict, requester_id: str, bot: BotConfig, service: ChannelAnalysisService) -> str:
    return _format_access(service.check_access(bot, str(args.get("channel") or ""), requester_id))


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


def _read(args: dict, requester_id: str, bot: BotConfig, service: ChannelAnalysisService) -> str:
    return _format_slice(service.read(bot, str(args.get("channel") or ""), requester_id, **_read_arguments(args)))


def _read_file(args: dict, requester_id: str, bot: BotConfig, service: ChannelAnalysisService) -> str:
    file_id = str(args.get("file_id") or "").strip()
    if not file_id:
        raise ChannelAnalysisError("Give me the file ID (F…) shown next to the file in a message.")
    offset = _int_arg(args, "offset") if args.get("offset") is not None else 0
    return _format_file(service.read_file(bot, str(args.get("channel") or ""), requester_id, file_id, offset))


def _strings_arg(args: dict, name: str) -> tuple[str, ...]:
    value = args.get(name)
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ChannelAnalysisError(f"`{name}` must be a list of strings.")
    return tuple(value)


def _spec_arg(args: dict) -> ExtractionSpec:
    instruction = args.get("instruction")
    raw_fields = args.get("fields")
    if not isinstance(instruction, str):
        raise ChannelAnalysisError("`instruction` must say, in words, how to fill one record.")
    if not isinstance(raw_fields, list) or not all(isinstance(f, dict) for f in raw_fields):
        raise ChannelAnalysisError("`fields` must be a list of {name, description, allowed_values?} objects.")
    fields = []
    for raw in raw_fields:
        if not isinstance(raw.get("name"), str) or not isinstance(raw.get("description", ""), str):
            raise ChannelAnalysisError("Each field needs a `name` and a `description`, both text.")
        fields.append(ExtractionField(raw["name"], raw.get("description", ""), _strings_arg(raw, "allowed_values")))
    return ExtractionSpec(instruction, tuple(fields))


def _shown(value: str | None) -> str:
    return MISSING if value is None else value


def _group_line(group: GroupCount, group_by: tuple[str, ...]) -> str:
    label = " · ".join(f"{name}={_shown(value)}" for name, value in zip(group_by, group.key, strict=True)) if group_by else "all records"
    links = f" · {' '.join(group.links)}" if group.links else ""
    return f"- {label}: {group.records} · {group.messages} · {group.dates}{links}"


def _analysis_coverage(result: Analysis) -> list[str]:
    """What was read, what was left out, and whether the counts can be trusted."""
    matched = f", matched any of: {', '.join(result.terms)}" if result.terms else ""
    lines = [
        f"Analysis of channel {result.channel_id} · window {_when(result.window_start)} to {_when(result.window_end)}",
        f"Read {result.messages_read} messages in {result.chunks} chunks (scanned {result.scanned}{matched}). "
        f"Extracted {result.records} records; rejected {result.rejected}.",
        f"Fields: {', '.join(result.fields)} · counted by: {', '.join(result.group_by) or 'nothing (one overall total)'}",
    ]
    if result.failed_chunks:
        lines.append("Chunks that failed (their messages are NOT in the counts):")
        lines.extend(f"- {_when(c.start_ts)} to {_when(c.end_ts)}, {c.messages} messages: {c.reason}" for c in result.failed_chunks)
    else:
        lines.append("Chunks that failed: none")
    if result.unreliable:
        lines.append("UNRELIABLE: more than half the chunks failed, so these counts leave out too much. Say so, and run a narrower window.")
    if result.messages_cut:
        lines.append(f"Messages longer than one message may add (their end was not read): {result.messages_cut}")
    if result.replies_omitted:
        lines.append(f"Replies not read (rate limited, no access, or over the cap on one thread): {result.replies_omitted}; the counts leave them out.")
    lines.append("Files not read: " + ("; ".join(f"{name} ({reason})" for name, reason in result.files_not_read) or "none"))
    return lines


def _analysis_counts(result: Analysis) -> list[str]:
    lines = ["", "Counts (records · messages · distinct dates):"]
    lines.extend(_group_line(group, result.group_by) for group in result.groups)
    if result.groups_omitted:
        lines.append(f"({result.groups_omitted} more groups: {result.records_omitted} records)")
    if not result.groups:
        lines.append("(no records)")
    if result.sample:
        lines += ["", "Sample records, newest first:"]
        for r in result.sample:
            lines.append(f"- [{_when(r.ts)}] {r.link or ''}: " + ", ".join(f"{name}={_shown(value)}" for name, value in r.values.items()))
    return lines


def _analysis_paging(result: Analysis) -> str:
    if not result.next_before:
        return "End of window: that was every matching message."
    return (
        f'More messages remain. Call analyze_channel again with before="{result.next_before}" and the same other arguments. '
        "Counts are for this call only: add them up across calls. Distinct dates are exact within one call; a date split across two calls is counted in each."
    )


def _format_analysis(result: Analysis) -> str:
    return "\n".join([*_analysis_coverage(result), *_analysis_counts(result), "", _analysis_paging(result)])


def _analyze(args: dict, requester_id: str, bot: BotConfig, service: ChannelAnalysisService) -> str:
    kwargs = _read_arguments(args)
    result = service.analyze(
        bot,
        str(args.get("channel") or ""),
        requester_id,
        _spec_arg(args),
        group_by=_strings_arg(args, "group_by"),
        include_files=args.get("include_files") is True,
        **kwargs,
    )
    return _format_analysis(result)


def dispatch_channel_analysis_tool(name: str, args: dict, requester_id: str, bot: BotConfig, service: ChannelAnalysisService) -> str:
    """Run one channel analysis tool and return the text to hand back to the LLM: the result, a refusal or an error message.

    `requester_id` is the admin the read is for: the person asking, or the owner of a scheduled question.
    """
    try:
        if name == "check_channel_access":
            return _check(args, requester_id, bot, service)
        if name == "read_channel_messages":
            return _read(args, requester_id, bot, service)
        if name == "read_channel_file":
            return _read_file(args, requester_id, bot, service)
        if name == "analyze_channel":
            return _analyze(args, requester_id, bot, service)
    except ChannelAnalysisError as exc:
        return str(exc)
    except Exception as exc:
        log.exception("Channel analysis tool %s failed", name)
        return f"Error executing {name}: {exc}"

    return f"Unknown channel analysis tool: {name}"
