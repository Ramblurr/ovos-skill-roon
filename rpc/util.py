import time
import uuid
from typing import Any, Callable, Coroutine
from .schema import Message


def unique_id() -> str:
    return str(uuid.uuid4()).replace("-", "")


def current_time_us() -> int:
    return int(time.time() * 1e6)


ErrorHandlerFn = Callable[[Message, Message], Coroutine[Any, Any, None]]
