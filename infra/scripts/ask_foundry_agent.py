#!/usr/bin/env python3
"""
Ask the cloud-hosted Edge IQ orchestrator agent in Microsoft Foundry a question.

Usage:
    python infra/scripts/ask_foundry_agent.py "Is WTP-01-PUMP-003 healthy?"

Defaults target the deployed Edge IQ project; override with
AZURE_AI_FOUNDRY_PROJECT_ENDPOINT / AZURE_AI_FOUNDRY_ORCHESTRATOR_AGENT_ID.
"""

from __future__ import annotations

import os
import sys

from azure.ai.agents import AgentsClient
from azure.identity import DefaultAzureCredential

ENDPOINT = os.getenv(
    "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT",
    "https://aif-vwcdscvow6xdu.services.ai.azure.com/api/projects/aif-vwcdscvow6xdu-proj",
)
AGENT_ID = os.getenv("AZURE_AI_FOUNDRY_ORCHESTRATOR_AGENT_ID", "asst_7dclv5bdyHNSIyAp0D8UBdZ3")


def main() -> int:
    question = " ".join(sys.argv[1:]) or "Which Edge IQ specialist should handle a pump vibration alarm, and why?"
    client = AgentsClient(
        endpoint=ENDPOINT,
        credential=DefaultAzureCredential(exclude_interactive_browser_credential=True),
    )
    thread = client.threads.create()
    client.messages.create(thread_id=thread.id, role="user", content=question)
    run = client.runs.create_and_process(thread_id=thread.id, agent_id=AGENT_ID)
    print(f"Q: {question}\nrun: {run.status}")
    if run.last_error:
        print(f"error: {run.last_error}")
        return 1
    for message in client.messages.list(thread_id=thread.id):
        if message.role == "assistant" and message.text_messages:
            print("\n" + message.text_messages[-1].text.value)
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
