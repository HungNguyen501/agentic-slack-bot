"""Data model for a verified data access request submission."""
from dataclasses import dataclass, field


@dataclass
class VerifiedAccessRequest:
    """A row filter access request whose principal has been confirmed to exist in Databricks."""

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

    def to_mrkdwn(self) -> str:
        """Render as a Slack code block, one `key: value` line per field."""
        lines = [
            f"display_name: {self.display_name}",
            f"user_email: {self.user_email}",
            f"principal: {self.principal}",
            f"filter_column: {self.filter_column}",
            f"allowed_value: {self.allowed_value}",
            f"scope_column: {self.scope_column}",
            f"scope_value: {self.scope_value}",
            f"principal_type: {self.principal_type}",
            f"groups: [{', '.join(self.groups)}]",
            f"tags: [{', '.join(self.tags)}]",
        ]
        return "```\n" + "\n".join(lines) + "\n```"
