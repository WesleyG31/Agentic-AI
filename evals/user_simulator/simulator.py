"""The simulated user: an LLM that plays a persona with a hidden goal.

Given the persona, the goal, and the conversation so far, it writes the next
user message and stays in character — sharing details only as the agent asks
for them. When the goal is clearly met, or the agent has clearly refused with
no recourse, it emits the END sentinel so the driver can close the conversation.
"""

from kompass.models.router import pick

END = "[END]"

PROMPT = """You are role-playing a person contacting a company's support assistant. \
Stay fully in character for the whole conversation.

Persona: {persona}
Your objective (pursue it naturally through the conversation — never quote it verbatim): \
{goal}

How to behave:
- Write ONLY your next message to the assistant: first person, one or two sentences.
- Sound like a real person and match your persona's mood.
- Reveal details only as they become relevant. If the assistant asks a clarifying
  question, answer just that — do not dump your whole situation in one message.
- When your objective is clearly achieved, OR the assistant has clearly refused and there
  is nothing left to try, OR you are giving up, reply with exactly {end} and nothing else.

Conversation so far (You = your messages, Assistant = the support assistant):
{transcript}

Your next message:"""


class UserSimulator:
    """A persona-driven simulated user that emits one message per turn."""

    def __init__(self, scenario: dict):
        self.persona = scenario["persona"]
        self.goal = scenario["goal"]
        self.opening_message = scenario["opening_message"]

    def respond(self, history: list[dict]) -> str:
        """Return the next user message given the conversation so far.

        history is a list of {"role": "user"|"assistant", "content": str}. The first
        turn is the scripted opening message; later turns are generated in character.
        """
        if not history:
            return self.opening_message
        transcript = "\n".join(
            f"{'You' if turn['role'] == 'user' else 'Assistant'}: {turn['content']}"
            for turn in history
        )
        prompt = PROMPT.format(
            persona=self.persona, goal=self.goal, end=END, transcript=transcript
        )
        return pick("balanced").invoke(prompt).content.strip()
