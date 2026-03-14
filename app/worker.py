import logging
import signal
import time

from app.config import get_settings
from app.services.process_job_runner import run_next_process_job

settings = get_settings()

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)

_running = True


def _stop_worker(signum, frame) -> None:
    global _running
    _running = False
    logger.info("Process job worker stopping after signal %s", signum)


def main() -> int:
    global _running
    signal.signal(signal.SIGINT, _stop_worker)
    signal.signal(signal.SIGTERM, _stop_worker)

    poll_seconds = max(1, settings.process_job_runner_poll_seconds)
    logger.info("Process job worker started with poll interval %ss", poll_seconds)

    while _running:
        processed = run_next_process_job()
        if processed:
            continue
        time.sleep(poll_seconds)

    logger.info("Process job worker stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
