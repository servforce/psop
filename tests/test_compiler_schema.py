from __future__ import annotations

from app.compiler import schemas as compiler_schemas
from app.compiler.models import ArtifactObject, EgCompileArtifact, PSkillCompileRequest
from app.compiler.service import CompilerService
from app.infra.database import Base, DatabaseManager
from app.jobs.models import RuntimeJob
from app.pskills.models import PSkillDefinition, PSkillVersion


def test_compiler_tables_use_pskill_compile_request_naming() -> None:
    manager = DatabaseManager("sqlite+pysqlite:///:memory:")
    manager.create_schema()

    tables = set(Base.metadata.tables)

    assert "pskill_compile_request" in tables
    assert "skill_compile_request" not in tables
    assert Base.metadata.tables["eg_compile_artifact"].c.compile_request_id is not None
    assert Base.metadata.tables["compile_diagnostic"].c.compile_request_id is not None


def test_compiler_response_models_do_not_expose_legacy_compile_request_aliases() -> None:
    assert "skill_compile_request_id" not in compiler_schemas.CompileDiagnosticResponse.model_fields
    assert "skill_compile_request_id" not in compiler_schemas.CompileArtifactResponse.model_fields
    assert "compile_request_id" in compiler_schemas.CompileDiagnosticResponse.model_fields
    assert "compile_request_id" in compiler_schemas.CompileArtifactResponse.model_fields


def test_compiler_span_attributes_include_closed_loop_provenance() -> None:
    service = object.__new__(CompilerService)
    definition = PSkillDefinition(
        id="pskill-1",
        key="span-demo",
        name="Span Demo",
        gitlab_project_id="gitlab-project-1",
        repository_url="https://gitlab.example.local/skills/span-demo",
    )
    version = PSkillVersion(
        id="pskill-version-1",
        pskill_definition_id=definition.id,
        version_no=3,
        status="published",
        source_ref="main",
        source_commit_sha="commit-sha-1",
    )
    compile_request = PSkillCompileRequest(
        id="compile-request-1",
        pskill_definition_id=definition.id,
        pskill_version_id=version.id,
        agent_run_id="agent-run-compiler-1",
        trigger_type="publish",
        source_commit_sha="commit-sha-1",
        status="running",
        dedupe_key="compile:pskill-version-1:commit-sha-1",
    )
    artifact_object = ArtifactObject(
        id="artifact-object-1",
        bucket="psop-artifacts",
        object_key="skills/span-demo/eg.compile.artifact.json",
        media_type="application/json",
        checksum="checksum-1",
    )
    artifact = EgCompileArtifact(
        id="compile-artifact-1",
        compile_request_id=compile_request.id,
        pskill_version_id=version.id,
        artifact_object_id=artifact_object.id,
        formal_revision="psop-eg-formal-v5",
        artifact_version="psop-eg-formal-v5/test",
        graph_summary={"node_count": 1},
        capability_summary={"tool_count": 0},
        status="ready",
    )
    job = RuntimeJob(
        id="job-compile-1",
        job_type="compile",
        status="running",
        payload={
            "compile_request_id": compile_request.id,
            "pskill_definition_id": definition.id,
            "pskill_version_id": version.id,
            "published_commit_sha": "commit-sha-1",
            "publish_record_id": "publish-record-1",
        },
        compile_request_id=compile_request.id,
        dedupe_key="job:compile:compile-request-1",
    )

    attributes = service._compiler_span_attributes(
        job=job,
        compile_request=compile_request,
        pskill_definition=definition,
        pskill_version=version,
        artifact=artifact,
        artifact_object=artifact_object,
        custom_attribute="custom-value",
    )

    assert attributes["compile_request_id"] == compile_request.id
    assert attributes["pskill_definition_id"] == definition.id
    assert attributes["skill_id"] == definition.id
    assert attributes["pskill_version_id"] == version.id
    assert attributes["skill_version_id"] == version.id
    assert attributes["compile_artifact_id"] == artifact.id
    assert attributes["artifact_object_id"] == artifact_object.id
    assert attributes["agent_run_id"] == compile_request.agent_run_id
    assert attributes["job_id"] == job.id
    assert attributes["publish_record_id"] == "publish-record-1"
    assert attributes["source_commit_sha"] == "commit-sha-1"
    assert attributes["custom_attribute"] == "custom-value"
