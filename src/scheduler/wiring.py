"""Composition root of the scheduler service."""
import functools

from schedules.core.ports import ScheduleRepository
from schedules.infra.supabase_schedules import SupabaseScheduleRepository


@functools.cache
def schedule_repository() -> ScheduleRepository:
    return SupabaseScheduleRepository()
