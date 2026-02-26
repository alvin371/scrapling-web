import sys
from loguru import logger
from app.config import settings


def setup_logging():
    logger.remove()
    fmt = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "{message}"
    )
    logger.add(sys.stdout, format=fmt, level=settings.log_level, colorize=True)
    logger.add(
        settings.log_path,
        level=settings.log_level,
        rotation="00:00",
        retention="30 days",
        serialize=True,
        compression="gz",
    )
    return logger
