"""Data model for a verified data access request submission."""
from dataclasses import dataclass, field


@dataclass
class VerifiedAccessRequest:
    """A row filter access request whose principal has been confirmed to exist in Databricks."""

    ticket_id: str
    display_name: str
    user_email: str
    principal: str
    filter_column: str
    allowed_value: str
    scope_column: str
    scope_value: str
    principal_type: str
    groups: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def field_items(self) -> list[tuple[str, str]]:
        """Ordered (key, value) pairs matching rules_v2.yaml's external schema exactly — the
        single source of truth shared by to_mrkdwn() here and
        access_control_rules.format_rules_entry(). Deliberately excludes ticket_id: that
        column doesn't exist in rules_v2.yaml, it's Slack-display-only context (added back by
        to_mrkdwn() below)."""
        return [
            ("display_name", self.display_name),
            ("user_email", self.user_email),
            ("principal", self.principal),
            ("filter_column", self.filter_column),
            ("allowed_value", self.allowed_value),
            ("scope_column", self.scope_column),
            ("scope_value", self.scope_value),
            ("principal_type", self.principal_type),
            ("groups", f"[{', '.join(self.groups)}]"),
            ("tags", f"[{', '.join(self.tags)}]"),
        ]

    def to_mrkdwn(self, extra_lines: list[str] | None = None) -> str:
        """Render as a Slack code block, one `key: value` line per field (ticket_id first,
        since that's Slack/review context rather than part of the external rules schema).

        Args:
            extra_lines: Additional "key: value" lines appended inside the same code block —
                e.g. request-lifecycle metadata like expiration, which isn't part of the
                verified record itself (and must never leak into format_rules_entry's output)
                but should still line up visually with the rest of the content.
        """
        lines = [f"ticket_id: {self.ticket_id}"] + [f"{key}: {value}" for key, value in self.field_items()]
        lines.extend(extra_lines or [])
        return "```\n" + "\n".join(lines) + "\n```"
