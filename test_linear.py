"""
test_linear.py — Connectivity verification for Linear using its GraphQL API.

Requirements from .env:
- LINEAR_API_KEY: Personal API key from Linear (Settings -> Security & Access -> Personal API keys).
- LINEAR_TEAM_ID (optional): Team ID or team key (e.g. "KUN"). If omitted, the script selects the first available team.
"""

import os
import sys
import requests
from dotenv import load_dotenv

# Ensure UTF-8 output on Windows consoles
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

load_dotenv(override=True)

LINEAR_GRAPHQL_ENDPOINT = "https://api.linear.app/graphql"


def execute_graphql(api_key: str, query: str, variables: dict | None = None) -> dict:
    headers = {
        "Content-Type": "application/json",
        "Authorization": api_key,
    }
    payload = {"query": query}
    if variables:
        payload["variables"] = variables

    response = requests.post(LINEAR_GRAPHQL_ENDPOINT, json=payload, headers=headers, timeout=15)

    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text}")

    result = response.json()
    if "errors" in result and result["errors"]:
        error_msgs = "; ".join([e.get("message", str(e)) for e in result["errors"]])
        raise RuntimeError(f"Linear GraphQL error: {error_msgs}")

    return result.get("data", {})


def test_linear():
    api_key = os.getenv("LINEAR_API_KEY", "").strip()
    configured_team = os.getenv("LINEAR_TEAM_ID", "").strip()

    if not api_key:
        print("[FAILURE] Missing Linear API key.")
        print("Please configure LINEAR_API_KEY in your .env file.")
        sys.exit(1)

    print("Connecting to Linear GraphQL API...")

    # 1. Query teams and resolve target team UUID
    try:
        teams_query = """
        query {
            teams {
                nodes {
                    id
                    name
                    key
                }
            }
        }
        """
        data = execute_graphql(api_key, teams_query)
        teams = data.get("teams", {}).get("nodes", [])

        if not teams:
            print("[FAILURE] No teams found in this Linear workspace.")
            sys.exit(1)

        resolved_team = None
        if configured_team:
            for team in teams:
                if (
                    configured_team.lower() == team["id"].lower()
                    or configured_team.lower() == team["key"].lower()
                    or configured_team.lower() == team["name"].lower()
                ):
                    resolved_team = team
                    break
            if not resolved_team:
                available = ", ".join([f"{t['name']} (key: {t['key']}, id: {t['id']})" for t in teams])
                print(f"[FAILURE] Team '{configured_team}' not found. Available teams: {available}")
                sys.exit(1)
        else:
            resolved_team = teams[0]

        team_id = resolved_team["id"]
        print(f"       Target team resolved: '{resolved_team['name']}' (Key: {resolved_team['key']}, ID: {team_id})")

    except Exception as exc:
        print(f"[FAILURE] Failed to query Linear teams: {exc}")
        sys.exit(1)

    # 2. Create a test issue to verify write capability
    try:
        print("Creating test issue in Linear...")
        mutation = """
        mutation CreateTestIssue($input: IssueCreateInput!) {
            issueCreate(input: $input) {
                success
                issue {
                    id
                    identifier
                    title
                    url
                }
            }
        }
        """
        variables = {
            "input": {
                "teamId": team_id,
                "title": "AgentLens Guardian - Test Issue",
                "description": "Verification issue created by test_linear.py to confirm Linear GraphQL API connectivity and write permissions.",
            }
        }

        data = execute_graphql(api_key, mutation, variables)
        issue_create_payload = data.get("issueCreate", {})

        if not issue_create_payload.get("success"):
            raise RuntimeError(f"Issue creation was unsuccessful: {issue_create_payload}")

        issue = issue_create_payload.get("issue", {})
        issue_id = issue.get("id")
        issue_identifier = issue.get("identifier")
        issue_url = issue.get("url")

        print(f"       Created Issue Identifier: {issue_identifier}")
        print(f"       Created Issue ID:         {issue_id}")
        if issue_url:
            print(f"       Issue URL:                {issue_url}")

    except Exception as exc:
        print(f"[FAILURE] Failed to create issue in Linear: {exc}")
        sys.exit(1)

    print(f"\n[SUCCESS] Linear integration verified successfully (Issue ID: {issue_id}).")


if __name__ == "__main__":
    test_linear()
