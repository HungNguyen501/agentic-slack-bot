"""RQ worker tasks — resolved by dotted name (e.g. worker.tasks.reply_to_mention)."""
import functools
import logging
import re
import threading
import time

from slack_sdk.errors import SlackApiError

from agent.access_control import review, submission
from agent.agent import run_agent, summarize_answer
from agent.history import record_thread_messages
from agent.pr_reminders.service import run_reminder
from connectors import slack
from connectors.db.bots import get_by_id as get_bot

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("worker")

# Any Slack mention token like <@U12345>; the fallback when the bot's own user id can't be looked up.
_MENTION_RE = re.compile(r"<@[^>]+>\s*", re.UNICODE)

_THINKING_INTERVAL_SECONDS = 1.0

# Every conversation's animation thread shares this lock/timestamp so this worker process
# never fires more than one chat.update per _MIN_GLOBAL_UPDATE_GAP_SECONDS in total, no matter
# how many conversations are animating at once — otherwise N concurrent conversations means N
# concurrent update loops with no coordination, which is what actually burns Slack's shared
# per-workspace chat.update rate limit. This is per-process only (each worker replica has its
# own budget); fine at this app's scale, but move to a Redis-backed limiter if that stops holding.
_animation_lock = threading.Lock()
_last_animation_update = 0.0
_MIN_GLOBAL_UPDATE_GAP_SECONDS = 1.0


@functools.cache
def _bot_user_id(bot_token: str) -> str:
    return slack.get_bot_user_id(bot_token)


def _strip_bot_mention(text: str, bot_token: str) -> str:
    """Remove the bot's own @mention from a message, keeping other people's: a request can name who to remind.

    If the bot's user id can't be looked up, every mention is stripped, as before, so the request still runs.
    """
    try:
        bot_mention = re.compile(rf"<@{re.escape(_bot_user_id(bot_token))}(\|[^>]*)?>\s*")
    except SlackApiError:
        log.warning("Could not look up the bot's user id; stripping every mention from the request")
        return _MENTION_RE.sub("", text).strip()
    return bot_mention.sub("", text).strip()


def _throttled_animation_update(channel: str, ts: str, text: str, token: str) -> None:
    """Send one animation frame, dropping it (not queuing it) if another thread just updated.

    A dropped frame just means this conversation's dots don't advance this tick — harmless,
    since it's decorative. Raises SlackApiError on an actual Slack-side failure (including a
    429), left for the caller to handle.
    """
    global _last_animation_update
    with _animation_lock:
        now = time.monotonic()
        if now - _last_animation_update < _MIN_GLOBAL_UPDATE_GAP_SECONDS:
            return
        _last_animation_update = now
    slack.update_message(channel, ts, text, token=token)


def _animate_thinking(channel: str, ts: str, token: str, stop_event: threading.Event) -> None:
    """Cycle the placeholder message's dots every _THINKING_INTERVAL_SECONDS until stop_event is set.

    Runs on its own thread alongside the (potentially slow) agent loop so the user sees the
    bot visibly working rather than a static message. stop_event.wait() doubles as the sleep,
    so setting the event both ends the loop and skips any remaining wait immediately.
    """
    frame = 0
    while not stop_event.wait(_THINKING_INTERVAL_SECONDS):
        frame += 1
        text = " ".join([":pepe-pray:"] * frame)
        try:
            _throttled_animation_update(channel, ts, text, token)
        except SlackApiError as exc:
            if exc.response is not None and exc.response.status_code == 429:
                # Actually rate-limited despite the throttle (e.g. other traffic on this
                # token) — back off for exactly as long as Slack says, then keep animating
                # rather than giving up on the rest of this conversation's animation.
                retry_after = float(exc.response.headers.get("Retry-After", 1))
                log.warning("Thinking-animation rate-limited; backing off %.1fs", retry_after)
                stop_event.wait(retry_after)
                continue
            log.exception("Thinking-animation update failed")
            return
        except Exception:
            log.exception("Thinking-animation update failed")
            return


