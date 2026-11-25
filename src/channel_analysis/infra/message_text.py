"""Turns a Slack message payload into the plain text, author, reactions and files the agent reads."""
import re
from datetime import UTC, datetime

from channel_analysis.core.configs import ChannelAnalysisConfigs
from channel_analysis.core.models import FileRef
from channel_analysis.infra.file_kinds import kind_of


def is_noise(message: dict) -> bool:
    return message.get("subtype") in ChannelAnalysisConfigs.NOISE_SUBTYPES


def is_broadcast(message: dict) -> bool:
    return message.get("subtype") in ChannelAnalysisConfigs.BROADCAST_SUBTYPES


def author_of(message: dict) -> str:
    name = message.get("username") or (message.get("bot_profile") or {}).get("name")
    if name:
        return str(name)
    if message.get("user"):
        return f"<@{message['user']}>"
    return "bot" if message.get("bot_id") else "unknown"


def reactions_of(message: dict) -> tuple[tuple[str, int], ...]:
    return tuple((str(r["name"]), int(r.get("count", 0))) for r in message.get("reactions") or [] if r.get("name"))


def files_of(message: dict) -> tuple[FileRef, ...]:
    files = []
    for f in message.get("files") or []:
        if f.get("id") and f.get("mode") != "tombstone":
            name = str(f.get("name") or f.get("title") or f["id"])
            files.append(FileRef(str(f["id"]), name, kind_of(name, str(f.get("filetype") or "unknown")), int(f.get("size") or 0)))
    return tuple(files)


def _words(text: str) -> str:
    """The words of a text without Slack's markup, to tell whether a block only repeats `text`.

    A link is `<url|label>` in `text` and `label (url)` in a rich-text block, so both become `label url`. Slack escapes `&`, `<` and
    `>` in `text` as entities but not in blocks, so they are decoded (`&amp;` last, or `&amp;lt;` would turn into `<`). Slack's `text`
    writes a list with `•` (bullets), `◦` / `▪` (nested) or `1.` (ordered) and a rich-text block here with `-`, so a leading marker is dropped.
    """
    text = ChannelAnalysisConfigs.LIST_MARKER.sub("", text)
    text = ChannelAnalysisConfigs.LABELLED_LINK.sub(r"\2 \1", text)
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    text = re.sub(r"[()<>|]+", " ", text)
    return " ".join(re.sub(r"[*_~`]+", " ", text).lower().split())


def flatten_text(message: dict) -> str:
    """The message's text, then the text of its blocks and attachments, without repeating a part.

    A person's message carries its words twice, as `text` and as a rich-text block that differs only in markup, so a rich-text
    block is skipped when its words are already in `text`. One that says more (an app's short `text` fallback over a longer
    body) is kept. A bot's report often has little or no `text`, with the content in section blocks and attachments.
    """
    parts: list[str] = []

    def add(text: str | None) -> None:
        text = (text or "").strip()
        if text and text not in parts:
            parts.append(text)

    text = message.get("text") or ""
    add(text)
    for block in message.get("blocks") or []:
        block_text = _block_text(block)
        if block.get("type") == "rich_text" and f" {_words(block_text)} " in f" {_words(text)} ":
            continue
        add(block_text)
    for attachment in message.get("attachments") or []:
        add(_attachment_text(attachment))
    return "\n".join(parts)


def _block_text(block: dict) -> str:
    kind = block.get("type")
    if kind in ("header", "section"):
        lines = [(block.get("text") or {}).get("text") or ""]
        lines.extend((field or {}).get("text") or "" for field in block.get("fields") or [])
        return "\n".join(line for line in lines if line)
    if kind == "context":
        return " ".join((el.get("text") or el.get("alt_text") or "") for el in block.get("elements") or [])
    if kind == "rich_text":
        return "\n".join(_rich_element(el) for el in block.get("elements") or [])
    if kind == "image":
        return block.get("alt_text") or ""
    if kind == "table":
        return "\n".join(" | ".join(_table_cell(cell) for cell in row) for row in block.get("rows") or [])
    return ""


def _table_cell(cell: dict) -> str:
    """A table cell is raw text or rich text; both give the words it shows."""
    if cell.get("type") == "rich_text":
        return " ".join(_rich_element(el) for el in cell.get("elements") or []).strip()
    return str(cell.get("text") or "")


def _rich_element(element: dict) -> str:
    if element.get("type") == "rich_text_list":
        return "\n".join(f"- {_inline(item)}" for item in element.get("elements") or [])
    return _inline(element)


def _inline(element: dict) -> str:
    kind = element.get("type")
    if kind in ("rich_text_section", "rich_text_quote", "rich_text_preformatted"):
        return "".join(_inline(child) for child in element.get("elements") or [])
    if kind == "text":
        return element.get("text") or ""
    if kind == "link":
        url, label = element.get("url") or "", element.get("text")
        return f"{label} ({url})" if label and label != url else url
    if kind == "user":
        return f"<@{element.get('user_id')}>"
    if kind == "usergroup":
        return f"<!subteam^{element.get('usergroup_id')}>"
    if kind == "channel":
        return f"<#{element.get('channel_id')}>"
    if kind == "emoji":
        return f":{element.get('name')}:"
    if kind == "broadcast":
        return f"@{element.get('range')}"
    if kind == "date":
        return _date_text(element)
    return ""


def _date_text(element: dict) -> str:
    """A date element shows its fallback text, else its timestamp in UTC (Slack formats it for each reader, so the format is dropped)."""
    if element.get("fallback"):
        return str(element["fallback"])
    try:
        return f"{datetime.fromtimestamp(int(element['timestamp']), UTC):%Y-%m-%d %H:%M} UTC"
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        return ""


def _attachment_text(attachment: dict) -> str:
    lines = [attachment.get("pretext"), attachment.get("title"), attachment.get("text")]
    lines.extend(f"{f.get('title')}: {f.get('value')}" if f.get("title") else str(f.get("value")) for f in attachment.get("fields") or [])
    lines.extend(_block_text(block) for block in attachment.get("blocks") or [])
    text = "\n".join(line for line in lines if line)
    return text or attachment.get("fallback") or ""
