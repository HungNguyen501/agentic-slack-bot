"""Shared Redis client."""
from redis import Redis

from common.configs import Configs

client = Redis.from_url(Configs.REDIS_URL)
