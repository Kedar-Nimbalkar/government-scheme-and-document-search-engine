"""Cooperative work deadline used by E2's synchronous request handlers."""

from time import monotonic


class RequestTimeout(RuntimeError):
    pass


class Budget:
    def __init__(self, seconds: float):
        self.deadline = monotonic() + seconds

    def remaining(self) -> float:
        remaining = self.deadline - monotonic()
        if remaining <= 0:
            raise RequestTimeout("Query processing budget exhausted")
        return remaining

    def check(self):
        self.remaining()
