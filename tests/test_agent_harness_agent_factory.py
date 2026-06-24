from __future__ import annotations

from app.agent_harness.agents.demo.psop_harness_agent.agent import make_demo_agent
from app.agent_harness.context import AgentBuildContext
from app.agent_harness.definitions import default_agent_registry
from app.agent_harness.events import AgentEventWriter
from app.agent_harness.factory import create_psop_agent
from app.agent_harness.memory.file_store import FileMemoryStore
from app.agent_harness.models.scripted_chat_model import ScriptedToolCallingChatModel
from app.agent_harness.sandbox.local import LocalAgentSandboxProvider
from app.agent_harness.schemas import AgentInvocation
from app.agent_harness.skills.loader import SkillLoader
from app.core.config import Settings


def test_make_demo_agent_returns_invokable_agent(tmp_path) -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        agent_harness_sandbox_root=str(tmp_path / "agent-runs"),
    )
    package = default_agent_registry(settings.backend_root).load("demo.psop_harness_agent")
    sandbox = LocalAgentSandboxProvider(settings).acquire(input_payload={"text": "demo"})
    writer = AgentEventWriter(sandbox.events_path)
    skill_loader = SkillLoader(settings.repo_root / "skills")
    definition = package.definition
    memory_store = FileMemoryStore(sandbox.memory_path)
    context = AgentBuildContext(
        settings=settings,
        invocation=AgentInvocation(agent_key=definition.agent_key, input={"text": "demo"}),
        definition=definition,
        system_prompt=package.read_system_prompt(),
        memory_prompt=package.read_memory_prompt(),
        skill_metadata=[skill_loader.load_metadata(skill_name) for skill_name in definition.skills],
        sandbox=sandbox,
        event_writer=writer,
        memory_store=memory_store,
        memory_scope=definition.memory_scope or definition.agent_key,
        memory_payload={},
        skill_loader=skill_loader,
        chat_model_factory=lambda _definition: ScriptedToolCallingChatModel(),
    )

    agent = make_demo_agent(context)

    assert hasattr(agent, "invoke")


def test_create_psop_agent_uses_langchain_create_agent(monkeypatch) -> None:
    import langchain.agents

    called = {}

    def fake_create_agent(**kwargs):
        called.update(kwargs)
        return "agent"

    monkeypatch.setattr(langchain.agents, "create_agent", fake_create_agent)

    result = create_psop_agent(model="model", tools=[], system_prompt="prompt", middleware=[], name="demo")

    assert result == "agent"
    assert called["model"] == "model"
    assert called["system_prompt"] == "prompt"
