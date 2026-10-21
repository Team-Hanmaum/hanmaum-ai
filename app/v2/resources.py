"""No-queue admission and bounded workers which retain leases until actual completion."""

import asyncio
from collections.abc import Callable

from app.errors import AnalysisFailure


def overloaded():
    return AnalysisFailure(503, "AI_OVERLOADED", "분석 처리 용량이 부족합니다.")


def timed_out():
    return AnalysisFailure(504, "AI_TIMEOUT", "분석 응답 대기 시간이 초과되었습니다.")


class Capacity:
    """Accessed only from the owning event loop; no waiting queue."""

    def __init__(self, limit: int):
        if type(limit) is not int or limit < 1:
            raise ValueError("Capacity must be a positive integer")
        self.limit = limit
        self.active = 0

    def acquire(self):
        if self.active >= self.limit:
            raise overloaded()
        self.active += 1
        return Lease(self)


class Lease:
    def __init__(self, capacity: Capacity):
        self.capacity = capacity
        self.references = 1

    def hold(self, task: asyncio.Task):
        self.references += 1
        task.add_done_callback(lambda _: self.release())

    def release(self):
        self.references -= 1
        if self.references == 0:
            self.capacity.active -= 1


class Workers:
    def __init__(self, capacity: int = 4):
        self.capacity = Capacity(capacity)
        self.tasks: set[asyncio.Task] = set()

    async def run(self, function: Callable, *args, budget: float = 1, leases=(), failure=None):
        if budget <= 0:
            raise failure or timed_out()
        # Admission saturation is distinct from a submitted storage operation timing out.
        lease = self.capacity.acquire()
        task = asyncio.create_task(asyncio.to_thread(function, *args))
        self.tasks.add(task)
        for owner in leases:
            if owner is not None:
                owner.hold(task)

        def finished(task):
            self.tasks.discard(task)
            lease.release()
            if not task.cancelled():
                task.exception()  # Never leave a worker traceback containing input in asyncio logs.

        task.add_done_callback(finished)
        done, _ = await asyncio.wait({task}, timeout=budget)
        if not done:
            # Do not cancel to_thread: its underlying work may still commit a receipt.
            raise failure or timed_out()
        return task.result()
