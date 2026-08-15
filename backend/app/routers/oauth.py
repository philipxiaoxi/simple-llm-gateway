from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import get_current_admin
from app.models import Admin, UpstreamAccount
from app.providers import get_provider
from app.schemas import OauthCallbackComplete
from app.services.grok_oauth import build_authorize_url, complete_oauth_from_paste, exchange_code, is_loopback_redirect

router = APIRouter(tags=["oauth"])


@router.get("/api/admin/accounts/{account_id}/oauth/start")
def oauth_start(
    account_id: int,
    db: Session = Depends(get_db),
    _: Admin = Depends(get_current_admin),
) -> dict:
    account = db.get(UpstreamAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    if get_provider(account.provider).auth_type != "oauth":
        raise HTTPException(status_code=400, detail="该供应商不需要 OAuth")
    settings = get_settings()
    return {
        "authorize_url": build_authorize_url(db, account),
        "needs_paste": is_loopback_redirect(settings.xai_oauth_redirect_uri),
    }


@router.post("/api/admin/oauth/grok/callback")
async def oauth_callback_complete(
    payload: OauthCallbackComplete,
    db: Session = Depends(get_db),
    _: Admin = Depends(get_current_admin),
) -> dict[str, bool]:
    try:
        await complete_oauth_from_paste(
            db,
            account_id=payload.account_id,
            pasted=payload.callback_url or "",
            code=payload.code,
            state=payload.state,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"ok": True}


@router.get("/api/admin/oauth/grok/callback")
async def oauth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    frontend = get_settings().app_base_url.rstrip("/")
    if error:
        return RedirectResponse(f"{frontend}/accounts?oauth=error&reason={error}")
    if not code or not state:
        return RedirectResponse(f"{frontend}/accounts?oauth=error&reason=missing")
    try:
        await exchange_code(db, code, state)
    except ValueError:
        return RedirectResponse(f"{frontend}/accounts?oauth=error&reason=exchange")
    return RedirectResponse(f"{frontend}/accounts?oauth=ok")
