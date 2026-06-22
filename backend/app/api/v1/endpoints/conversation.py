"""
Conversational Analytics API — Phase 4.

POST /conversation/sessions          — create a session
GET  /conversation/sessions          — list sessions
GET  /conversation/sessions/{id}     — session detail + history
DELETE /conversation/sessions/{id}   — delete a session
POST /conversation/ask               — ask a question in a session
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.logging import get_logger
from app.insights.conversation.engine import ConversationEngine
from app.schemas.insights import (
    AskRequest, AskResponse,
    MessageOut, SessionCreateRequest, SessionCreateResponse, SessionOut,
)
from app.security.deps import CurrentUser, require_permission
from app.security.rbac import Permission

router = APIRouter()
log    = get_logger(__name__)


@router.post("/sessions", response_model=SessionCreateResponse, summary="Create a conversation session")
async def create_session(
    req: SessionCreateRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission(Permission.TRIGGER_INSIGHT)),
):
    engine = ConversationEngine(db)
    sid    = engine.create_session(req.category_id, req.region_id, req.title)
    sess   = engine.get_session(sid)
    return SessionCreateResponse(session_id=sid, title=sess["title"] or "")


@router.get("/sessions", response_model=list[SessionOut], summary="List conversation sessions")
async def list_sessions(active_only: bool = True, db: Session = Depends(get_db)):
    return [SessionOut(**s) for s in ConversationEngine(db).list_sessions(active_only)]


@router.get("/sessions/{session_id}", summary="Session detail with message history")
async def get_session(session_id: str, db: Session = Depends(get_db)):
    engine  = ConversationEngine(db)
    session = engine.get_session(session_id)
    if not session:
        raise HTTPException(404, f"Session {session_id} not found")
    messages = engine.get_history(session_id, last_n=100)
    return {
        "session":  SessionOut(**session),
        "messages": [MessageOut(**m) for m in messages],
    }


@router.delete("/sessions/{session_id}", summary="Delete a conversation session")
async def delete_session(
    session_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission(Permission.TRIGGER_INSIGHT)),
):
    deleted = ConversationEngine(db).delete_session(session_id)
    if not deleted:
        raise HTTPException(404, f"Session {session_id} not found")
    return {"deleted": True}


@router.post("/ask", response_model=AskResponse, summary="Ask an analytics question")
async def ask(
    req: AskRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission(Permission.TRIGGER_INSIGHT)),
):
    """
    Evidence-based conversational analytics over revenue, forecast,
    and signal data. Every answer has ANSWER / EVIDENCE / CAVEATS
    sections grounded in live database numbers.

    Example questions:
    - Which category will generate the highest revenue next quarter?
    - Why is dairy forecasted to decline?
    - Compare snacks and beverages.
    - Which regions are underperforming?
    - What are the top growth opportunities?
    """
    engine = ConversationEngine(db)
    try:
        result = await engine.ask(req.session_id, req.question, req.category_id, req.region_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return AskResponse(**result)
