# app/main.py
import logging
import sys
import time
import random

from .config import (
    DELAY_SECONDS, IDLE_SLEEP, FEED_REFRESH_MIN, FEED_REFRESH_MAX
)
from .store import Database
from .feeds import fetch_all_feeds
from . import queue_store
from . import worker
from .ai_processor import AIProcessor

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/app.log", mode='a', encoding='utf-8')
    ]
)

log = logging.getLogger(__name__)

def initialize_database():
    """Initializes the database and ensures tables are created."""
    log.info("Verifying database schema...")
    try:
        db = Database()
        db.initialize()
        db.close()
        log.info("Database verification successful.")
    except Exception as e:
        log.critical(f"Failed to initialize database: {e}", exc_info=True)
        sys.exit(1)

def run_247():
    """Runs the 24/7 worker loop."""
    log.info("Starting 24/7 worker loop: 1 worker, ~%ds delay, feed refresh ~%d-%d min.", 
             DELAY_SECONDS, FEED_REFRESH_MIN / 60, FEED_REFRESH_MAX / 60)

    initialize_database()

    # These instances are created once and passed to the worker
    ai_processor = AIProcessor()
    db = Database()

    last_refresh = 0.0
    refresh_interval = random.randint(FEED_REFRESH_MIN, FEED_REFRESH_MAX)

    try:
        while True:
            now = time.monotonic()

            # 1. Refresh feeds if it's time
            if not queue_store.is_empty() and (now - last_refresh >= refresh_interval):
                log.info("Feed refresh interval reached.")
                try:
                    new_items_count = fetch_all_feeds()
                    if new_items_count > 0:
                        log.info(f"Refreshed feeds and enqueued {new_items_count} new articles.")
                    else:
                        log.info("Feed refresh completed, no new articles found.")
                except Exception:
                    log.exception("Error during feed refresh cycle.")
                last_refresh = now
                refresh_interval = random.randint(FEED_REFRESH_MIN, FEED_REFRESH_MAX)

            # 2. Pop one article from the queue
            article = queue_store.pop()

            if article:
                log.info(f"Popped article {article.get('id')} from queue.")
                # 3. Process the article
                success = worker.process_one_article(article, ai_processor, db)
                
                if success:
                    log.info(f"Successfully processed article {article.get('id')}.")
                else:
                    log.warning(f"Failed to process article {article.get('id')}. It may have been discarded.")

                # 4. Target cadence between articles
                sleep_duration = DELAY_SECONDS + random.randint(0, 10)
                log.info(f"Sleeping for {sleep_duration}s before next article.")
                time.sleep(sleep_duration)
            else:
                # Queue is empty, sleep for a short while
                if last_refresh == 0: # First run, fetch feeds immediately
                    log.info("Queue is empty, running initial feed fetch.")
                    try:
                        fetch_all_feeds()
                    except Exception:
                        log.exception("Error during initial feed fetch.")
                    last_refresh = time.monotonic()
                
                log.info(f"Queue is empty. Sleeping for {IDLE_SLEEP}s.")
                time.sleep(IDLE_SLEEP)

    except KeyboardInterrupt:
        log.info("Worker loop interrupted by user.")
    except Exception:
        log.critical("A critical error occurred in the main worker loop.", exc_info=True)
    finally:
        db.close()
        log.info("Worker loop terminated.")

if __name__ == "__main__":
    run_247()