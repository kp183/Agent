"""
pipeline.py — Live End-to-End Orchestration Pipeline for AgentLens Guardian.

Wires together Gmail (IMAP/SMTP), Guardian Decision Engine, Slack, and Linear
with post-action verification and simulated failure recovery.
"""

import os
import sys
import argparse
import imaplib
import smtplib
import email
from email.header import decode_header
from email.message import EmailMessage
from email.utils import parseaddr
import json
from datetime import datetime, timezone
import requests
from dotenv import load_dotenv
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# Ensure UTF-8 output on Windows consoles for screen recording
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

load_dotenv(override=True)

# 1. Import Guardian Decision Engine
from guardian import (
    evaluate_action_policy,
    extract_intent_signals,
    ActionTier,
    EvaluationResult,
    load_policy,
)

# Reference account email on file (used for identity mismatch verification in demos)
# Default is "aditi.sharma@example.com". Can also be set in .env or modified below.
ACCOUNT_EMAIL_ON_FILE = os.getenv("ACCOUNT_EMAIL_ON_FILE", "aditi.sharma@example.com")

LINEAR_GRAPHQL_ENDPOINT = "https://api.linear.app/graphql"
DECISIONS_LOG_PATH = os.getenv("DECISIONS_LOG_PATH", "decisions.jsonl")


def log_decision(entry: dict, log_path: str = DECISIONS_LOG_PATH) -> None:
    """Appends an evaluation decision record to structured JSONL log."""
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print(f"[LOGGED] Decision recorded to {log_path}")
    except Exception as exc:
        print(f"[WARNING] Failed to write decision log: {exc}")


def get_decision_summary_counts(log_path: str = DECISIONS_LOG_PATH) -> dict:
    """Reads decisions.jsonl and calculates decision counts across runs."""
    counts = {
        "AUTONOMOUS": 0,
        "GUARDED": 0,
        "BLOCKED": 0,
        "RECOVERED": 0,
    }
    if not os.path.exists(log_path):
        return counts

    try:
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    tier = record.get("tier")
                    if tier in counts:
                        counts[tier] += 1
                    if record.get("post_action_verification") == "TRIGGERED_FALLBACK":
                        counts["RECOVERED"] += 1
                except json.JSONDecodeError:
                    continue
    except Exception as exc:
        print(f"[WARNING] Failed to parse {log_path}: {exc}")

    return counts


def decode_mime_header(header_value: str | None) -> str:
    """Decodes MIME-encoded header fields into plain text."""
    if not header_value:
        return "(no subject)"
    decoded_fragments = decode_header(header_value)
    subject_parts = []
    for fragment, encoding in decoded_fragments:
        if isinstance(fragment, bytes):
            subject_parts.append(fragment.decode(encoding or "utf-8", errors="replace"))
        else:
            subject_parts.append(str(fragment))
    return "".join(subject_parts)


def extract_body(msg) -> str:
    """Extracts plain text body from an email Message object."""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))
            if content_type == "text/plain" and "attachment" not in content_disposition:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    body = payload.decode(charset, errors="replace")
                    break
        if not body:
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        body = payload.decode(charset, errors="replace")
                        break
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            body = payload.decode(charset, errors="replace")
    return body.strip()


# 2. Fetch Latest Unread Email via IMAP
def fetch_latest_unread_email(subject_filter: str | None = None, mark_read: bool = True) -> dict | None:
    """
    Connects to Gmail via IMAP and retrieves the most recent unread email.
    If subject_filter is provided, finds the latest unread email matching the subject keyword.
    Marks the fetched email as read (\\Seen) if mark_read is True.
    """
    gmail_address = os.getenv("GMAIL_ADDRESS", "").strip()
    raw_password = os.getenv("GMAIL_APP_PASSWORD", "").strip()
    gmail_password = raw_password.replace(" ", "")

    if not gmail_address or not gmail_password:
        print("[FAILED] Missing GMAIL_ADDRESS or GMAIL_APP_PASSWORD in .env")
        return None

    try:
        print("[STEP] Connecting to Gmail IMAP (imap.gmail.com)...")
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        mail.login(gmail_address, gmail_password)
        # Select with readonly=False so we can mark processed emails as read
        mail.select("INBOX", readonly=False)

        if subject_filter:
            print(f"[STEP] Searching unread emails for subject keyword '{subject_filter}'...")
            status, response = mail.search(None, f'(UNSEEN SUBJECT "{subject_filter}")')
        else:
            status, response = mail.search(None, "UNSEEN")

        if status != "OK":
            print(f"[FAILED] IMAP search failed: {response}")
            mail.logout()
            return None

        email_ids = response[0].split()
        if not email_ids:
            if subject_filter:
                print(f"[INFO] No unread emails matching '{subject_filter}' found in INBOX.")
            else:
                print("[INFO] No unread emails found in INBOX.")
            mail.logout()
            return None

        latest_id = email_ids[-1]
        fetch_status, msg_data = mail.fetch(latest_id, "(RFC822)")
        if fetch_status != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
            print(f"[FAILED] Could not fetch email ID {latest_id}")
            mail.logout()
            return None

        raw_email = msg_data[0][1]
        parsed_msg = email.message_from_bytes(raw_email)

        raw_subject = parsed_msg.get("Subject", "")
        subject = decode_mime_header(raw_subject)
        from_header = parsed_msg.get("From", "")
        _, sender_email = parseaddr(from_header)
        body = extract_body(parsed_msg)

        # Mark as read so subsequent runs won't re-process the same email
        if mark_read:
            mail.store(latest_id, "+FLAGS", "\\Seen")
            print(f"[STEP] Marked email ID {latest_id.decode()} as READ in Gmail.")

        mail.close()
        mail.logout()

        print(f"[RESULT] Fetched unread email (ID: {latest_id.decode()}):")
        print(f"         From:    {sender_email}")
        print(f"         Subject: {subject}")
        print(f"         Body:    {body[:120]}{'...' if len(body) > 120 else ''}")

        return {
            "subject": subject,
            "body": body,
            "sender_email": sender_email,
        }

    except Exception as exc:
        print(f"[FAILED] Gmail IMAP error: {exc}")
        return None


