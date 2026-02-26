import sys
from redis import Redis
from rq import Worker, Queue
from config import settings
from loguru import logger

logger.add(sys.stdout, level=settings.log_level)
logger.add(settings.log_path, serialize=True, rotation="00:00", retention="30 days")

if __name__ == "__main__":
    redis_conn = Redis.from_url(settings.redis_url)
    queues = [Queue(name, connection=redis_conn) for name in ["high", "default", "low"]]
    logger.info(f"RQ Worker starting — queues: {[q.name for q in queues]}")
    Worker(queues, connection=redis_conn).work()
