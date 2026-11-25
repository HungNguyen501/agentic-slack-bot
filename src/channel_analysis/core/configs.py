import re


class ChannelAnalysisConfigs:
    """All of the feature's constants: size limits, timeouts, formats it writes and parses, wording, name sets and patterns."""

    DEFAULT_LOOKBACK_DAYS = 30
    # How far back a window may reach: `lookback_days + end_days_ago` can't exceed it.
    MAX_REACH_DAYS = 30
    MAX_CONTAINS_TERMS = 5

    # About 10k tokens: one page fits in the agent's context with room to reason, and a request has 10 iterations in all.
    PAGE_CHAR_BUDGET = 40_000
    MESSAGE_CHAR_LIMIT = 3_000
    # What one thread's replies may add to a page; the rest is counted, not shown.
    REPLIES_CHAR_LIMIT = 10_000
    # Per message, on top of its text: the author, time, link and reaction lines.
    MESSAGE_OVERHEAD_CHARS = 200
    # What a `[file F…: name, type, size]` line adds besides the name and the type.
    FILE_LINE_OVERHEAD_CHARS = 50
    # What one `:name:×3` reaction adds besides its name and its count: the colons, the ×, and the space after it.
    REACTION_OVERHEAD_CHARS = 4
    # A reply is shown under `  ↳ ` and every line of it is indented by four spaces.
    REPLY_INDENT_CHARS = 4

    # Messages asked of Slack in one call, and how many a single tool call may scan while looking for matches. With replies
    # on, each message with a thread costs at least one more Slack call (about 50 a minute are allowed), so both are smaller.
    FETCH_BATCH = 200
    FETCH_BATCH_WITH_REPLIES = 50
    MAX_SCAN_PER_CALL = 600
    MAX_SCAN_PER_CALL_WITH_REPLIES = 100

    # A message's thread is read up to this many messages (the root included): what a page may show of one thread is far less.
    REPLIES_FETCH_MAX = 101

    FILE_PAGE_CHARS = 40_000
    FILE_MAX_BYTES = 5 * 1024 * 1024
    # A spreadsheet of 5 MB can unpack to far more text than a page tool will ever serve; stop extracting here.
    FILE_MAX_CHARS = 2_000_000
    # A CSV header is repeated on every page after the first only if it takes at most this share of a page.
    HEADER_MAX_PAGE_SHARE = 0.25

    # A PDF or a workbook is parsed in a child process with these limits: a file under FILE_MAX_BYTES can unpack to far more.
    PARSE_TIMEOUT_SECONDS = 30
    PARSE_CPU_SECONDS = 25
    PARSE_MEMORY_BYTES = 1024 * 1024 * 1024
    XLSX_MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
    XLSX_MAX_MEMBERS = 500

    # 1,000 members per page.
    MEMBER_PAGES_MAX = 10

    # --- analyze_channel: a cheaper model turns chunks of messages into records, code counts them. Untuned: measure before fixing
    # (docs/rfc_channel_analysis.md "Measuring before fixing").
    CHUNK_CHAR_BUDGET = 16_000
    MAX_CHUNKS_PER_CALL = 8
    EXTRACT_PARALLELISM = 4
    CHUNK_TIMEOUT_SECONDS = 90
    # Reasoning tokens count against this, so it leaves room for them as well as for the records.
    EXTRACT_MAX_OUTPUT_TOKENS = 12_000
    # Scanning is the point of an analysis, so its cap is higher than a page read's (about 15 history calls, or 10 with replies).
    MAX_SCAN_PER_CALL_ANALYSIS = 3_000
    MAX_SCAN_PER_CALL_ANALYSIS_WITH_REPLIES = 500
    # A file piece fits in one chunk, and one message adds at most as many pieces as a call has chunks, so the first message of a
    # call always fits and a call always makes progress.
    ITEM_CHAR_LIMIT = 14_000
    MAX_ITEMS_PER_MESSAGE = MAX_CHUNKS_PER_CALL
    # What an item's `[m12] ` marker and the blank line after it add to its heading and text.
    ITEM_MARKER_OVERHEAD_CHARS = 10
    MAX_FILES_PER_CALL = 20
    MAX_FIELDS = 8
    MAX_GROUP_BY = 3
    MAX_VALUE_CHARS = 200
    SAMPLE_RECORDS = 20
    MAX_GROUPS_LISTED = 100
    MAX_LINKS_PER_GROUP = 3
    MAX_INSTRUCTION_CHARS = 1_000
    MAX_FIELD_DESCRIPTION_CHARS = 200
    MAX_ALLOWED_VALUES = 20
    MAX_ALLOWED_VALUE_CHARS = 50

    # What an admin is told for each way a channel can be unreadable. The codes are Slack's where Slack has one.
    ACCESS_MESSAGES: dict[str, str] = {
        "channel_not_found": (
            "I can't find that channel. If it is private and I am not in it, it looks the same as a channel that does not exist: "
            "invite me, then send the channel link or ID."
        ),
        "channel_search_incomplete": (
            "This workspace has too many channels for me to find that one by name. Send me the channel link or its ID instead."
        ),
        "is_dm": "That is a direct message, and I only read channels.",
        "is_archived": "That channel is archived.",
        "not_in_channel": "I'm not a member of that channel, so I can't read it. Invite me with `/invite` and try again.",
        "missing_scope": (
            "My Slack app is missing a permission it needs to read channels (channels:history, groups:history, channels:read or groups:read). "
            "A workspace admin needs to add it and reinstall the app."
        ),
        "files_scope": (
            "My Slack app is missing the files:read permission, so I can read messages but not files. "
            "A workspace admin needs to add it and reinstall the app."
        ),
        "private_not_member": (
            "That is a private channel and you are not a member of it, so I won't read it for you. "
            "Ask a member to run this, or join the channel first."
        ),
        "membership_unverified": (
            "That is a private channel and I could not confirm you are a member of it (it has too many members to check), so I won't read it for you."
        ),
        "ratelimited": "Slack is rate limiting me. Try again in a minute.",
    }

    FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")

    # `m` is the item number in the answer's records; a field of that name would collide with it.
    RESERVED_FIELD_NAMES = frozenset({"m"})

    CHANNEL_ID_EXACT = re.compile(r"^[CG][A-Z0-9]{8,}$")

    DM_ID = re.compile(r"^D[A-Z0-9]{8,}$")

    # A copied channel link (https://acme.slack.com/archives/C0123456789/p17...) or the app's (https://app.slack.com/client/T0123/C0123456789).
    CHANNEL_URL = re.compile(r"^https?://[A-Za-z0-9.-]+\.slack\.com/(?:archives|client/[A-Z0-9]+)/([A-Z][A-Z0-9]{8,})(?:[/?#].*)?$")

    # What a result's `before` looks like: where to resume, and where the window started, so every page of one window has the same bounds.
    RESUME_TOKEN = re.compile(r"^(\d{9,11}\.\d{1,6})(?:@(\d{9,11}\.\d{1,6}))?$")

    DAY_SECONDS = 86400

    # Paging one window takes minutes; a resume token whose window started longer ago than the reach plus this wasn't made by a read.
    RESUME_TOLERANCE_SECONDS = 3600

    PLAIN_KINDS = frozenset({"csv", "tsv", "txt", "text", "json", "log", "md", "markdown", "yaml", "yml", "xml", "sql"})

    SUPPORTED_KINDS = PLAIN_KINDS | {"pdf", "xlsx"}

    SUPPORTED_FILES_TEXT = "CSV, TSV, text, JSON, logs, PDF with a text layer, and Excel (.xlsx)"

    # Subtypes that say nothing a report would be about.
    NOISE_SUBTYPES = frozenset(
        {
            "channel_join", "channel_leave", "channel_topic", "channel_purpose", "channel_name",
            "channel_archive", "channel_unarchive", "pinned_item", "unpinned_item", "group_join", "group_leave",
        }
    )  # fmt: skip

    # A reply posted "also to the channel" is in the channel's history as a message of its own, and in its thread too.
    BROADCAST_SUBTYPES = frozenset({"thread_broadcast", "reply_broadcast"})

    LABELLED_LINK = re.compile(r"<(https?://[^|>\s]+)\|([^>]+)>")

    # What a counts line shows for a missing value (the record's key keeps None, so it can't be mistaken for a value that reads the same).
    MISSING = "(missing)"

    # A value is kept whole up to this, so two values that differ only after the display cut (`MAX_VALUE_CHARS`) are still two records and two groups.
    MAX_STORED_VALUE_CHARS = 2_000
    # All one fetch may wait for a rate limit, however many threads it reads; a 429's Retry-After can be longer than a job can afford.
    MAX_RETRY_AFTER_SECONDS = 30.0
    # The line a workbook's text starts each sheet with, and the line a check's answer starts with (the agent trusts only the channel ID on it).
    SHEET_MARKER = "## Sheet: "
    ACCESS_CHECK_PREFIX = "Access check for "
    # Every tool that takes a `channel` argument and reads through the admin's access.
    CHANNEL_TOOLS = frozenset({"check_channel_access", "read_channel_messages", "read_channel_file", "analyze_channel"})
    CHANNEL_ID = re.compile(r"\b[CG][A-Z0-9]{8,}\b")
    CHECKED_CHANNEL = re.compile(rf"^{ACCESS_CHECK_PREFIX}([CG][A-Z0-9]{{8,}})\b")
    # How a request names a channel on purpose: `#name` or a mention `<#C123|name>`. A bare word that happens to be a channel's name
    # ("billing complaints") is a topic, not a choice of channel.
    HASH_NAME = re.compile(r"(?<![\w<&])#([\w-]+)")
    MENTION_NAME = re.compile(r"<#[CG][A-Z0-9]{8,}\|([^>]+)>")
