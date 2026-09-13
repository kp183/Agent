"""
test_gmail.py — Connectivity verification for Gmail using IMAP and SMTP with an App Password.

Requirements from .env:
- GMAIL_ADDRESS: Google account email address.
- GMAIL_APP_PASSWORD: 16-character Google App Password (spaces will be stripped automatically).
"""

import os
import sys
import smtplib
import imaplib
import email
from email.header import decode_header
from email.message import EmailMessage
from dotenv import load_dotenv

# Ensure UTF-8 output on Windows consoles
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

load_dotenv(override=True)


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


def test_gmail():
    gmail_address = os.getenv("GMAIL_ADDRESS", "").strip()
    raw_app_password = os.getenv("GMAIL_APP_PASSWORD", "").strip()
    # Google App Passwords are 16 characters; strip internal spaces if pasted as "xxxx xxxx xxxx xxxx"
    gmail_app_password = raw_app_password.replace(" ", "")

    if not gmail_address or not gmail_app_password:
        print("[FAILURE] Missing Gmail credentials.")
        print("Please configure GMAIL_ADDRESS and GMAIL_APP_PASSWORD in your .env file.")
        sys.exit(1)

    print(f"Connecting to Gmail as {gmail_address}...")

    # 1. IMAP Verification: Connect and inspect most recent unread email
    try:
        print("[1/2] Connecting to Gmail IMAP (imap.gmail.com)...")
        imap_server = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        imap_server.login(gmail_address, gmail_app_password)
        imap_server.select("INBOX", readonly=True)

        status, response = imap_server.search(None, "UNSEEN")
        if status != "OK":
            raise RuntimeError(f"Failed to search inbox: {response}")

        email_ids = response[0].split()
        if email_ids:
            latest_id = email_ids[-1]
            fetch_status, msg_data = imap_server.fetch(latest_id, "(RFC822)")
            if fetch_status == "OK" and msg_data and isinstance(msg_data[0], tuple):
                raw_email = msg_data[0][1]
                parsed_msg = email.message_from_bytes(raw_email)
                raw_subject = parsed_msg.get("Subject")
                subject = decode_mime_header(raw_subject)
                sender = parsed_msg.get("From", "unknown sender")
                print(f"       Found unread email ID {latest_id.decode()}:")
                print(f"       - From: {sender}")
                print(f"       - Subject: {subject}")
            else:
                print("       Found unread email, but could not fetch headers.")
        else:
            print("       No unread emails found in INBOX (inbox read verified).")

        imap_server.close()
        imap_server.logout()
        print("       IMAP read check passed.")

    except Exception as exc:
        print(f"[FAILURE] Gmail IMAP check failed: {exc}")
        sys.exit(1)

    # 2. SMTP Verification: Send a test email to self
    try:
        print("[2/2] Connecting to Gmail SMTP (smtp.gmail.com)...")
        msg = EmailMessage()
        msg["Subject"] = "AgentLens Guardian - Gmail Connection Test"
        msg["From"] = gmail_address
        msg["To"] = gmail_address
        msg.set_content(
            "Hello,\n\n"
            "This is an automated test email sent from test_gmail.py confirming that "
            "Gmail SMTP and IMAP connectivity is operational.\n\n"
            "— AgentLens Guardian Scaffolding"
        )

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp_server:
            smtp_server.login(gmail_address, gmail_app_password)
            smtp_server.send_message(msg)

        print(f"       Test email successfully sent to {gmail_address}.")
        print("       SMTP write check passed.")

    except Exception as exc:
        print(f"[FAILURE] Gmail SMTP send failed: {exc}")
        sys.exit(1)

    print("\n[SUCCESS] Gmail integration verified successfully (IMAP read + SMTP write).")


if __name__ == "__main__":
    test_gmail()
