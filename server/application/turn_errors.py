"""Turn lifecycle exceptions shared without importing the gateway package."""


class TurnCancellationRequested(RuntimeError):
    """The durable turn state says this delivery must be finalized as cancelled."""
