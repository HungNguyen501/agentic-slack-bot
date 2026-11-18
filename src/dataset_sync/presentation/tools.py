"""Dataset sync tool handlers: list_synced_datasets, sync_dataset and schedule_dataset_sync (all admin-only, enforced in dispatch)."""
import logging
from datetime import UTC

from bots.core.models import BotConfig
from dataset_sync.core.errors import DatasetSyncError
from dataset_sync.core.service import DatasetSyncService

log = logging.getLogger("dataset_sync.tools")


def _list_synced_datasets(service: DatasetSyncService) -> str:
    overviews = service.list_datasets()
    if not overviews:
        return "No datasets are registered."
    lines = []
    for overview in overviews:
        source, schedule = overview.source, overview.schedule
        synced = f"last synced {source.last_synced_at.astimezone(UTC):%Y-%m-%d %H:%M} UTC, {source.row_count} rows" if source.last_synced_at else "never synced"
        status = f"last attempt failed: {source.last_error}" if source.last_status == "error" else "last attempt ok" if source.last_status else "no attempt yet"
        recurring = f"recurring sync `{schedule.cron}` UTC (schedule id `{schedule.id}`)" if schedule else "no recurring sync"
        enabled = "" if source.enabled else " [DISABLED]"
        lines.append(f"- `{source.dataset}`{enabled}: {source.description or 'no description'}\n  {synced}; {status}; {recurring}")
    return "\n".join(lines)


def _dataset_arg(args: dict) -> str:
    return str(args.get("dataset") or "").strip()


def _sync_dataset(args: dict, service: DatasetSyncService) -> str:
    result = service.sync_dataset(_dataset_arg(args), force=args.get("force") is True)
    return f"Synced `{result.dataset}`: {result.row_count} rows (was {result.previous_row_count})."


def _schedule_dataset_sync(args: dict, user_id: str | None, channel: str, bot: BotConfig, service: DatasetSyncService) -> str:
    dataset = _dataset_arg(args)
    cron = str(args.get("cron") or "").strip()
    added = service.schedule_sync(dataset, cron, args.get("channel"), channel, bot, user_id)
    existing = added.schedule
    if not added.created:
        reply = f"`{dataset}` already has a recurring sync — id: `{existing.id}`, on `{existing.cron}` (UTC). I didn't create another."
        if existing.bot_id not in (None, bot.bot_id):
            reply += (
                f" It was set up through another bot (`{existing.bot_id}`), and a bot can only remove its own schedules, "
                f"so changing it (you asked for `{cron}`) has to be done through that bot."
            )
        elif existing.cron != cron:
            reply += f" You asked for `{cron}`; to change it, ask me to remove schedule `{existing.id}` and set it up again."
        return reply
    return (
        f"Recurring sync of `{dataset}` scheduled — id: `{existing.id}`, on `{cron}` (UTC). "
        f"It runs silently; a failed run is reported in <#{existing.channel}>. Ask me to remove schedule `{existing.id}` to stop it."
    )


def dispatch_dataset_sync_tool(name: str, args: dict, user_id: str | None, channel: str, bot: BotConfig, service: DatasetSyncService) -> str:
    """Run one dataset sync tool and return the text to hand back to the LLM: a summary, a refusal or an error message."""
    try:
        if name == "list_synced_datasets":
            return _list_synced_datasets(service)
        if name == "sync_dataset":
            return _sync_dataset(args, service)
        if name == "schedule_dataset_sync":
            return _schedule_dataset_sync(args, user_id, channel, bot, service)
    except DatasetSyncError as exc:
        return str(exc)
    except Exception as exc:
        log.exception("Dataset sync tool %s failed", name)
        return f"Error executing {name}: {exc}"

    return f"Unknown dataset sync tool: {name}"
