#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agent_harness.schemas import AgentInvocation
from app.agent_harness.service import AgentHarnessService
from app.core.config import Settings
from app.gateway.inference import OpenAICompatibleInferenceGateway


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the PSOP Agent Harness demo agent.")
    parser.add_argument(
        "--input",
        required=True,
        help="现场作业描述。",
    )
    args = parser.parse_args()

    settings = Settings()
    service = AgentHarnessService(
        settings=settings,
        inference_gateway=OpenAICompatibleInferenceGateway.from_settings(settings),
    )
    result = service.invoke(
        AgentInvocation(
            agent_key="demo.psop_harness_agent",
            input={"text": args.input},
        )
    )
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0 if result.status == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
