import json

from .. import bus
from ..bus import Message
from ..models import AgentState


class Agent:
    name = "Agent"

    # ---- persistent beliefs (survive restarts: stored in agent_state) ----
    def get(self, db, key, default=0):
        row = db.get(AgentState, (self.name, key))
        return json.loads(row.value) if row else default

    def put(self, db, key, value):
        row = db.get(AgentState, (self.name, key))
        if row:
            row.value = json.dumps(value)
        else:
            db.add(AgentState(agent=self.name, key=key, value=json.dumps(value)))
        db.flush()

    def incr(self, db, key, n=1):
        v = self.get(db, key, 0) + n
        self.put(db, key, v)
        return v

    # ---- messaging ----
    def reply(self, m, performative, **content):
        return Message(conversation_id=m.conversation_id, sender=self.name, receiver=m.sender,
                       performative=performative, content=content)

    def ask(self, db, to, cid, performative="REQUEST", **content):
        """Send a message to another agent (or '*' to broadcast) through the bus."""
        return bus.send(db, Message(conversation_id=cid, sender=self.name, receiver=to,
                                    performative=performative, content=content))

    def handle(self, db, m):
        if m.performative == "INFORM":
            self.on_inform(db, m)
            return None
        return self.reply(m, "FAILURE", reason="message type not supported")

    def on_inform(self, db, m):  # reactive behaviour: override
        pass
