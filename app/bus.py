"""Message bus: the ONLY way agents talk to each other. Every message is logged."""
import json
import uuid
from typing import Literal

from pydantic import BaseModel, Field

from .models import MessageLog


class Message(BaseModel):
    conversation_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    sender: str
    receiver: str  # agent name, or "*" for a broadcast
    performative: Literal["REQUEST", "AGREE", "REFUSE", "PROPOSE", "INFORM", "FAILURE"]
    content: dict = Field(default_factory=dict)


AGENTS: dict = {}


def register(agent):
    AGENTS[agent.name] = agent


def _log(db, m: Message):
    db.add(MessageLog(conversation_id=m.conversation_id, sender=m.sender, receiver=m.receiver,
                      performative=m.performative, content=json.dumps(m.content, default=str)))
    db.flush()


def send(db, m: Message):
    """Log, deliver, log the reply. Broadcasts ('*') go to every agent's on_inform()."""
    _log(db, m)
    if m.receiver == "*":
        for a in list(AGENTS.values()):
            if a.name != m.sender:
                a.on_inform(db, m)
        return None
    agent = AGENTS.get(m.receiver)
    if agent is None:
        reply = Message(conversation_id=m.conversation_id, sender="Bus", receiver=m.sender,
                        performative="FAILURE", content={"reason": f"unknown receiver {m.receiver}"})
    else:
        reply = agent.handle(db, m)
    if reply:
        _log(db, reply)
    return reply


def request(db, sender, receiver, **content):
    """Convenience for the user-facing layer: send a REQUEST and return the reply."""
    return send(db, Message(sender=sender, receiver=receiver, performative="REQUEST", content=content))
