"""
test_slack.py — Connectivity verification for Slack using slack_sdk.

Requirements from .env:
- SLACK_BOT_TOKEN: Bot user OAuth token (starts with xoxb-).
- SLACK_CHANNEL_ID: Slack channel ID (e.g. C0123456789) where bot has access.
"""

import os
import sys
from dotenv import load_dotenv
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# Ensure UTF-8 output on Windows consoles
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

load_dotenv(override=True)


def test_slack():
    token = os.getenv("SLACK_BOT_TOKEN", "").strip()
    channel_id = os.getenv("SLACK_CHANNEL_ID", "").strip()

    if not token or not channel_id:
        print("[FAILURE] Missing Slack credentials.")
        print("Please configure SLACK_BOT_TOKEN and SLACK_CHANNEL_ID in your .env file.")
        sys.exit(1)

    client = WebClient(token=token)

    # 1. Verify Bot Token Authentication
    try:
        print("Verifying Slack Bot Token via auth.test...")
        auth_info = client.auth_test()
        bot_user = auth_info.get("user", "unknown")
        bot_id = auth_info.get("user_id", "unknown")
        team = auth_info.get("team", "unknown")
        print(f"       Authenticated as bot: {bot_user} (ID: {bot_id}) on team: {team}")
    except SlackApiError as exc:
        print(f"[FAILURE] Slack authentication failed: {exc.response.get('error', exc)}")
        sys.exit(1)
    except Exception as exc:
        print(f"[FAILURE] Slack connection error: {exc}")
        sys.exit(1)

    # 2. Ensure bot is in channel and post a test message
    try:
        try:
            client.conversations_join(channel=channel_id)
        except SlackApiError:
            pass  # If channel is already joined, private, or cannot be joined directly, proceed to chat_postMessage

        print(f"Posting test message to channel {channel_id}...")
        message_text = "🚀 *AgentLens Guardian* connectivity test: Slack bot can successfully post messages!"
        result = client.chat_postMessage(
            channel=channel_id,
            text=message_text,
        )

        message_ts = result.get("ts")
        posted_channel = result.get("channel")
        print(f"       Message successfully delivered to channel: {posted_channel} (ts: {message_ts})")

    except SlackApiError as exc:
        err = exc.response.get("error", str(exc))
        print(f"[FAILURE] Failed to post Slack message: {err}")
        if err == "channel_not_found":
            print("Tip: Make sure the channel ID is correct and the bot has been invited to the channel (/invite @bot_name).")
        elif err == "not_in_channel":
            print("Tip: The bot must be invited to the channel first (/invite @bot_name).")
        sys.exit(1)
    except Exception as exc:
        print(f"[FAILURE] Unexpected error posting to Slack: {exc}")
        sys.exit(1)

    print("\n[SUCCESS] Slack integration verified successfully (chat:write operational).")


if __name__ == "__main__":
    test_slack()
