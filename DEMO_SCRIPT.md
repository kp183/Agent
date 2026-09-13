# AgentLens Guardian — 2-Minute Demo Script

Use this exact walkthrough for your screen recording. Keep Terminal, Slack, and Linear visible.

---

### [0:00 - 0:25] Introduction & Philosophy
* **What to show:** Show terminal or architecture slide.
* **What to say:**
  > *"Autonomous customer support agents frequently fail in production for two reasons: they let high confidence excuse policy violations, and they drop requests when downstream APIs silently fail. 
  > This is **AgentLens Guardian** — a runtime policy and verification engine that enforces deterministic guardrails across Gmail, Slack, and Linear."*

---

### [0:25 - 0:50] Scenario A: Autonomous Execution
* **What to show:** Run `python pipeline.py --subject "Order delayed"`
* **What to say:**
  > *"Here, a customer asks a standard shipping inquiry. The Guardian evaluates intent, checks sender identity, and notes no financial or security flags. Confidence is 95%, so it routes to **AUTONOMOUS** — instantly sending a direct Gmail reply and logging the ticket as resolved in Slack."*
* **Visual cue:** Show Slack channel `#C0C2C3PMK16` with the green checkmark resolution message.

---

### [0:50 - 1:15] Scenario B: Identity Mismatch (Deterministic Block)
* **What to show:** Run `python pipeline.py --subject "shipping address"`
* **What to say:**
  > *"Next, a customer asks to redirect an order to a new address, but the sender email doesn't match the account on file. Guardian immediately trips an **IDENTITY_MISMATCH** flag. The action is deterministically **BLOCKED**, an incident ticket is created in Linear, and an escalation notice is sent to Slack."*
* **Visual cue:** Click the Linear issue link in Slack to show ticket `KUN-7`.

---

### [1:15 - 1:45] The Climax: Deterministic Override & Fallback Recovery
* **What to show:** Run `python pipeline.py --simulate-failure --subject "charged twice"`
* **What to say:**
  > *"Now the most critical test: an urgent email claims an account was hacked and demands an ₹18,500 refund. Even though confidence is 95%, **confidence never rescues a flagged action**. It is immediately blocked.
  > But what if downstream Linear suffers a silent failure? Guardian runs **Post-Action State Verification**. It detects that Linear returned no ticket ID, logs `[VERIFICATION FAILED]`, and triggers an immediate emergency fallback recovery directly to the engineering team in Slack."*
* **Visual cue:** Highlight the red alert in Slack: `🚨 CRITICAL GUARDIAN ALERT — VERIFICATION FAILURE & FALLBACK RECOVERY`.

---

### [1:45 - 2:00] Conclusion & Benchmark
* **What to show:** Run `python eval_scenarios.py`
* **What to say:**
  > *"We validated this across a 12-scenario benchmark covering financial thresholds, adversarial injection, and identity checks — achieving 100% policy accuracy. AgentLens Guardian gives teams the confidence to deploy autonomous agents without fear of catastrophic errors."*