# 3. Send Gmail Reply via SMTP
def send_gmail_reply(to_email: str, subject: str, body: str) -> bool:
    """Sends an email response to the customer via Gmail SMTP."""
    gmail_address = os.getenv("GMAIL_ADDRESS", "").strip()
    raw_password = os.getenv("GMAIL_APP_PASSWORD", "").strip()
    gmail_password = raw_password.replace(" ", "")

    if not gmail_address or not gmail_password:
        print("[FAILED] Missing Gmail credentials for reply.")
        return False

    try:
        print(f"[STEP] Sending Gmail reply to {to_email}...")
        reply_msg = EmailMessage()
        reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        reply_msg["Subject"] = reply_subject
        reply_msg["From"] = gmail_address
        reply_msg["To"] = to_email
        reply_msg.set_content(body)

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp_server:
            smtp_server.login(gmail_address, gmail_password)
            smtp_server.send_message(reply_msg)

        print(f"[RESULT] Reply successfully delivered to {to_email}.")
        return True
    except Exception as exc:
        print(f"[FAILED] Failed to send Gmail reply: {exc}")
        return False


# 4. Post Message to Slack
def post_slack_message(text: str) -> bool:
    """Posts an update/alert message to the designated Slack channel."""
    token = os.getenv("SLACK_BOT_TOKEN", "").strip()
    channel_id = os.getenv("SLACK_CHANNEL_ID", "").strip()

    if not token or not channel_id:
        print("[FAILED] Missing SLACK_BOT_TOKEN or SLACK_CHANNEL_ID in .env")
        return False

    try:
        print(f"[STEP] Posting notification to Slack channel {channel_id}...")
        client = WebClient(token=token)
        response = client.chat_postMessage(channel=channel_id, text=text)
        print(f"[RESULT] Slack notification posted (ts: {response.get('ts')}).")
        return True
    except SlackApiError as exc:
        print(f"[FAILED] Slack API error: {exc.response.get('error', exc)}")
        return False
    except Exception as exc:
        print(f"[FAILED] Unexpected Slack error: {exc}")
        return False


