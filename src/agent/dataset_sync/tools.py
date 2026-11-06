"""Dataset sync tool handlers: list_synced_datasets, sync_dataset and schedule_dataset_sync (all admin-only, enforced in dispatch)."""
import logging
import re
import uuid
from datetime import UTC

from agent.dataset_sync import store
from agent.dataset_sync.service import DatasetSyncError, require_source, sync_dataset
from common.configs import AgentConfigs
from common.cron import CronError, validate_cron
from connectors.db.bots import BotConfig
from connectors.db.schedules import add_dataset_sync, get_dataset_sync_schedules

log = logging.getLogger("agent.dataset_sync.tools")

_CHANNEL_ID_RE = re.compile(r"[CGD][A-Z0-9]{6,}")


def _list_synced_datasets() -> str:
    sources = store.list_sources()
    if not sources:
        return "No datasets are registered."
    schedules = get_dataset_sync_schedules()
    lines = []
    for source in sources:
        synced = f"last synced {source.last_synced_at.astimezone(UTC):%Y-%m-%d %H:%M} UTC, {source.row_count} rows" if source.last_synced_at else "never synced"
        status = f"last attempt failed: {source.last_error}" if source.last_status == "error" else "last attempt ok" if source.last_status else "no attempt yet"
        schedule = schedules.get(source.dataset)
        recurring = f"recurring sync `{schedule.cron}` UTC (schedule id `{schedule.id}`)" if schedule else "no recurring sync"
        enabled = "" if source.enabled else " [DISABLED]"
        lines.append(f"- `{source.dataset}`{enabled}: {source.description or 'no description'}\n  {synced}; {status}; {recurring}")
    return "\n".join(lines)


def _dataset_arg(args: dict) -> str:
    return str(args.get("dataset") or "").strip()


def _sync_dataset(args: dict) -> str:
    result = sync_dataset(_dataset_arg(args), force=args.get("force") is True)
    return f"Synced `{result.dataset}`: {result.row_count} rows (was {result.previous_row_count})."


def _schedule_dataset_sync(args: dict, user_id: str | None, channel: str, bot: BotConfig) -> str:
    source = require_source(_dataset_arg(args))
    cron = str(args.get("cron") or "").strip()
    try:
        validate_cron(cron, AgentConfigs.DATASET_SYNC_MIN_INTERVAL_MINUTES, "A dataset sync")
    except CronError as exc:
        raise DatasetSyncError(str(exc)) from exc
    report_channel = args.get("channel") or channel
    if report_channel != channel and not _CHANNEL_ID_RE.fullmatch(report_channel):
        raise DatasetSyncError(f"'{report_channel}' is not a Slack channel ID.")

    added = add_dataset_sync(
        str(uuid.uuid4()), cron, report_channel, f"Dataset sync · {source.dataset}", bot.bot_id, {"dataset": source.dataset, "created_by": user_id}
    )
    if not added.created:
        existing = added.schedule
        reply = f"`{source.dataset}` already has a recurring sync — id: `{existing.id}`, on `{existing.cron}` (UTC). I didn't create another."
        if existing.cron != cron:
            reply += f" You asked for `{cron}`; to change it, ask me to remove schedule `{existing.id}` and set it up again."
        return reply
    return (
        f"Recurring sync of `{source.dataset}` scheduled — id: `{added.schedule.id}`, on `{cron}` (UTC). "
        f"It runs silently; a failed run is reported in <#{report_channel}>. Ask me to remove schedule `{added.schedule.id}` to stop it."
    )


def dispatch_dataset_sync_tool(name: str, args: dict, user_id: str | None, channel: str, bot: BotConfig) -> str:
    """Run one dataset sync tool and return the text to hand back to the LLM: a summary, a refusal or an error message."""
    try:
        if name == "list_synced_datasets":
            return _list_synced_datasets()
        if name == "sync_dataset":
            return _sync_dataset(args)
        if name == "schedule_dataset_sync":
            return _schedule_dataset_sync(args, user_id, channel, bot)
    except DatasetSyncError as exc:
        return str(exc)
    except Exception as exc:
        log.exception("Dataset sync tool %s failed", name)
        return f"Error executing {name}: {exc}"

    return f"Unknown dataset sync tool: {name}"
