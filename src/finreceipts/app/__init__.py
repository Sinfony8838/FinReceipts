"""FastAPI + SSE demo surface for FinReceipts."""

__all__ = ["create_app"]


def __getattr__(name: str):
    if name == "create_app":
        from finreceipts.app.main import create_app

        return create_app
    raise AttributeError(name)
