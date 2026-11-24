"""Turns a Slack message payload into the plain text, author, reactions and files the agent reads."""
import re

from channel_analysis.core.models import FileRef

# Subtypes that say nothing a report would be about.
NOISE_SUBTYPES = frozenset(
    {
        "channel_join", "channel_leave", "channel_topic", "channel_purpose", "channel_name",
        "channel_archive", "channel_unarchive", "pinned_item", "unpinned_item", "group_join", "group_leave",
    }
)  # fmt: skip


def is_noise(message: dict) -> bool:
    return message.get("subtype") in NOISE_SUBTYPES


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
            files.append(FileRef(str(f["id"]), str(f.get("name") or f.get("title") or f["id"]), str(f.get("filetype") or "unknown"), int(f.get("size") or 0)))
    return tuple(files)


def _words(text: str) -> str:
    """The words of a text without Slack's markup, to tell whether a block only repeats `text`."""
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
    if kind == "channel":
        return f"<#{element.get('channel_id')}>"
    if kind == "emoji":
        return f":{element.get('name')}:"
    if kind == "broadcast":
        return f"@{element.get('range')}"
    return ""


def _attachment_text(attachment: dict) -> str:
    lines = [attachment.get("pretext"), attachment.get("title"), attachment.get("text")]
    lines.extend(f"{f.get('title')}: {f.get('value')}" if f.get("title") else str(f.get("value")) for f in attachment.get("fields") or [])
    lines.extend(_block_text(block) for block in attachment.get("blocks") or [])
    text = "\n".join(line for line in lines if line)
    return text or attachment.get("fallback") or ""
