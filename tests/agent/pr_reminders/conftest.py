import pytest

from connectors.db.bots import BotConfig


@pytest.fixture
def bot():
    return BotConfig(bot_id="bot-a", bot_token="xoxb-test", signing_secret=b"secret", app_id="A_BOT")


@pytest.fixture
def pr_node():
    """Factory for the GraphQL PR node `agent.pr_reminders.queries` asks GitHub for."""

    def build(state="OPEN", is_draft=False, decision="REVIEW_REQUIRED", title="Fix it", created_at="2026-11-01T10:00:00Z", reviews=(), total_reviews=None):
        return {
            "url": "https://github.com/org/repo/pull/1",
            "title": title,
            "state": state,
            "isDraft": is_draft,
            "reviewDecision": decision,
            "createdAt": created_at,
            "latestOpinionatedReviews": {
                "totalCount": len(reviews) if total_reviews is None else total_reviews,
                "nodes": [{"state": review_state} for review_state in reviews],
            },
        }

    return build
