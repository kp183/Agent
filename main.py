"""
AgentLens Guardian — entrypoint (SCAFFOLD ONLY)

Intentionally empty of decision logic. This file exists so that Sunday's
build starts with working plumbing (env vars, app boot, route stubs) and
none of the actual judged logic (evaluator, policy engine, verification,
integrations wiring) — that gets written live during the 9:30-4:00 build
window per the plan.
"""
from fastapi import FastAPI
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="AgentLens Guardian")


@app.get("/health")
def health():
    return {"status": "ok"}


# --- Route stubs (to be implemented Sunday) ---
# POST /ingest        -> receives incoming support request (from Gmail poll or webhook)
# POST /evaluate       -> runs the Guardian evaluator (confidence + risk -> tier)
# POST /execute        -> executes the tool call for AUTONOMOUS tier actions
# POST /verify         -> post-action state verification
# POST /escalate       -> GUARDED/BLOCKED path -> Slack + Linear
