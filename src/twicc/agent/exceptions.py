"""Agent-related exception types shared across providers."""


class SendDeliveryError(RuntimeError):
    """A message could not be delivered to the agent.

    Raised anywhere on the send path BEFORE the agent has taken ownership
    of the message (state guards, hybrid composer protection, transport
    failures). The WS ``send_message`` handler turns it into an ``error``
    frame whose ``code`` lets the frontend run its recovery flow: restore
    the composer draft and drop the optimistic chat message.

    Known codes: ``agent_starting``, ``agent_dead``,
    ``hybrid_composer_busy``, and the generic default ``send_failed``.
    """

    def __init__(self, message: str, *, code: str = "send_failed") -> None:
        super().__init__(message)
        self.code = code
