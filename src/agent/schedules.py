"""Schedule-management tool handlers (list/add/update/remove cron-scheduled questions)."""
import logging

from connectors.db.schedules import add_schedule, get_schedules, remove_schedule, update_schedule

log = logging.getLogger("agent.schedules")


def dispatch_schedule_tool(name: str, args: dict, bot_id: str) -> str:
    """Execute a schedule management tool and return a human-readable result string.

    Args:
        name: Tool name — one of list_schedules, add_schedule, update_schedule, remove_schedule.
        args: Parsed JSON arguments from the LLM tool call.

    Returns:
        A plain-text or Slack-mrkdwn confirmation string, or an error message if the
        operation fails or the tool name is unrecognised.
    """
    try:
        if name == "list_schedules":
            schedules = get_schedules(bot_id)
            if not schedules:
                return "No schedules configured."
            header = f"{'ID':<36}  {'Cron':<20}  {'Channel':<12}  Question"
            divider = "-" * len(header)
            rows = [header, divider] + [
                f"{s.id:<36}  {s.cron:<20}  {s.channel:<12}  {s.question}"
                for s in schedules
            ]
            return f"```\n{chr(10).join(rows)}\n```"

        if name == "add_schedule":
            s = add_schedule(args["cron"], args["channel"], args["question"], bot_id=bot_id)
            return f"Schedule created — id: `{s.id}`"

        if name == "update_schedule":
            s = update_schedule(
                args["id"],
                cron=args.get("cron"),
                channel=args.get("channel"),
                question=args.get("question"),
                bot_id=bot_id,
            )
            if not s:
                return f"Schedule `{args['id']}` not found or no fields to update."
            return f"Schedule `{s.id}` updated."

        if name == "remove_schedule":
            deleted = remove_schedule(args["id"], bot_id=bot_id)
            if deleted:
                return f"Schedule `{args['id']}` removed."
            return f"Schedule `{args['id']}` not found."

    except Exception as exc:
        log.exception("Schedule tool %s failed", name)
        return f"Error executing {name}: {exc}"

    return f"Unknown schedule tool: {name}"
