"""The bot registry's row as the rest of the app sees it."""
from dataclasses import dataclass, field


@dataclass
class BotConfig:
    bot_id: str
    bot_token: str
    signing_secret: bytes
    enabled_skills: list[str] = field(default_factory=list)
    admin_users: frozenset[str] = field(default_factory=frozenset)
    app_id: str | None = None
