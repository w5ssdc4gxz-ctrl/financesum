"""Celery application configuration."""
from celery import Celery
from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "financesum",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "app.tasks.fetch",
        "app.tasks.parse",
        "app.tasks.analyze"
    ]
)

# Configure Celery
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,  # 1 hour max
    task_soft_time_limit=3000,  # 50 minutes soft limit
)












