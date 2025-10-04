# app/queue_store.py
import collections
from typing import List, Dict, Any

# Simple in-memory deque-based queue
_queue = collections.deque()

def push_many(articles: List[Dict[str, Any]]):
    """Adds a list of articles to the right side of the queue."""
    _queue.extend(articles)

def pop() -> Dict[str, Any] | None:
    """Removes and returns an article from the left side of the queue.
    Returns None if the queue is empty.
    """
    try:
        return _queue.popleft()
    except IndexError:
        return None

def is_empty() -> bool:
    """Checks if the queue is empty."""
    return not _queue

# For convenience if we treat it like a class as in the user's patch
class ArticleQueue:
    @staticmethod
    def push_many(articles: List[Dict[str, Any]]):
        push_many(articles)

    @staticmethod
    def pop() -> Dict[str, Any] | None:
        return pop()

    @staticmethod
    def is_empty() -> bool:
        return is_empty()