# 5. Create Linear Issue (with simulated failure support)
def create_linear_issue(title: str, description: str, simulate_failure: bool = False) -> dict:
    """
    Creates a ticket in Linear via GraphQL.
    If simulate_failure is True, mocks a silent failure returning {} without calling API.
    """
    if simulate_failure:
        print("[STEP] Creating Linear issue (SIMULATION MODE: Silent API failure injected)...")
        # Simulates Linear silently returning an empty body / no usable ticket payload
        return {}

    api_key = os.getenv("LINEAR_API_KEY", "").strip()
    configured_team = os.getenv("LINEAR_TEAM_ID", "").strip()

    if not api_key:
        print("[FAILED] Missing LINEAR_API_KEY in .env")
        return {}

    try:
        print("[STEP] Creating Linear issue via GraphQL API...")
        headers = {
            "Content-Type": "application/json",
            "Authorization": api_key,
        }

        # Resolve target team UUID
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
        resp = requests.post(LINEAR_GRAPHQL_ENDPOINT, json={"query": teams_query}, headers=headers, timeout=15)
        teams = resp.json().get("data", {}).get("teams", {}).get("nodes", [])

        if not teams:
            print("[FAILED] No teams found in Linear workspace.")
            return {}

        resolved_team = teams[0]
        if configured_team:
            for team in teams:
                if (
                    configured_team.lower() == team["id"].lower()
                    or configured_team.lower() == team["key"].lower()
                    or configured_team.lower() == team["name"].lower()
                ):
                    resolved_team = team
                    break

        team_id = resolved_team["id"]

        mutation = """
        mutation CreateIssue($input: IssueCreateInput!) {
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
                "title": title,
                "description": description,
            }
        }

        resp = requests.post(
            LINEAR_GRAPHQL_ENDPOINT,
            json={"query": mutation, "variables": variables},
            headers=headers,
            timeout=15,
        )
        res_data = resp.json().get("data", {}).get("issueCreate", {})
        if not res_data.get("success"):
            print(f"[FAILED] Linear mutation returned error: {resp.text}")
            return {}

        issue = res_data.get("issue", {})
        print(f"[RESULT] Linear issue created: {issue.get('identifier')} (ID: {issue.get('id')})")
        return issue

    except Exception as exc:
        print(f"[FAILED] Linear issue creation error: {exc}")
        return {}


# 6. Post-Action Verification for Linear Creation
def verify_linear_creation(result: dict) -> bool:
    """
    Verification check: ensures the ticket creation returned a valid ticket ID.
    Catches silent failures and payload drops.
    """
    print("[STEP] Running post-action verification on Linear creation result...")
    ticket_id = result.get("id")
    if not result or not ticket_id:
        print("[VERIFICATION FAILED] Linear returned no ticket ID! (Payload is empty or missing 'id')")
        return False

    print(f"[VERIFIED] Linear ticket verified successfully! ID: {ticket_id}")
    return True


# 7. Main Pipeline Orchestration
def run_pipeline(
    simulate_linear_failure: bool = False,
    override_email: dict | None = None,
    subject_filter: str | None = None,
):
    """
    Executes the full Guardian pipeline:
    1. Ingest email
    2. Extract intent signals & evaluate policy
    3. Route according to ActionTier (AUTONOMOUS, GUARDED, BLOCKED)
    4. Post-action verification with fallback recovery
    """
    print("\n" + "=" * 80)
    print("                 AgentLens Guardian — Live Intake & Policy Pipeline              ")
    print("=" * 80)
    if simulate_linear_failure:
        print("⚠️  FLAG ENABLED: Simulating Linear API failure for recovery verification demo")
    if subject_filter:
        print(f"🔍 FILTER ENABLED: Searching for unread email matching '{subject_filter}'")
    print("=" * 80 + "\n")

    # Step 1: Ingest Email
    email_data = override_email or fetch_latest_unread_email(subject_filter=subject_filter)
    if not email_data:
        print("[INFO] No email to process. Pipeline idle.")
        return

    subject = email_data.get("subject", "")
    body = email_data.get("body", "")
    sender = email_data.get("sender_email", "")

    # Step 2: Signal Extraction & Evaluation
    print(f"\n[STEP] Evaluating policy for email from '{sender}' (Account on file: '{ACCOUNT_EMAIL_ON_FILE}')...")
    policy = load_policy()
    signals = extract_intent_signals(
        email_subject=subject,
        email_body=body,
        sender_email=sender,
        account_email_on_file=ACCOUNT_EMAIL_ON_FILE,
        policy=policy,
    )

    evaluation = evaluate_action_policy(signals, policy=policy)

    print("\n--- [EVALUATION RESULT] ---")
    print(f"Tier:             {evaluation.tier.value}")
    print(f"Risk Level:       {evaluation.risk_level}")
    print(f"Confidence Score: {evaluation.confidence_score:.2f}")
    print(f"Policy Flags:     {evaluation.policy_flags}")
    print(f"Reasoning:        {evaluation.reasoning}")
    print("---------------------------\n")

    verification_status = "N/A"

    # Step 3: Tier-Based Execution & Verification
    if evaluation.tier == ActionTier.AUTONOMOUS:
        print("[TIER: AUTONOMOUS] Executing autonomous resolution...")
        reply_body = (
            f"Hello,\n\n"
            f"Thank you for reaching out regarding '{subject}'.\n"
            f"Your request has been processed autonomously by AgentLens Guardian.\n\n"
            f"— Support Team"
        )
        send_gmail_reply(to_email=sender, subject=subject, body=reply_body)

        confidence_pct = int(evaluation.confidence_score * 100)
        slack_msg = (
            f"✅ *Ticket resolved autonomously*\n"
            f"• *Subject:* {subject}\n"
            f"• *Customer:* `{sender}`\n"
            f"• *Confidence:* `{confidence_pct}%`\n"
            f"• *Status:* Direct reply sent via Gmail"
        )
        post_slack_message(slack_msg)

    elif evaluation.tier == ActionTier.GUARDED:
        print("[TIER: GUARDED] Action requires human approval before execution...")
        confidence_pct = int(evaluation.confidence_score * 100)
        slack_msg = (
            f"⚠️ *Guardian Approval Required (GUARDED)*\n"
            f"• *Subject:* {subject}\n"
            f"• *Customer:* `{sender}`\n"
            f"• *Confidence:* `{confidence_pct}%`\n"
            f"• *Reasoning:* {evaluation.reasoning}\n"
            f"• *Policy Flags:* `{evaluation.policy_flags}`\n"
            f"• *Action:* Paused pending human review in Slack"
        )
        post_slack_message(slack_msg)

    elif evaluation.tier == ActionTier.BLOCKED:
        print("[TIER: BLOCKED] Policy violation or high risk detected. Escalating...")
        ticket_title = f"[Guardian Escalation] {subject}"
        ticket_description = (
            f"**Customer:** {sender}\n"
            f"**Email Subject:** {subject}\n"
            f"**Risk Level:** {evaluation.risk_level}\n"
            f"**Policy Flags:** {', '.join(evaluation.policy_flags)}\n"
            f"**Reasoning:** {evaluation.reasoning}\n\n"
            f"**Original Message:**\n{body}"
        )

        issue_result = create_linear_issue(
            title=ticket_title,
            description=ticket_description,
            simulate_failure=simulate_linear_failure,
        )

        # Step 4: Post-Action Verification & Fallback Recovery
        if verify_linear_creation(issue_result):
            verification_status = "SUCCEEDED"
            issue_url = issue_result.get("url", "")
            issue_identifier = issue_result.get("identifier", "Ticket")
            slack_msg = (
                f"🚫 *Blocked Action Escalated*\n"
                f"• *Customer:* `{sender}`\n"
                f"• *Subject:* {subject}\n"
                f"• *Flags:* `{evaluation.policy_flags}`\n"
                f"• *Reasoning:* {evaluation.reasoning}\n"
                f"• *Linear Issue:* <{issue_url}|{issue_identifier}> created successfully."
            )
            post_slack_message(slack_msg)
        else:
            verification_status = "TRIGGERED_FALLBACK"
            # FALLBACK RECOVERY: Critical beat of the demo
            print("[RECOVERY] Triggering emergency engineering fallback notification...")
            urgent_slack_msg = (
                f"🚨 *CRITICAL GUARDIAN ALERT — VERIFICATION FAILURE & FALLBACK RECOVERY*\n"
                f"• *Reason:* Linear ticket creation could not be verified (empty response returned)!\n"
                f"• *Customer:* `{sender}`\n"
                f"• *Subject:* {subject}\n"
                f"• *Risk Level:* `{evaluation.risk_level}`\n"
                f"• *Policy Flags:* `{evaluation.policy_flags}`\n"
                f"• *Escalation:* Direct bypass alert sent to Engineering On-Call Slack channel."
            )
            post_slack_message(urgent_slack_msg)

    # Step 5: Structured Logging to decisions.jsonl
    log_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "email_subject": subject,
        "email_snippet": body[:120].strip() + ("..." if len(body) > 120 else ""),
        "sender_email": sender,
        "confidence_score": evaluation.confidence_score,
        "risk_level": evaluation.risk_level,
        "policy_flags": evaluation.policy_flags,
        "tier": evaluation.tier.value,
        "reasoning": evaluation.reasoning,
        "post_action_verification": verification_status,
    }
    log_decision(log_entry)

    # Step 6: Post Cumulative Decision Summary to Slack
    counts = get_decision_summary_counts()
    summary_text = (
        "📊 *AgentLens Guardian run summary*\n"
        f"• *AUTONOMOUS:* {counts['AUTONOMOUS']}\n"
        f"• *GUARDED:* {counts['GUARDED']}\n"
        f"• *BLOCKED:* {counts['BLOCKED']}\n"
        f"• *Silent failures recovered:* {counts['RECOVERED']}"
    )
    print("\n[STEP] Posting decision summary to Slack...")
    post_slack_message(summary_text)

    print("\n" + "=" * 80)
    print("                   Pipeline Execution Completed Successfully                    ")
    print("=" * 80 + "\n")


# 8. CLI Interface
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AgentLens Guardian Live Pipeline")
    parser.add_argument(
        "--simulate-failure",
        action="store_true",
        help="Simulate Linear API failure to demonstrate post-action verification and recovery",
    )
    parser.add_argument(
        "--subject",
        type=str,
        default=None,
        help="Target a specific unread email by subject keyword (e.g. --subject 'Order delayed')",
    )
    args = parser.parse_args()

    run_pipeline(
        simulate_linear_failure=args.simulate_failure,
        subject_filter=args.subject,
    )
