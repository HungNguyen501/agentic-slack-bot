"""Composition root of the worker service: the one place that builds adapters and the use cases that run on them."""
import functools

from agent.dispatch import ToolServices
from agent.pr_reminders.message import VOICE_SPEC as PR_REMINDER_VOICE_SPEC
from bots.core.ports import BotRepository
from bots.infra.supabase_bots import SupabaseBotRepository
from channel_analysis.core.service import ChannelAnalysisService
from channel_analysis.infra.openai_extraction import OpenAIExtractionModel
from channel_analysis.infra.slack_files import SlackChannelFiles
from channel_analysis.infra.slack_history import SlackChannelHistory
from characters.core.models import FEATURE_PR_REMINDER
from characters.core.service import CharacterService
from characters.infra.files_source import FilesCharacterSource
from characters.infra.known_skills import SupabaseKnownSkills
from characters.infra.redis_thread_characters import RedisThreadCharacterStore
from characters.infra.supabase_characters import SupabaseCharacterRepository
from common.configs import AgentConfigs, Configs
from connectors.redis_client import client as redis_client
from dataset_sync.core.service import DatasetSyncService
from dataset_sync.infra.databricks_fetcher import DatabricksDatasetFetcher
from dataset_sync.infra.slack_notifier import SlackFailureNotifier
from dataset_sync.infra.supabase_datasets import SupabaseDatasetRepository
from schedules.core.ports import ScheduleRepository
from schedules.infra.supabase_schedules import SupabaseScheduleRepository


@functools.cache
def bot_repository() -> BotRepository:
    return SupabaseBotRepository()


@functools.cache
def schedule_repository() -> ScheduleRepository:
    return SupabaseScheduleRepository()


@functools.cache
def dataset_sync_service() -> DatasetSyncService:
    return DatasetSyncService(SupabaseDatasetRepository(), DatabricksDatasetFetcher(), schedule_repository(), SlackFailureNotifier())


@functools.cache
def character_service() -> CharacterService:
    return CharacterService(
        SupabaseCharacterRepository(),
        RedisThreadCharacterStore(redis_client, AgentConfigs.THREAD_CHARACTER_TTL_SECONDS),
        SupabaseKnownSkills(),
        FilesCharacterSource(),
        {FEATURE_PR_REMINDER: PR_REMINDER_VOICE_SPEC},
    )


@functools.cache
def channel_analysis_service() -> ChannelAnalysisService:
    # The service keeps a little per-run state (the files of a message an analysis call deferred), which is only safe because
    # `rq worker` forks a process per job; run in-process it would carry that state from one job to the next.
    return ChannelAnalysisService(SlackChannelHistory(), SlackChannelFiles(), OpenAIExtractionModel(Configs.EXTRACTION_MODEL))


@functools.cache
def tool_services() -> ToolServices:
    return ToolServices(
        schedules=schedule_repository(),
        dataset_sync=dataset_sync_service(),
        characters=character_service(),
        channel_analysis=channel_analysis_service(),
    )
