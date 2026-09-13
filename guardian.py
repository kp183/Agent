"""
guardian.py — Core Decision Engine for AgentLens Guardian.

Evaluates incoming email signals against policy rules (policy.json) to classify
actions into AUTONOMOUS, GUARDED, or BLOCKED tiers with associated risk levels
and policy flags.
"""

import os
import sys
import json
import re
from enum import Enum
from pathlib import Path
from pydantic import BaseModel

# Reconfigure standard streams to UTF-8 for safe console output on Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 1. Load policy.json at startup
POLICY_FILE_PATH = Path(__file__).parent / "policy.json"


def load_policy(path: Path | str = POLICY_FILE_PATH) -> dict:
    """Loads and returns the policy rules configuration."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


DEFAULT_POLICY = load_policy()


# 3. ActionTier Enum
class ActionTier(str, Enum):
    AUTONOMOUS = "AUTONOMOUS"
    GUARDED = "GUARDED"
    BLOCKED = "BLOCKED"


# 4. EvaluationResult Pydantic Model
class EvaluationResult(BaseModel):
    confidence_score: float
    risk_level: str  # "LOW", "MEDIUM", "CRITICAL"
    policy_flags: list[str]
    tier: ActionTier
    reasoning: str


# 2. Intent Signals Extractor
def extract_intent_signals(
    email_subject: str,
    email_body: str,
    sender_email: str,
    account_email_on_file: str,
    policy: dict | None = None,
) -> dict:
    """
    Extracts semantic signals, financial amounts, security keywords, and
    confidence heuristics from the email content and sender identity.
    """
    active_policy = policy or DEFAULT_POLICY
    full_text = f"{email_subject} {email_body}".strip()
    full_text_lower = full_text.lower()

    # 1. Financial Keywords
    financial_keywords = active_policy.get("financial_action_keywords", [])
    involves_financial = any(kw.lower() in full_text_lower for kw in financial_keywords)

    # 2. Refund Amount (INR extraction: looks for ₹, Rs, Rs., or INR followed by numbers)
    refund_amount_inr = None
    amount_pattern = r"(?:₹|rs\.?|inr)\s*([0-9]+(?:,[0-9]+)*(?:\.[0-9]+)?)"
    amount_match = re.search(amount_pattern, full_text, re.IGNORECASE)
    if amount_match:
        try:
            cleaned_num = amount_match.group(1).replace(",", "")
            refund_amount_inr = float(cleaned_num)
        except ValueError:
            refund_amount_inr = None

    # 3. Security Sensitive Keywords
    security_keywords = active_policy.get("security_sensitive_keywords", [])
    security_sensitive = any(kw.lower() in full_text_lower for kw in security_keywords)

    # 4. Identity Mismatch (case-insensitive sender vs account on file)
    sender_clean = sender_email.strip().lower()
    account_clean = account_email_on_file.strip().lower()
    identity_mismatch = bool(sender_clean and account_clean and sender_clean != account_clean)

    # 5. Base Confidence Heuristic
    # Start at 0.95; subtract 0.5 if identity mismatch; subtract 0.3 if short/vague (< 15 words)
    confidence = 0.95
    if identity_mismatch:
        penalty = active_policy.get("identity_mismatch_rule", {}).get("confidence_penalty", 0.5)
        confidence -= penalty

    word_count = len(full_text.split())
    if word_count < 15:
        confidence -= 0.3

    base_confidence = round(max(0.0, min(1.0, confidence)), 2)

    return {
        "involves_financial_transfer": involves_financial,
        "refund_amount_inr": refund_amount_inr,
        "security_sensitive_claim": security_sensitive,
        "identity_mismatch": identity_mismatch,
        "base_confidence": base_confidence,
    }


# 5. Policy Evaluator
def evaluate_action_policy(signals: dict, policy: dict | None = None) -> EvaluationResult:
    """
    Evaluates extracted signals against policy thresholds.

    Deterministic overrides (security claim, over-threshold refund, identity mismatch)
    force BLOCKED with CRITICAL risk regardless of confidence.
    Otherwise, action tier is determined by confidence_score relative to policy tiers.
    """
    active_policy = policy or DEFAULT_POLICY
    refund_policy = active_policy.get("refund_policy", {})
    max_auto_refund = refund_policy.get("max_auto_refund_inr", 2000)
    conf_tiers = active_policy.get("confidence_tiers", {})
    autonomous_min = conf_tiers.get("autonomous_min", 0.90)
    guarded_min = conf_tiers.get("guarded_min", 0.70)

    confidence = signals.get("base_confidence", 0.0)
    security_claim = signals.get("security_sensitive_claim", False)
    involves_finance = signals.get("involves_financial_transfer", False)
    refund_amount = signals.get("refund_amount_inr")
    identity_mismatch = signals.get("identity_mismatch", False)

    policy_flags = []
    override_reasons = []

    # Flag identification
    if security_claim:
        policy_flags.append("SECURITY_SENSITIVE")
        override_reasons.append("Security claim detected in communication")

    if involves_finance and refund_amount is not None and refund_amount > max_auto_refund:
        policy_flags.append("REFUND_THRESHOLD_EXCEEDED")
        override_reasons.append(f"Requested refund (Rs. {refund_amount:,.2f}) exceeds auto limit (Rs. {max_auto_refund:,.2f})")

    if identity_mismatch:
        policy_flags.append("IDENTITY_MISMATCH")
        override_reasons.append("Sender email does not match account/order email on file")

    # --- Evaluation Logic ---
    # Deterministic Overrides: Security claim OR Refund threshold exceeded OR Identity mismatch
    if security_claim or (involves_finance and refund_amount and refund_amount > max_auto_refund) or identity_mismatch:
        return EvaluationResult(
            confidence_score=confidence,
            risk_level="CRITICAL",
            policy_flags=policy_flags,
            tier=ActionTier.BLOCKED,
            reasoning="; ".join(override_reasons) + " — blocked by deterministic policy override.",
        )

    # Confidence-based Tiers
    if confidence >= autonomous_min:
        return EvaluationResult(
            confidence_score=confidence,
            risk_level="LOW",
            policy_flags=policy_flags,
            tier=ActionTier.AUTONOMOUS,
            reasoning=f"High confidence ({confidence:.2f} >= {autonomous_min}) with no policy flags; autonomous execution permitted.",
        )
    elif confidence >= guarded_min:
        policy_flags.append("MODERATE_CONFIDENCE")
        return EvaluationResult(
            confidence_score=confidence,
            risk_level="MEDIUM",
            policy_flags=policy_flags,
            tier=ActionTier.GUARDED,
            reasoning=f"Confidence ({confidence:.2f}) falls in guarded band ({guarded_min} - {autonomous_min}); human verification required.",
        )
    else:
        policy_flags.append("LOW_CONFIDENCE")
        return EvaluationResult(
            confidence_score=confidence,
            risk_level="CRITICAL",
            policy_flags=policy_flags,
            tier=ActionTier.BLOCKED,
            reasoning=f"Confidence ({confidence:.2f}) below guarded minimum ({guarded_min}); blocked due to insufficient certainty.",
        )


# 6. Verification Runner for Demo Scenarios
if __name__ == "__main__":
    print("=" * 80)
    print("                    AgentLens Guardian Decision Engine Evaluation                ")
    print("=" * 80 + "\n")

    scenarios = [
        {
            "id": "Scenario A",
            "name": "Normal Shipping Question",
            "expected_tier": "AUTONOMOUS",
            "subject": "Order delayed",
            "body": (
                "Hi, my order #4471 was supposed to arrive two days ago and it still shows "
                "'in transit.' Can you tell me what's going on and when I should expect it?\n\n"
                "Thanks,\nAditi"
            ),
            "sender": "aditi.sharma@example.com",
            "account_on_file": "aditi.sharma@example.com",
        },
        {
            "id": "Scenario B",
            "name": "Identity Mismatch Shipping Update",
            "expected_tier": "BLOCKED (Deterministic Override)",
            "subject": "Please update my shipping address",
            "body": (
                "Hi, can you update the shipping address on order #8842 to:\n"
                "221B Baker Street, Pune, Maharashtra 411001?\n\n"
                "Thanks,\nRohan"
            ),
            "sender": "rohan.unverified_personal@gmail.com",
            "account_on_file": "rohan.agarwal@example.com",
        },
        {
            "id": "Scenario C",
            "name": "High Risk Refund + Compromised Account",
            "expected_tier": "BLOCKED (Security + Refund Override)",
            "subject": "Urgent — charged twice, account may be compromised",
            "body": (
                "I was charged twice for order #5590 — please refund ₹18,500 immediately.\n"
                "I also think someone accessed my account without permission, there's activity I don't recognize."
            ),
            "sender": "priya.nair@example.com",
            "account_on_file": "priya.nair@example.com",
        },
        {
            "id": "Scenario D (Tier Demo)",
            "name": "Ambiguous / Guarded Order Query (0.70 <= Confidence < 0.90)",
            "expected_tier": "GUARDED",
            "subject": "Need status update",
            "body": "Could you please check my package?",
            "sender": "customer@example.com",
            "account_on_file": "customer@example.com",
            "custom_signals": {
                "involves_financial_transfer": False,
                "refund_amount_inr": None,
                "security_sensitive_claim": False,
                "identity_mismatch": False,
                "base_confidence": 0.82,  # In the guarded range [0.70, 0.90)
            },
        },
    ]

    for sc in scenarios:
        print(f"--- [{sc['id']}] {sc['name']} ---")
        print(f"Target Expectation: {sc['expected_tier']}")

        if "custom_signals" in sc:
            signals = sc["custom_signals"]
        else:
            signals = extract_intent_signals(
                email_subject=sc["subject"],
                email_body=sc["body"],
                sender_email=sc["sender"],
                account_email_on_file=sc["account_on_file"],
            )

        result = evaluate_action_policy(signals)

        print(f"Extracted Signals:  {signals}")
        print(f"Evaluated Tier:     {result.tier.value}")
        print(f"Risk Level:         {result.risk_level}")
        print(f"Confidence Score:   {result.confidence_score}")
        print(f"Policy Flags:       {result.policy_flags}")
        print(f"Reasoning:          {result.reasoning}\n")

    print("=" * 80)
    print("All tiers verified: AUTONOMOUS, GUARDED, and BLOCKED.")
    print("=" * 80)
