"""`create-agent`: the setup helper that turns a founder/admin's own login
into a fresh agent member and its one-time API key.

Plain httpx calls against the real SMAC API (login, then register) --
this is a bootstrapping tool, not a tool the running bridge uses, so it
does not go through `SmacApi`. It does reuse `SmacApiError` and
`_envelope_message` so failures read the same way in both places.
"""

import asyncio
import getpass
import os
import sys
from typing import Any

import httpx

from smac_mcp.api import SmacApiError, _envelope_message


async def create_agent(
    base_url: str,
    workspace_id: str,
    email: str,
    password: str,
    agent_name: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict:
    """Log in as a human member and register `agent_name` as a new agent.

    Returns the POST /members/agents response: member_id, member_name,
    member_type, handle, and a one-time api_key that is never retrievable
    again.

    Raises SmacApiError, carrying the server's own message, when the
    server is unreachable, when login fails (wrong workspace_id/email/
    password all surface the login endpoint's identical uniform message,
    by design, so this can't be used to probe which of the three was
    wrong), or when agent creation fails (e.g. the logged-in member isn't
    human and so can't manage membership).
    """
    kwargs: dict[str, Any] = {"base_url": base_url.rstrip("/"), "timeout": 10.0}
    if transport is not None:
        kwargs["transport"] = transport

    async with httpx.AsyncClient(**kwargs) as client:
        try:
            login_response = await client.post(
                "/auth/login",
                json={
                    "workspace_id": workspace_id,
                    "email": email,
                    "password": password,
                },
            )
        except httpx.HTTPError:
            raise SmacApiError(
                f"SMAC server is not reachable at {base_url} — is it running?"
            )
        if not login_response.is_success:
            raise SmacApiError(_envelope_message(login_response))
        access_token = login_response.json()["access_token"]

        try:
            create_response = await client.post(
                "/members/agents",
                json={"member_name": agent_name},
                headers={"Authorization": f"Bearer {access_token}"},
            )
        except httpx.HTTPError:
            raise SmacApiError(
                f"SMAC server is not reachable at {base_url} — is it running?"
            )
        if not create_response.is_success:
            raise SmacApiError(_envelope_message(create_response))
        result: dict = create_response.json()
        return result


def run_interactive() -> None:
    """Prompt for a workspace, a founder/admin's own credentials, and the
    new agent's name; create the agent and print its one-time API key.

    The password is read via getpass (never echoed, never logged). On a
    SmacApiError (bad credentials, unreachable server, ...) prints the
    server's own message to stderr and exits with status 1.
    """
    base_url = os.environ.get("SMAC_URL", "http://127.0.0.1:8000")
    workspace_id = input("Workspace ID: ")
    email = input("Your email: ")
    password = getpass.getpass("Your password: ")
    agent_name = input("Agent name: ")

    try:
        result = asyncio.run(
            create_agent(
                base_url=base_url,
                workspace_id=workspace_id,
                email=email,
                password=password,
                agent_name=agent_name,
            )
        )
    except SmacApiError as exc:
        print(f"Error: {exc.message}", file=sys.stderr)
        raise SystemExit(1)

    print(f"Created agent '{result['member_name']}' (@{result['handle']}).")
    print(f"API key: {result['api_key']}")
    print(
        "This key is shown exactly once — store it now "
        "(e.g. `export SMAC_API_KEY=...`); SMAC cannot show it to you again."
    )
