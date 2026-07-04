"""Streamlit chat UI for Kompass.

Talks to the Kompass FastAPI over HTTP (POST /chat, POST /resume) - it never
imports the agent directly. When a run pauses for human approval, each pending
action is rendered as an approval card in the chat flow with approve / edit /
reject controls, mirroring the HITL middleware's decision types.

Run:  streamlit run ui/app.py   (requires `make api` in another terminal)
"""

import json
import os

import httpx
import streamlit as st

API_URL = os.getenv("KOMPASS_API_URL", "http://localhost:8000")

st.set_page_config(page_title="Kompass", page_icon="🧭")


# --- API ----------------------------------------------------------------------------


def post(path: str, payload: dict) -> dict | None:
    """POST to the Kompass API. Returns the JSON body, or None after surfacing the error."""
    try:
        resp = httpx.post(f"{API_URL}{path}", json=payload, timeout=120.0)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as exc:
        st.error(f"Kompass API error ({API_URL}): {exc}")
        return None


def apply_response(data: dict) -> None:
    """Fold an API response into session state: either an answer or pending actions."""
    st.session_state.thread_id = data["thread_id"]
    if data["status"] == "awaiting_approval":
        st.session_state.pending = data["pending_actions"]
    else:
        st.session_state.pending = None
        st.session_state.messages.append({"role": "assistant", "content": data["answer"]})


def resume(decisions: list[dict]) -> None:
    """Send reviewer decisions for the paused run and refresh the chat."""
    data = post("/resume", {"thread_id": st.session_state.thread_id, "decisions": decisions})
    if data:
        st.session_state.edit_idx = None
        st.session_state.reject_idx = None
        apply_response(data)
        st.rerun()


# --- Approval card ------------------------------------------------------------------


def render_approval_card(i: int, action: dict, pending: list[dict]) -> None:
    """One warning card per pending action: tool name, args, approve / edit / reject."""
    st.warning(f"⏸️ Approval required — **`{action['name']}`**")
    if action.get("description"):
        st.caption(action["description"])
    st.json(action["args"])

    approve_col, edit_col, reject_col = st.columns(3)
    if approve_col.button("✅ Approve", key=f"approve_{i}", type="primary"):
        resume([{"type": "approve"}] * len(pending))
    if edit_col.button("✏️ Edit", key=f"edit_{i}"):
        st.session_state.edit_idx, st.session_state.reject_idx = i, None
    if reject_col.button("❌ Reject", key=f"reject_{i}"):
        st.session_state.reject_idx, st.session_state.edit_idx = i, None

    if st.session_state.edit_idx == i:
        raw = st.text_area(
            "Edit args (JSON)", json.dumps(action["args"], indent=2), key=f"edit_args_{i}"
        )
        if st.button("Send edited action", key=f"edit_send_{i}"):
            try:
                args = json.loads(raw)
            except json.JSONDecodeError as exc:
                st.error(f"Invalid JSON: {exc}")
            else:
                decisions: list[dict] = [{"type": "approve"}] * len(pending)
                decisions[i] = {
                    "type": "edit",
                    "edited_action": {"name": action["name"], "args": args},
                }
                resume(decisions)

    if st.session_state.reject_idx == i:
        reason = st.text_input("Reason (optional)", key=f"reject_reason_{i}")
        if st.button("Confirm rejection", key=f"reject_send_{i}"):
            message = reason or "Rejected by reviewer."
            resume([{"type": "reject", "message": message}] * len(pending))


# --- Page ---------------------------------------------------------------------------

if "messages" not in st.session_state:
    st.session_state.update(
        messages=[], thread_id=None, pending=None, edit_idx=None, reject_idx=None
    )

with st.sidebar:
    if st.button("🔄 New conversation", use_container_width=True):
        st.session_state.clear()
        st.rerun()
    st.caption(f"API: `{API_URL}`")
    st.markdown("**Try the demo journeys**")
    st.markdown(
        "1. *How many vacation days do I get per year?* — grounded answer with citations.\n"
        "2. *I'm Lena Fischer, order 4471 arrived damaged — please refund it "
        "(ticket 88012).* — drafts a refund and pauses here for your approval."
    )
    st.caption("Citations are inline, e.g. [policies/refund_policy.md § Eligibility].")

st.title("🧭 Kompass — ACME Support & Operations")
st.caption("Ask about policies, orders and tickets. Risky actions pause for your approval.")

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if st.session_state.pending:
    with st.chat_message("assistant"):
        for i, action in enumerate(st.session_state.pending):
            render_approval_card(i, action, st.session_state.pending)

prompt = st.chat_input("Ask Kompass…", disabled=bool(st.session_state.pending))
if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.spinner("Kompass is thinking…"):
        data = post("/chat", {"message": prompt, "thread_id": st.session_state.thread_id})
    if data:
        apply_response(data)
        st.rerun()
