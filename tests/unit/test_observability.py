from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from groundtruth.observability import (
    STAGE_LOGGER,
    LLMCallLog,
    LoggingLLMClient,
    log_stage,
)

SECRET = "sk-DEADBEEF0123456789ABCDEF"


class _Resp:
    def __init__(self) -> None:
        self.model = "m"
        self.text = f"answer mentioning {SECRET}"
        self.tool_calls: list = []

        class _U:
            prompt_tokens, completion_tokens, total_tokens = 11, 7, 18

        self.usage = _U()


class _Inner:
    def complete(self, role, messages, **kw):  # type: ignore[no-untyped-def]
        return _Resp()


class TestStageLog:
    def test_emits_a_structured_record_per_transition(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger="groundtruth.jobs"):
            log_stage("01JOB", "work", "retrieval", "start")
            log_stage("01JOB", "work", "commit", "end", seconds=1.2)
        records = [r for r in caplog.records if r.name == "groundtruth.jobs"]
        assert len(records) == 2
        assert records[0].groundtruth["stage"] == "retrieval"
        assert records[1].groundtruth["seconds"] == 1.2

    def test_stage_log_fields_are_redacted(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO, logger="groundtruth.jobs"):
            log_stage("01JOB", "work", "llm", "failed", error=f"boom {SECRET}")
        assert SECRET not in caplog.records[-1].groundtruth["error"]


class TestLLMCallLog:
    def test_off_by_default(self, tmp_path: Path) -> None:
        log = LLMCallLog(tmp_path)
        assert log.enabled is False
        log.record("01JOB", role="tag", model="m", prompt=["p"], response_text="r")
        assert not (tmp_path / "llm").exists()

    def test_when_enabled_writes_jsonl_at_the_spec_path(self, tmp_path: Path) -> None:
        log = LLMCallLog(tmp_path, enabled=True)
        log.record("01JOB", role="tag", model="m", prompt=["hi"], response_text="ok")
        log.record("01JOB", role="reduce", model="m", prompt=["hi2"], response_text="ok2")
        lines = (tmp_path / "llm" / "01JOB.jsonl").read_text().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["role"] == "tag"

    def test_no_secret_in_the_llm_log(self, tmp_path: Path) -> None:
        log = LLMCallLog(tmp_path, enabled=True)
        log.record(
            "01JOB",
            role="answer",
            model="m",
            prompt=[{"role": "user", "content": f"my token is {SECRET}"}],
            response_text=f"and again {SECRET}",
        )
        text = (tmp_path / "llm" / "01JOB.jsonl").read_text()
        assert SECRET not in text
        assert "[redacted]" in text

    def test_logging_failure_never_raises(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        log = LLMCallLog(tmp_path / "state", enabled=True)
        log._dir = tmp_path / "nope" / "x"  # type: ignore[attr-defined]
        (tmp_path / "nope").write_text("i am a file, not a dir")  # mkdir will fail
        with caplog.at_level(logging.WARNING):
            log.record("01JOB", role="tag", model="m", prompt=["p"], response_text="r")
        assert any("log write failed" in r.message for r in caplog.records)


class TestLoggingLLMClient:
    def test_wraps_and_records_each_call(self, tmp_path: Path) -> None:
        call_log = LLMCallLog(tmp_path, enabled=True)
        client = LoggingLLMClient(_Inner(), call_log, "01JOB")
        response = client.complete("answer", [{"role": "user", "content": "q"}])
        assert response.text
        record = json.loads((tmp_path / "llm" / "01JOB.jsonl").read_text().splitlines()[0])
        assert record["role"] == "answer"
        assert record["usage"]["total_tokens"] == 18
        assert SECRET not in json.dumps(record)


def test_stage_logger_name() -> None:
    assert STAGE_LOGGER.name == "groundtruth.jobs"
