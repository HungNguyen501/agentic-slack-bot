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

# Messages asked of Slack in one call, and how many a single tool call may scan while looking for matches. With replies
# on, each message with a thread costs one more Slack call (about 50 a minute are allowed), so both are smaller.
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
TABULAR_FILE_TYPES = frozenset({"csv", "tsv"})
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