def reply_to_mention(
    channel: str,
    thread_ts: str,
    bot_id: str,
    user: str | None = None,
    text: str = "",
) -> str:
    """Strip the @mention, run the agent, and post the answer back into the Slack thread.

    Args:
        channel: Slack channel ID where the mention occurred.
        thread_ts: Timestamp of the thread root used to scope conversation history.
        user: Slack user ID of the person who mentioned the bot; forwarded to the agent
            for schedule management authorization.
        text: Raw message text including the @mention prefix.
        bot_id: Bot identifier used to load per-bot config (token, skills, admin users).

    Returns:
        The Slack message timestamp (ts) of the last posted reply chunk.
    """
    bot = get_bot(bot_id)
    question = _strip_bot_mention(text, bot.bot_token)

    review_command = review.parse_review_command(question)
    if review_command:
        decision, distinct_ids = review_command

        if len(distinct_ids) > 1:
            result = "I found multiple request ids in that message — please approve or reject one at a time."
            return slack.post_message(channel, result, thread_ts, token=bot.bot_token)

        request_id = distinct_ids[0] if distinct_ids else None

        # Approval does real I/O (Databricks principal resolution, GitHub PR creation) that
        # can take a few seconds, so give the same "working on it" feedback as the agent loop
        # below rather than leaving the reviewer staring at silence.
        thinking_ts = slack.post_message(channel, ":pepe-pray:", thread_ts, token=bot.bot_token)
        stop_animation = threading.Event()
        animation_thread = threading.Thread(
            target=_animate_thinking,
            args=(channel, thinking_ts, bot.bot_token, stop_animation),
            daemon=True,
        )
        animation_thread.start()

        try:
            result = review.handle_review_decision(bot, channel, thread_ts, user, decision, request_id)
        except Exception as exc:
            log.exception("Review decision error: %s", exc)
            result = "Sorry, something went wrong while processing that review decision."
        finally:
            stop_animation.set()
            animation_thread.join()

        slack.update_message(channel, thinking_ts, result, token=bot.bot_token)
        record_thread_messages(
            thread_ts,
            {"role": "user", "content": f"<@{user}> said: {question}"},
            {"role": "assistant", "content": result},
        )
        return thinking_ts

    if not question:
        answer = (
            "Hi! Ask me anything about our Databricks catalogs, tables, columns, "
            "jobs, user's access control or data lineage."
        )
        return slack.post_message(channel, answer, thread_ts, token=bot.bot_token)

    log.info("Running agent for question: %.200s (bot_id=%s)", question, bot_id)
    # Posted immediately (before the potentially slow agent loop) and animated in place while
    # it runs, so the user sees the bot visibly working rather than staring at silence for
    # however long the tool-calling loop takes.
    thinking_ts = slack.post_message(channel, ":pepe-pray:", thread_ts, token=bot.bot_token)
    stop_animation = threading.Event()
    animation_thread = threading.Thread(
        target=_animate_thinking,
        args=(channel, thinking_ts, bot.bot_token, stop_animation),
        daemon=True,
    )
    animation_thread.start()

    try:
        answer = run_agent(question, thread_ts, channel, user_id=user, bot=bot)
    except Exception as exc:
        log.exception("Agent error: %s", exc)
        answer = "Sorry, I ran into an error while processing your question. Please try again :hugging_face:."
    finally:
        # Stop and join before touching thinking_ts ourselves — otherwise a final animation
        # edit still in flight could land after (and clobber) the real answer below.
        stop_animation.set()
        animation_thread.join()

    if not answer:
        # run_agent returns "" when a tool call already posted everything the user needs
        # (e.g. the data access request button) — remove the placeholder, nothing left to reply with.
        slack.delete_message(channel, thinking_ts, token=bot.bot_token)
        return thread_ts

    chunks = slack.split_message(answer)
    slack.update_message(channel, thinking_ts, chunks[0], token=bot.bot_token)
    ts = thinking_ts
    for chunk in chunks[1:]:
        ts = slack.post_message(channel, chunk, thread_ts, token=bot.bot_token)
    log.info("Posted reply (%d chunk(s)) to %s (thread %s)", len(chunks), channel, thread_ts)
    return ts


def process_scheduled_question(channel: str, question: str, bot_id: str, **kwargs) -> str:
    """Run the agent for a scheduled question, then post the question with a results summary
    as the thread root and the full answer as a reply inside that thread.

    The thread root is posted first (as a placeholder) so its timestamp can be used as the
    history key while the agent runs; it's then edited in place once the answer and summary
    are ready. Using the real thread timestamp from the start means the thread can be used
    to continue the conversation with follow-up @mentions afterward.

    Args:
        channel: Slack channel ID to post into.
        question: The question text to send to the agent.
        bot_id: Bot identifier used to load per-bot config (token, skills, admin users).

    Returns:
        The Slack message timestamp (ts) of the agent's detailed reply.
    """
    bot = get_bot(bot_id)
    log.info("Running scheduled agent for channel=%s question=%.200s (bot_id=%s)", channel, question, bot_id)

    header_ts = slack.post_message(channel, f"*Scheduled question:* _{question}_", token=bot.bot_token)

    try:
        answer = run_agent(question, header_ts, channel, bot=bot)
        summary = summarize_answer(question, answer)
    except Exception as exc:
        log.exception("Scheduled agent error: %s", exc)
        answer = "Sorry, I ran into an error while processing the scheduled question."
        summary = answer

    slack.update_message(
        channel,
        header_ts,
        f"*Scheduled question:* _{question}_\n\n{summary}",
        token=bot.bot_token,
    )

    chunks = slack.split_message(answer)
    ts = header_ts
    for chunk in chunks:
        ts = slack.post_message(channel, chunk, header_ts, token=bot.bot_token)
    log.info("Posted scheduled answer (%d chunk(s)) to %s (thread %s)", len(chunks), channel, header_ts)
    return ts


def process_pr_reminder(schedule_id: str, bot_id: str) -> None:
    """Run one tick of the PR reminder in the `schedules` row `schedule_id`; see agent.pr_reminders.service.run_reminder.

    It posts into the PRs' own threads, so there is no reply to return.
    """
    run_reminder(get_bot(bot_id), schedule_id)


def notify_access_request_button_expired(bot_id: str, channel: str, thread_ts: str) -> None:
    """Tell the thread that a clicked "Open Form" button is past its TTL and won't open.

    Args:
        bot_id: Bot identifier used to load per-bot config (token).
        channel: Slack channel ID the button was posted into.
        thread_ts: Thread timestamp to reply into.
    """
    bot = get_bot(bot_id)
    slack.post_message(
        channel,
        "This request button has expired. Please ask again to get a new one.",
        thread_ts,
        token=bot.bot_token,
    )


def process_data_access_submission(
    bot_id: str,
    channel: str,
    thread_ts: str,
    requester_id: str | None,
    request_type: str,
    reviewers: list[str],
    fields: dict,
) -> str:
    """Load the bot and hand a submitted data access request form to the access-control flow.

    See agent.access_control.submission.submit_data_access_request for what this does.

    Returns:
        The Slack message timestamp (ts) of the last posted review-request chunk.
    """
    bot = get_bot(bot_id)
    return submission.submit_data_access_request(bot, channel, thread_ts, requester_id, request_type, reviewers, fields)
