"""Extension boundary for explicitly authorized API/CLI integrations."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Assignment:
    task_id: str
    model: str
    prompt_path: Path
    result_path: Path


@dataclass(frozen=True)
class Receipt:
    external_id: str
    status: str


class Provider(ABC):
    """Adapters must be opt-in; do not log credentials or use browser sessions.

    Future API/CLI adapters implement this boundary, including their own timeouts,
    retry policy, budget and explicit authorization. Core only consumes UTF-8 text.
    """

    @abstractmethod
    def dispatch(self, assignment: Assignment) -> Receipt:
        """Dispatch an assignment, returning an opaque tracking identifier."""

    @abstractmethod
    def collect(self, assignment: Assignment, receipt: Receipt) -> str | None:
        """Return answer text when available; None means still pending."""


class ManualProvider(Provider):
    def dispatch(self, assignment: Assignment) -> Receipt:
        # Generating a prompt does not mean a web task has actually been sent.
        return Receipt(str(assignment.prompt_path), "awaiting_manual_submission")

    def collect(self, assignment: Assignment, receipt: Receipt) -> str | None:
        if not assignment.result_path.exists():
            return None
        return assignment.result_path.read_text(encoding="utf-8-sig")
