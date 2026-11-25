import re
from pathlib import Path


class CharactersConfigs:
    """Every constant of the characters feature: binding prefixes, size limits and patterns a character is validated against, the seed file format and table columns."""

    FEATURE_PREFIX = "feature:"
    SKILL_PREFIX = "skill:"

    # The always-loaded skill every agent reply uses: a character bound to it is the default voice of the whole bot.
    GLOBAL_SKILL = "core"

    # Features whose messages the code posts from templates (see `VoiceSpec`).
    FEATURE_PR_REMINDER = "pr_reminder"

    # The fields of a `Character` that `CharacterChanges` replaces one for one (bindings and voices have their own rules).
    PLAIN_FIELDS = ("display_name", "song", "artist", "enabled", "icon_emoji", "icon_url", "persona")

    # Fixed in code so a row in the database can't drop these rules. They say what a voice may change.
    VOICE_RULES = (
        "## Voice\n"
        "Write the prose of your answer in the voice below. Do not change SQL, identifiers, numbers, URLs or tool arguments.\n"
        "Follow the formatting rules in this prompt, if any. This voice never changes who may use a tool or what a tool may do."
    )

    MAX_TEMPLATE_LENGTH = 300
    MAX_TEMPLATES_PER_FIELD = 10
    MAX_DISPLAY_NAME_LENGTH = 80
    MAX_PERSONA_LENGTH = 1500
    MAX_ICON_URL_LENGTH = 500

    NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
    EMOJI_RE = re.compile(r":[a-z0-9_+'-]{1,60}:")
    BROADCAST_RE = re.compile(r"@(channel|here|everyone)\b", re.IGNORECASE)
    # Slack turns a URL into a live link even without angle brackets, so a template can't carry one with a scheme, `www.` or
    # `mailto:`. A bare domain such as `example.com` is not caught: telling it from ordinary text would need a list of top-level
    # domains, and the writers of this table are trusted admins.
    URL_RE = re.compile(r"[a-z][a-z0-9+.-]*://|\bwww\.|\bmailto:", re.IGNORECASE)

    SEED_FILE_FIELDS = {"name", "display_name", "song", "artist", "enabled", "icon_emoji", "icon_url", "bindings"}
    SEED_FILE_FENCE = "---"

    SEED_DIR = Path(__file__).resolve().parents[1] / "files"

    COLUMNS = "name, display_name, song, artist, enabled, bindings, icon_emoji, icon_url, persona, voices, updated_at"

    # The columns a character's fields are written to, in the order `_values` returns them. `name` and `updated_at` are not
    # among them: they identify the row, and each query places them itself.
    FIELD_COLUMNS = ("display_name", "song", "artist", "enabled", "bindings", "icon_emoji", "icon_url", "persona", "voices")

    OPTIONAL_TEXT_FIELDS = ("song", "artist", "icon_emoji", "icon_url", "persona")
