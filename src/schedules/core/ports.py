from typing import Protocol

from schedules.core.models import AddedDatasetSync, AddedReminder, Schedule


class ScheduleRepository(Protocol):
    def get_schedules(self, bot_id: str | None = None, *, active_only: bool = False) -> list[Schedule]:
        """Schedules by creation time. With `bot_id`: that bot's plus legacy rows with no owner. `active_only` skips ended ones."""

    def get_schedule(self, id: str) -> Schedule | None:
        """One schedule by id, whatever its kind or status, or None."""

    def add_schedule(self, cron: str, channel: str, question: str, bot_id: str | None = None, run_as: str | None = None) -> Schedule:
        """Insert a question schedule that runs with the access of Slack user `run_as`."""

    def update_schedule(
        self, id: str, cron: str | None = None, channel: str | None = None, question: str | None = None, *, bot_id: str, run_as: str | None = None
    ) -> Schedule | None:
        """Edit a `question` schedule owned by `bot_id` (or ownerless); None if nothing to change or no such row.

        An edit makes `run_as` the schedule's user: it runs with the access of whoever last decided what it does.
        """

    def remove_schedule(self, id: str, *, bot_id: str) -> bool:
        """Delete a schedule owned by `bot_id` (or ownerless); False if no row matched."""

    def add_pr_reminder(
        self, id: str, cron: str, channel: str, label: str, bot_id: str, config: dict, *, max_active: int, timezone: str = "UTC"
    ) -> AddedReminder | None:
        """Insert a PR reminder unless an equivalent one is active (returned, `created` False) or the channel's quota is used (None).

        The duplicate check, quota check and insert are one atomic step.
        """

    def add_dataset_sync(self, id: str, cron: str, channel: str, label: str, bot_id: str, config: dict) -> AddedDatasetSync:
        """Insert a recurring dataset sync unless an active one for `config["dataset"]` exists (returned, `created` False); atomic."""

    def get_dataset_sync_schedules(self) -> dict[str, Schedule]:
        """Active recurring dataset syncs by dataset name."""

    def set_schedule_status(self, id: str, status: str) -> None:
        """End an active schedule ('completed' or 'expired'); the row is kept."""

    def merge_schedule_config(self, id: str, patch: dict) -> None:
        """Merge `patch` into the schedule's config (top-level keys replaced, the rest kept)."""
