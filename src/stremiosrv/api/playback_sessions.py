from fastapi import APIRouter, Request

router = APIRouter(prefix="/playback-sessions")


def _registry(request: Request):
    return getattr(request.app.state, "playback_registry", None)


@router.get("")
@router.get("/")
def playback_sessions(request: Request) -> dict:
    """Independent HTTP playback activity, separate from the stable /active.json contract."""
    registry = _registry(request)
    if registry is None:
        return {"activeWindowSeconds": 0, "sessions": [], "active": []}
    return registry.snapshot()
