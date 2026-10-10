"""AgentDFT bridge: the interface a real DFT backend will implement, plus a mock.

The mock validates the handoff, stores it in a job folder and records the job as
"started (simulated)". It never runs a calculation.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path

from magview import osdeps
from magview.io.handoff import validate_handoff

ENV_JOBS_DIR = "MAGVIEW_AGENTDFT_DIR"


@dataclass(frozen=True)
class ValidationReport:
    ok: bool
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class JobTicket:
    job_id: str
    status: str
    path: str
    history: list[dict]
    message: str = ""


class HandoffRejected(ValueError):
    def __init__(self, report: ValidationReport) -> None:
        super().__init__("handoff rejected: " + "; ".join(report.errors))
        self.report = report


class AgentDFTBridge(ABC):
    """What MagView needs from any DFT backend."""

    @abstractmethod
    def validate(self, handoff: dict) -> ValidationReport: ...

    @abstractmethod
    def submit(self, handoff: dict) -> JobTicket: ...

    @abstractmethod
    def status(self, job_id: str) -> JobTicket: ...


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


class MockAgentDFTBridge(AgentDFTBridge):
    def __init__(self, root: str | Path | None = None) -> None:
        env = os.environ.get(ENV_JOBS_DIR)
        self.root = Path(root or env or osdeps.data_dir() / "agentdft_jobs")

    def validate(self, handoff: dict) -> ValidationReport:
        errors = validate_handoff(handoff)
        return ValidationReport(not errors, errors)

    def submit(self, handoff: dict) -> JobTicket:
        report = self.validate(handoff)
        if not report.ok:
            raise HandoffRejected(report)
        job_id = _dt.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
        job_dir = self.root / job_id
        job_dir.mkdir(parents=True, exist_ok=False)
        (job_dir / "handoff.json").write_text(json.dumps(handoff, indent=1), encoding="utf-8")
        kind = "non-collinear" if handoff["moments"]["collinear"] is False else "collinear"
        history = [
            {"t": _now(), "status": "received", "detail": "handoff validated"},
            {"t": _now(), "status": "started (simulated)",
             "detail": f"mock backend: would run a {kind} spin-polarized calculation on "
                       f"{len(handoff['magnetic_cell']['species'])} sites"
                       + (" and unfold onto the reference cell" if handoff["requires_unfolding"] else "")},
        ]
        ticket = JobTicket(job_id, "started (simulated)", str(job_dir), history,
                           "Mock AgentDFT: no calculation is actually run.")
        (job_dir / "job.json").write_text(json.dumps(asdict(ticket), indent=1), encoding="utf-8")
        return ticket

    def status(self, job_id: str) -> JobTicket:
        path = self.root / job_id / "job.json"
        if not path.is_file():
            raise KeyError(f"unknown job {job_id}")
        return JobTicket(**json.loads(path.read_text(encoding="utf-8")))
