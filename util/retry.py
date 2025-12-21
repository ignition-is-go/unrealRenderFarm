"""
Retry utility with exponential backoff for resilient API calls.
"""

import functools
import logging
import time


LOGGER = logging.getLogger(__name__)


def retry(max_attempts=3, backoff=2, exceptions=(Exception,)):
    """
    Decorator that retries a function with exponential backoff.

    :param max_attempts: int. maximum number of attempts
    :param backoff: float. backoff multiplier (delay = backoff ^ attempt)
    :param exceptions: tuple. exceptions to catch and retry on
    :return: decorator function
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_attempts:
                        delay = backoff ** attempt
                        LOGGER.warning(
                            '%s failed (attempt %d/%d): %s. Retrying in %ds...',
                            func.__name__, attempt, max_attempts, e, delay
                        )
                        time.sleep(delay)
                    else:
                        LOGGER.error(
                            '%s failed after %d attempts: %s',
                            func.__name__, max_attempts, e
                        )
            return None  # Return None after all retries exhausted
        return wrapper
    return decorator


def retry_call(func, args=(), kwargs=None, max_attempts=3, backoff=2, exceptions=(Exception,)):
    """
    Call a function with retry logic (non-decorator version).

    :param func: callable. function to call
    :param args: tuple. positional arguments
    :param kwargs: dict. keyword arguments
    :param max_attempts: int. maximum number of attempts
    :param backoff: float. backoff multiplier
    :param exceptions: tuple. exceptions to catch and retry on
    :return: function result or None if all retries failed
    """
    if kwargs is None:
        kwargs = {}

    last_exception = None
    for attempt in range(1, max_attempts + 1):
        try:
            return func(*args, **kwargs)
        except exceptions as e:
            last_exception = e
            if attempt < max_attempts:
                delay = backoff ** attempt
                LOGGER.warning(
                    '%s failed (attempt %d/%d): %s. Retrying in %ds...',
                    func.__name__, attempt, max_attempts, e, delay
                )
                time.sleep(delay)
            else:
                LOGGER.error(
                    '%s failed after %d attempts: %s',
                    func.__name__, max_attempts, e
                )
    return None
