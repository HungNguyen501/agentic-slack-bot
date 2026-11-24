"""Schedule-management tool handlers (list/add/update/remove cron-scheduled questions)."""
import logging

from schedules.core.ports import ScheduleRepository

log = logging.getLogger("schedules.tools")


def dispatch_schedule_tool(name: str, args: dict, bot_id: str, schedules: ScheduleRepository, user_id: str | None = None) -> str:
    """Execute a schedule management tool and return a human-readable result string.

    Args:
        name: Tool name — one of list_schedules, add_schedule, update_schedule, remove_schedule.
        args: Parsed JSON arguments from the LLM tool call.
        schedules: Where the bot's schedules are stored.
        user_id: The admin asking; a schedule they create or edit runs with their access.

    Returns:
        A plain-text or Slack-mrkdwn confirmation string, or an error message if the
        operation fails or the tool name is unrecognised.
    """
    try:
        if name == "list_schedules":
            rows = schedules.get_schedules(bot_id, active_only=True)
            if not rows:
                return "No schedules configured."
            zone_width = max(len("Timezone"), *(len(s.timezone) for s in rows))
            owner_width = max(len("Owner"), *(len(s.run_as or "-") for s in rows))
            header = f"{'ID':<36}  {'Cron':<20}  {'Timezone':<{zone_width}}  {'Channel':<12}  {'Owner':<{owner_width}}  Question"
            divider = "-" * len(header)
            lines = [header, divider] + [
                f"{s.id:<36}  {s.cron:<20}  {s.timezone:<{zone_width}}  {s.channel:<12}  {s.run_as or '-':<{owner_width}}  {s.question}"
                for s in rows
            ]
            return f"```\n{chr(10).join(lines)}\n```"

        if name == "add_schedule":
            s = schedules.add_schedule(args["cron"], args["channel"], args["question"], bot_id=bot_id, run_as=user_id)
            return f"Schedule created — id: `{s.id}`"

        if name == "update_schedule":
            s = schedules.update_schedule(
                args["id"],
                cron=args.get("cron"),
                channel=args.get("channel"),
                question=args.get("question"),
                bot_id=bot_id,
                run_as=user_id,
            )
            if not s:
                return f"Schedule `{args['id']}` not found or no fields to update."
            return f"Schedule `{s.id}` updated."

        if name == "remove_schedule":
            deleted = schedules.remove_schedule(args["id"], bot_id=bot_id)
            if deleted:
                return f"Schedule `{args['id']}` removed."
            return f"Schedule `{args['id']}` not found."

    except Exception as exc:
        log.exception("Schedule tool %s failed", name)
        return f"Error executing {name}: {exc}"

    return f"Unknown schedule tool: {name}"
