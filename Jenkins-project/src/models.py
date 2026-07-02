from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BuildInfo:
    job_name: str
    build_number: int
    result: str
    url: str
    duration_ms: int
    timestamp: int
    branch: str = ""
    triggered_by: str = ""
    console_log: str = ""
    parameters: dict = field(default_factory=dict)
    git_url: str = ""
    commit_sha: str = ""
    jenkinsfile: str = ""
    repo_files_text: str = ""
    node_name: str = ""
    node_status: str = ""
    node_offline_reason: str = ""
    node_temporarily_offline: bool = False
    node_num_executors: int | None = None

    @property
    def duration_human(self) -> str:
        secs = self.duration_ms // 1000
        m, s = divmod(secs, 60)
        return f"{m}m {s}s" if m else f"{s}s"


@dataclass
class DiagnosticResult:
    root_cause: str
    error_type: str
    affected_component: str
    fix_steps: list[str]
    confidence: str
    suggested_action: str
    raw_response: str = ""
    error_locations: list[dict] = field(default_factory=list)
