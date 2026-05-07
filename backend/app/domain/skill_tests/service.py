from __future__ import annotations

import posixpath
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domain.compiler.models import ArtifactObject
from app.domain.runtime.schemas import AppendTerminalEventRequest, CreateInvocationRequest
from app.domain.runtime.service import RuntimeService
from app.domain.skill_tests.models import SkillTestCase, SkillTestDataObject, SkillTestRun
from app.domain.skill_tests.repository import SkillTestRepository
from app.domain.skill_tests.schemas import (
    DeleteSkillTestDataResponse,
    SendSkillTestDataRequest,
    SendSkillTestDataResponse,
    SkillTestCaseCreateRequest,
    SkillTestCaseResponse,
    SkillTestCaseUpdateRequest,
    SkillTestDataObjectResponse,
    SkillTestRunResponse,
    SkillTestRunSummary,
    StartSkillTestRunRequest,
)
from app.domain.skills.exceptions import SkillConflictError, SkillNotFoundError, SkillValidationError
from app.domain.skills.models import now_utc
from app.gateway.inference import LlmInferenceGateway
from app.infra.object_store import ObjectStoreService


class SkillTestService:
    def __init__(
        self,
        *,
        settings: Settings,
        inference_gateway: LlmInferenceGateway,
        object_store: ObjectStoreService,
        repository: SkillTestRepository | None = None,
        runtime_service: RuntimeService | None = None,
    ) -> None:
        self.settings = settings
        self.inference_gateway = inference_gateway
        self.object_store = object_store
        self.repository = repository or SkillTestRepository()
        self.runtime_service = runtime_service or RuntimeService(settings=settings, inference_gateway=inference_gateway)

    def list_cases(self, session: Session, skill_id: str) -> list[SkillTestCaseResponse]:
        self._get_skill(session, skill_id)
        return [self._build_case_response(session, item) for item in self.repository.list_cases(session, skill_id)]

    def create_case(self, session: Session, skill_id: str, payload: SkillTestCaseCreateRequest) -> SkillTestCaseResponse:
        self._get_skill(session, skill_id)
        self._validate_target_artifact(session, skill_id, payload.target_compile_artifact_id)
        case = SkillTestCase(
            skill_definition_id=skill_id,
            target_compile_artifact_id=payload.target_compile_artifact_id,
            name=self._normalize_case_name(payload.name),
            description=payload.description,
            target_version_selector=payload.target_version_selector or "latest",
            input_envelope=self._build_case_input_envelope(
                explicit_initial_events=payload.initial_terminal_events,
                legacy_input_envelope=payload.input_envelope,
            ),
            terminal_context=self._normalize_terminal_context(payload.terminal_context),
            assertions=payload.assertions,
            status="active",
        )
        session.add(case)
        session.commit()
        return self._build_case_response(session, case)

    def get_case(self, session: Session, skill_id: str, case_id: str) -> SkillTestCaseResponse:
        case = self._get_case(session, skill_id, case_id)
        return self._build_case_response(session, case)

    def update_case(
        self,
        session: Session,
        skill_id: str,
        case_id: str,
        payload: SkillTestCaseUpdateRequest,
    ) -> SkillTestCaseResponse:
        case = self._get_case(session, skill_id, case_id)
        if "target_compile_artifact_id" in payload.model_fields_set:
            self._validate_target_artifact(session, skill_id, payload.target_compile_artifact_id)
            case.target_compile_artifact_id = payload.target_compile_artifact_id
        if payload.name is not None:
            case.name = self._normalize_case_name(payload.name)
        if payload.description is not None:
            case.description = payload.description
        if payload.target_version_selector is not None:
            case.target_version_selector = payload.target_version_selector or "latest"
        if "initial_terminal_events" in payload.model_fields_set:
            case.input_envelope = self._build_case_input_envelope(
                explicit_initial_events=payload.initial_terminal_events or [],
                legacy_input_envelope={},
            )
        elif payload.input_envelope is not None:
            case.input_envelope = payload.input_envelope
        if payload.terminal_context is not None:
            case.terminal_context = self._normalize_terminal_context(payload.terminal_context)
        if payload.assertions is not None:
            case.assertions = payload.assertions
        if payload.status is not None:
            if payload.status not in {"active", "archived"}:
                raise SkillValidationError("测试 case 状态无效。", details={"status": payload.status})
            case.status = payload.status
        session.commit()
        return self._build_case_response(session, case)

    def delete_case(self, session: Session, skill_id: str, case_id: str) -> SkillTestCaseResponse:
        case = self._get_case(session, skill_id, case_id)
        case.status = "archived"
        session.commit()
        return self._build_case_response(session, case)

    def upload_data_object(
        self,
        session: Session,
        skill_id: str,
        case_id: str,
        *,
        filename: str,
        content: bytes,
        mime_type: str,
        name: str | None = None,
        description: str = "",
        role: str = "input",
    ) -> SkillTestDataObjectResponse:
        case = self._get_case(session, skill_id, case_id)
        self._validate_upload(filename=filename, content=content, mime_type=mime_type)
        safe_filename = self._safe_filename(filename)
        object_key = posixpath.join("skill-tests", skill_id, case_id, f"{uuid.uuid4()}-{safe_filename}")
        metadata = {
            "skill_id": skill_id,
            "test_case_id": case.id,
            "filename": safe_filename,
            "role": role or "input",
        }
        stored = self.object_store.upload_bytes(
            object_key=object_key,
            content=content,
            media_type=mime_type,
            metadata=metadata,
        )
        artifact_object = ArtifactObject(
            bucket=stored.bucket,
            object_key=stored.object_key,
            media_type=stored.media_type,
            size_bytes=stored.size_bytes,
            checksum=stored.checksum,
            content_json={
                "kind": "skill_test_data",
                "filename": safe_filename,
                "name": name or safe_filename,
                "description": description,
                "role": role or "input",
                "metadata": stored.metadata,
            },
        )
        session.add(artifact_object)
        session.flush()
        data_object = SkillTestDataObject(
            skill_definition_id=skill_id,
            test_case_id=case.id,
            artifact_object_id=artifact_object.id,
            name=name or safe_filename,
            description=description,
            role=role or "input",
            filename=safe_filename,
            mime_type=stored.media_type,
            size_bytes=stored.size_bytes,
            checksum=stored.checksum,
        )
        session.add(data_object)
        session.commit()
        return self._build_data_response(data_object)

    def list_data_objects(self, session: Session, skill_id: str, case_id: str) -> list[SkillTestDataObjectResponse]:
        self._get_case(session, skill_id, case_id)
        return [self._build_data_response(item) for item in self.repository.list_data_objects(session, case_id)]

    def delete_data_object(
        self,
        session: Session,
        skill_id: str,
        case_id: str,
        data_id: str,
    ) -> DeleteSkillTestDataResponse:
        self._get_case(session, skill_id, case_id)
        data_object = self.repository.get_data_object(session, data_id)
        if not data_object or data_object.test_case_id != case_id:
            raise SkillNotFoundError("未找到测试数据。", details={"data_id": data_id})
        session.delete(data_object)
        session.commit()
        return DeleteSkillTestDataResponse(deleted=True, data_id=data_id)

    def start_run(
        self,
        session: Session,
        skill_id: str,
        case_id: str,
        payload: StartSkillTestRunRequest,
    ) -> SkillTestRunResponse:
        skill = self._get_skill(session, skill_id)
        case = self._get_case(session, skill_id, case_id)
        open_test_run = self._get_open_test_run(session, case.id)
        if open_test_run:
            raise SkillConflictError(
                "当前测试 Case 已存在进行中测试，请关闭或继续已进行中的测试。",
                details={
                    "active_test_run_id": open_test_run.id,
                    "run_id": open_test_run.run_id,
                    "status": open_test_run.status,
                },
            )
        data_objects = self.repository.list_data_objects(session, case_id)
        available_data_ids = {item.id for item in data_objects}
        selected_ids = (
            payload.selected_data_object_ids
            if "selected_data_object_ids" in payload.model_fields_set
            else [item.id for item in data_objects]
        )
        invalid_ids = sorted(set(selected_ids) - available_data_ids)
        if invalid_ids:
            raise SkillValidationError("测试数据不属于当前 case。", details={"data_ids": invalid_ids})
        initial_events = self._initial_events_for_run(case=case, payload=payload)
        input_envelope = {"initial_terminal_events": initial_events} if initial_events else {}
        terminal_context = self._normalize_terminal_context(payload.terminal_context_override or case.terminal_context)
        if not isinstance(terminal_context.get("test_context"), dict):
            terminal_context["test_context"] = {}
        terminal_context["test_context"].update(
            {
                "skill_test_case_id": case.id,
                "selected_data_object_ids": selected_ids,
            }
        )
        test_run = SkillTestRun(
            skill_definition_id=skill_id,
            test_case_id=case.id,
            status="running",
            selected_data_object_ids=selected_ids,
            input_envelope=input_envelope,
            assertion_summary={"total": len(case.assertions or []), "passed": 0, "failed": 0, "pending": len(case.assertions or [])},
            started_at=now_utc(),
        )
        session.add(test_run)
        session.flush()

        invocation = self.runtime_service.create_invocation(
            session,
            CreateInvocationRequest(
                skill_key=skill.key,
                version_selector=case.target_version_selector or "latest",
                compile_artifact_id=case.target_compile_artifact_id,
                input_envelope={},
                gateway_type="terminal",
                terminal_context=terminal_context,
            ),
        )
        test_run = self.repository.get_test_run(session, test_run.id) or test_run
        test_run.invocation_id = invocation.id
        test_run.run_id = invocation.run_id
        session.commit()
        if invocation.run_id and initial_events:
            for index, event in enumerate(initial_events, start=1):
                self.runtime_service.append_terminal_event(
                    session,
                    invocation.run_id,
                    self._append_request_from_initial_event(
                        event,
                        external_event_id=f"skill-test-run:{test_run.id}:initial:{index}",
                    ),
                )
        return self.evaluate_run(session, test_run.id)

    def list_runs(self, session: Session, skill_id: str, case_id: str) -> list[SkillTestRunResponse]:
        self._get_case(session, skill_id, case_id)
        runs = self.repository.list_runs(session, case_id)
        for item in runs:
            self._sync_test_run_status(session, item)
        return [self._build_run_response(item) for item in runs]

    def get_run(self, session: Session, test_run_id: str) -> SkillTestRunResponse:
        test_run = self.repository.get_test_run(session, test_run_id)
        if not test_run:
            raise SkillNotFoundError("未找到测试运行。", details={"test_run_id": test_run_id})
        self._sync_test_run_status(session, test_run)
        return self._build_run_response(test_run)

    def send_data(
        self,
        session: Session,
        test_run_id: str,
        payload: SendSkillTestDataRequest,
    ) -> SendSkillTestDataResponse:
        test_run = self.repository.get_test_run(session, test_run_id)
        if not test_run or not test_run.run_id:
            raise SkillNotFoundError("未找到可注入数据的测试运行。", details={"test_run_id": test_run_id})
        data_object = self.repository.get_data_object(session, payload.test_data_object_id)
        if not data_object or data_object.test_case_id != test_run.test_case_id:
            raise SkillValidationError("测试数据不属于当前测试运行。", details={"data_id": payload.test_data_object_id})
        selected_ids = list(test_run.selected_data_object_ids or [])
        if payload.test_data_object_id not in set(selected_ids):
            selected_ids.append(payload.test_data_object_id)
            test_run.selected_data_object_ids = selected_ids

        event_payload = {
            "filename": data_object.filename,
            "name": data_object.name,
            "role": data_object.role,
            "description": data_object.description,
            **(payload.payload_inline or {}),
        }
        appended = self.runtime_service.append_terminal_event(
            session,
            test_run.run_id,
            AppendTerminalEventRequest(
                direction="input",
                event_kind=payload.event_kind or "terminal.file.input.v1",
                mime_type=data_object.mime_type,
                payload_inline=event_payload,
                artifact_object_id=data_object.artifact_object_id,
                external_event_id=f"skill-test-run:{test_run.id}:data:{data_object.id}:{uuid.uuid4()}",
            ),
        )
        return SendSkillTestDataResponse(
            accepted=True,
            terminal_event=appended.event.model_dump(mode="json"),
        )

    def evaluate_run(self, session: Session, test_run_id: str) -> SkillTestRunResponse:
        test_run = self.repository.get_test_run(session, test_run_id)
        if not test_run:
            raise SkillNotFoundError("未找到测试运行。", details={"test_run_id": test_run_id})
        case = self.repository.get_case(session, test_run.test_case_id)
        if not case:
            raise SkillNotFoundError("未找到测试 case。", details={"case_id": test_run.test_case_id})
        if not test_run.run_id:
            test_run.assertion_results = []
            test_run.assertion_summary = {"total": len(case.assertions or []), "passed": 0, "failed": 0, "pending": len(case.assertions or [])}
            test_run.status = "running"
            session.commit()
            return self._build_run_response(test_run)

        replay = self.runtime_service.build_replay(session, test_run.run_id)
        results = [self._evaluate_assertion(assertion, index=index, replay=replay) for index, assertion in enumerate(case.assertions or [], start=1)]
        summary = {
            "total": len(results),
            "passed": sum(1 for item in results if item["status"] == "passed"),
            "failed": sum(1 for item in results if item["status"] == "failed"),
            "pending": sum(1 for item in results if item["status"] == "pending"),
        }
        if summary["failed"]:
            test_run.status = "failed"
            test_run.ended_at = test_run.ended_at or now_utc()
        elif summary["pending"] or replay.run.status in {"running", "queued", "waiting_input"}:
            test_run.status = "running"
        else:
            test_run.status = "passed"
            test_run.ended_at = test_run.ended_at or now_utc()
        test_run.assertion_results = results
        test_run.assertion_summary = summary
        session.commit()
        return self._build_run_response(test_run)

    def _evaluate_assertion(self, assertion: dict[str, Any], *, index: int, replay) -> dict[str, Any]:
        assertion_type = str(assertion.get("type") or "")
        label = assertion.get("label") or assertion_type or f"assertion-{index}"
        result = {
            "id": str(assertion.get("id") or f"assertion-{index}"),
            "type": assertion_type,
            "label": label,
            "status": "failed",
            "expected": assertion,
            "actual": None,
            "message": "",
        }
        run_status = replay.run.status
        run_is_open = run_status in {"queued", "running", "waiting_input"}

        if assertion_type == "run.status_equals":
            expected = str(assertion.get("status") or assertion.get("expected") or "succeeded")
            result["actual"] = run_status
            result["status"] = "passed" if run_status == expected else ("pending" if run_is_open else "failed")
            result["message"] = f"run.status is {run_status}, expected {expected}"
            return result

        if assertion_type in {"final_output_contains", "final_output_not_contains"}:
            text = str(assertion.get("text") or assertion.get("contains") or "")
            final_output = replay.run.final_output or ""
            contains = text in final_output
            expected_contains = assertion_type == "final_output_contains"
            result["actual"] = final_output
            if contains == expected_contains:
                result["status"] = "passed"
            elif run_is_open and not final_output:
                result["status"] = "pending"
            else:
                result["status"] = "failed"
            result["message"] = f"final_output {'contains' if contains else 'does not contain'} {text!r}"
            return result

        if assertion_type == "trace_event_exists":
            event_type = str(assertion.get("event_type") or assertion.get("expected") or "")
            exists = any(event.event_type == event_type for event in replay.trace_events)
            result["actual"] = [event.event_type for event in replay.trace_events]
            result["status"] = "passed" if exists else ("pending" if run_is_open else "failed")
            result["message"] = f"trace event {event_type!r} {'exists' if exists else 'not found'}"
            return result

        if assertion_type == "terminal_event_exists":
            event_kind = str(assertion.get("event_kind") or "")
            direction = str(assertion.get("direction") or "")
            contains = str(assertion.get("contains") or "")
            events = replay.terminal_events
            matched = [
                event
                for event in events
                if (not event_kind or event.event_kind == event_kind)
                and (not direction or event.direction == direction)
                and (not contains or contains in str(event.payload_inline or ""))
            ]
            result["actual"] = [
                {"direction": event.direction, "event_kind": event.event_kind, "payload_inline": event.payload_inline}
                for event in events
            ]
            result["status"] = "passed" if matched else ("pending" if run_is_open else "failed")
            result["message"] = "terminal event exists" if matched else "terminal event not found"
            return result

        result["status"] = "failed"
        result["message"] = f"unsupported assertion type: {assertion_type}"
        return result

    def _sync_test_run_status(self, session: Session, test_run: SkillTestRun) -> None:
        if not test_run.run_id:
            return
        run = self.runtime_service.get_run(session, test_run.run_id)
        if run.status in {"queued", "running", "waiting_input"}:
            if test_run.status != "running":
                test_run.status = "running"
                session.commit()
            return
        if test_run.status == "running":
            self.evaluate_run(session, test_run.id)

    def _get_open_test_run(self, session: Session, case_id: str) -> SkillTestRun | None:
        for test_run in self.repository.list_open_runs(session, case_id):
            self._sync_test_run_status(session, test_run)
        return next(
            (
                test_run
                for test_run in self.repository.list_open_runs(session, case_id)
                if test_run.status in {"pending", "queued", "running", "waiting_input"}
            ),
            None,
        )

    def _get_skill(self, session: Session, skill_id: str):
        skill = self.repository.get_skill(session, skill_id)
        if not skill or skill.status == "archived":
            raise SkillNotFoundError("未找到 Skill。", details={"skill_id": skill_id})
        return skill

    def _get_case(self, session: Session, skill_id: str, case_id: str) -> SkillTestCase:
        case = self.repository.get_case(session, case_id)
        if not case or case.skill_definition_id != skill_id or case.status == "archived":
            raise SkillNotFoundError("未找到测试 case。", details={"skill_id": skill_id, "case_id": case_id})
        return case

    def _validate_target_artifact(self, session: Session, skill_id: str, artifact_id: str | None) -> None:
        if not artifact_id:
            return
        artifact = self.repository.get_artifact(session, artifact_id)
        if not artifact:
            raise SkillValidationError("指定编译产物不存在。", details={"compile_artifact_id": artifact_id})
        if artifact.status != "ready":
            raise SkillValidationError("指定编译产物尚不可运行。", details={"compile_artifact_id": artifact_id})
        skill_version = self.repository.get_skill_version(session, artifact.skill_version_id)
        if not skill_version or skill_version.skill_definition_id != skill_id:
            raise SkillValidationError("指定编译产物不属于当前 Skill。", details={"compile_artifact_id": artifact_id})

    @staticmethod
    def _normalize_case_name(name: str) -> str:
        normalized = name.strip()
        if not normalized:
            raise SkillValidationError("测试 case 名称不能为空。")
        return normalized

    def _validate_upload(self, *, filename: str, content: bytes, mime_type: str) -> None:
        if not filename:
            raise SkillValidationError("上传文件名不能为空。")
        if not content:
            raise SkillValidationError("上传文件不能为空。")
        if len(content) > self.settings.test_data_max_upload_bytes:
            raise SkillValidationError("上传文件过大。", details={"max_bytes": self.settings.test_data_max_upload_bytes})
        if not self._is_allowed_mime_type(mime_type):
            raise SkillValidationError("不支持的测试数据 MIME 类型。", details={"mime_type": mime_type})

    @staticmethod
    def _is_allowed_mime_type(mime_type: str) -> bool:
        if mime_type.startswith(("text/", "image/", "audio/", "video/")):
            return True
        return mime_type in {"application/json", "application/pdf", "application/octet-stream"}

    @staticmethod
    def _safe_filename(filename: str) -> str:
        cleaned = filename.replace("\\", "/").split("/")[-1].strip()
        return cleaned or "upload.bin"

    @staticmethod
    def _normalize_terminal_context(value: dict[str, Any] | None) -> dict[str, Any]:
        context = dict(value or {})
        context.setdefault("terminal_kind", "web")
        context.setdefault("operator_mode", "manual")
        context.setdefault(
            "supported_inputs",
            ["terminal.text.input.v1", "terminal.file.input.v1", "terminal.image.input.v1"],
        )
        context.setdefault("supported_outputs", ["terminal.text.output.v1", "terminal.markdown.output.v1"])
        return context

    def _build_case_input_envelope(
        self,
        *,
        explicit_initial_events: list[dict[str, Any]] | None,
        legacy_input_envelope: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if explicit_initial_events is not None and len(explicit_initial_events) > 0:
            return {"initial_terminal_events": self._normalize_initial_terminal_events(explicit_initial_events)}

        legacy_events = self._initial_events_from_input_envelope(legacy_input_envelope or {})
        return {"initial_terminal_events": legacy_events} if legacy_events else {}

    def _initial_events_for_run(self, *, case: SkillTestCase, payload: StartSkillTestRunRequest) -> list[dict[str, Any]]:
        if payload.initial_terminal_events:
            return self._normalize_initial_terminal_events(payload.initial_terminal_events)
        if payload.input_override:
            return self._initial_events_from_input_envelope(payload.input_override)
        if payload.send_case_initial_events:
            return self._extract_case_initial_events(case)
        return []

    def _extract_case_initial_events(self, case: SkillTestCase) -> list[dict[str, Any]]:
        return self._initial_events_from_input_envelope(case.input_envelope or {})

    def _initial_events_from_input_envelope(self, input_envelope: dict[str, Any]) -> list[dict[str, Any]]:
        initial_events = input_envelope.get("initial_terminal_events")
        if isinstance(initial_events, list):
            return self._normalize_initial_terminal_events(initial_events)
        if "user_input" in input_envelope:
            return self._text_initial_event(input_envelope["user_input"])
        if "text" in input_envelope:
            return self._text_initial_event(input_envelope["text"])
        return []

    def _text_initial_event(self, value: Any) -> list[dict[str, Any]]:
        if value is None:
            return []
        text = str(value).strip()
        if not text:
            return []
        return [
            {
                "direction": "input",
                "event_kind": "terminal.text.input.v1",
                "mime_type": "text/plain",
                "payload_inline": text,
                "source": {"kind": "web"},
            }
        ]

    def _normalize_initial_terminal_events(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for index, event in enumerate(events, start=1):
            item = dict(event or {})
            direction = str(item.get("direction") or "input").strip().lower()
            if direction != "input":
                raise SkillValidationError(
                    "测试 case 的首轮终端事件只能是 input。",
                    details={"index": index, "direction": direction},
                )
            event_kind = str(item.get("event_kind") or "terminal.text.input.v1").strip()
            mime_type = str(item.get("mime_type") or "text/plain").strip()
            if not event_kind or not mime_type:
                raise SkillValidationError("首轮终端事件必须包含 event_kind 与 mime_type。", details={"index": index})
            payload_inline = item.get("payload_inline")
            artifact_object_id = item.get("artifact_object_id")
            if payload_inline in (None, "") and not artifact_object_id:
                continue
            normalized.append(
                {
                    "direction": "input",
                    "event_kind": event_kind,
                    "mime_type": mime_type,
                    "payload_inline": payload_inline,
                    "artifact_object_id": artifact_object_id,
                    "source": item.get("source") or {"kind": "web"},
                }
            )
        return normalized

    @staticmethod
    def _append_request_from_initial_event(
        event: dict[str, Any],
        *,
        external_event_id: str,
    ) -> AppendTerminalEventRequest:
        return AppendTerminalEventRequest(
            direction=str(event.get("direction") or "input"),
            event_kind=str(event.get("event_kind") or "terminal.text.input.v1"),
            mime_type=str(event.get("mime_type") or "text/plain"),
            payload_inline=event.get("payload_inline"),
            artifact_object_id=event.get("artifact_object_id"),
            source=event.get("source") or {"kind": "web"},
            external_event_id=external_event_id,
        )

    def _build_case_response(self, session: Session, case: SkillTestCase) -> SkillTestCaseResponse:
        latest_run = self.repository.get_latest_run(session, case.id)
        return SkillTestCaseResponse(
            id=case.id,
            skill_definition_id=case.skill_definition_id,
            name=case.name,
            description=case.description,
            target_version_selector=case.target_version_selector,
            target_compile_artifact_id=case.target_compile_artifact_id,
            initial_terminal_events=self._extract_case_initial_events(case),
            input_envelope=case.input_envelope,
            terminal_context=case.terminal_context,
            assertions=case.assertions,
            status=case.status,
            latest_run=self._build_run_summary(latest_run) if latest_run else None,
            created_at=case.created_at,
            updated_at=case.updated_at,
        )

    @staticmethod
    def _build_data_response(data_object: SkillTestDataObject) -> SkillTestDataObjectResponse:
        return SkillTestDataObjectResponse(
            id=data_object.id,
            skill_definition_id=data_object.skill_definition_id,
            test_case_id=data_object.test_case_id,
            artifact_object_id=data_object.artifact_object_id,
            name=data_object.name,
            description=data_object.description,
            role=data_object.role,
            filename=data_object.filename,
            mime_type=data_object.mime_type,
            size_bytes=data_object.size_bytes,
            checksum=data_object.checksum,
            created_at=data_object.created_at,
        )

    @staticmethod
    def _build_run_response(test_run: SkillTestRun) -> SkillTestRunResponse:
        return SkillTestRunResponse(
            id=test_run.id,
            skill_definition_id=test_run.skill_definition_id,
            test_case_id=test_run.test_case_id,
            invocation_id=test_run.invocation_id,
            run_id=test_run.run_id,
            status=test_run.status,
            selected_data_object_ids=test_run.selected_data_object_ids,
            initial_terminal_events=SkillTestService._initial_events_from_run_envelope(test_run.input_envelope),
            input_envelope=test_run.input_envelope,
            assertion_results=test_run.assertion_results,
            assertion_summary=test_run.assertion_summary,
            started_at=test_run.started_at,
            ended_at=test_run.ended_at,
            created_at=test_run.created_at,
            updated_at=test_run.updated_at,
        )

    @staticmethod
    def _build_run_summary(test_run: SkillTestRun) -> SkillTestRunSummary:
        return SkillTestRunSummary(
            id=test_run.id,
            status=test_run.status,
            run_id=test_run.run_id,
            assertion_summary=test_run.assertion_summary,
            created_at=test_run.created_at,
            ended_at=test_run.ended_at,
        )

    @staticmethod
    def _initial_events_from_run_envelope(input_envelope: dict[str, Any]) -> list[dict[str, Any]]:
        initial_events = input_envelope.get("initial_terminal_events")
        return initial_events if isinstance(initial_events, list) else []
