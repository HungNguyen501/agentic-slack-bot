"""Composition root of the worker service: the one place that builds adapters and the use cases that run on them."""
import functools

from agent.dispatch import ToolServices
from bots.core.ports import BotRepository
from bots.infra.supabase_bots import SupabaseBotRepository
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
def tool_services() -> ToolServices:
    return ToolServices(schedules=schedule_repository(), dataset_sync=dataset_sync_service())
