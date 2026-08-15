from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import get_current_admin
from app.models import Admin, UpstreamAccount
from app.providers import get_provider
from app.services.grok_oauth import build_authorize_url, exchange_code

router = APIRouter(tags=["oauth"])


@router.get("/api/admin/accounts/{account_id}/oauth/start")
def oauth_start(
    account_id: int,
    db: Session = Depends(get_db),
    _: Admin = Depends(get_current_admin),
) -> dict[str, str]:
    account = db.get(UpstreamAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    if get_provider(account.provider).auth_type != "oauth":
        raise HTTPException(status_code=400, detail="该供应商不需要 OAuth")
    return {"authorize_url": build_authorize_url(db, account)}


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
