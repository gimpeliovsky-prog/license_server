import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from app.api.routes import admin_router, auth_router, erpnext_router, status_router
from app.config import get_settings
from app.web.routes import router as web_router

settings = get_settings()

logging.basicConfig(level=settings.log_level)

app = FastAPI(title=settings.app_name)
session_secret = settings.session_secret or settings.jwt_secret
app.add_middleware(
    SessionMiddleware,
    secret_key=session_secret,
    https_only=not settings.allow_insecure_http,
)


@app.middleware("http")
async def enforce_https(request: Request, call_next):
    if not settings.allow_insecure_http:
        proto = request.headers.get("x-forwarded-proto") or request.url.scheme
        if proto != "https":
            return JSONResponse(status_code=400, content={"detail": "HTTPS required"})
    return await call_next(request)


app.include_router(auth_router)
app.include_router(status_router)
app.include_router(erpnext_router)
app.include_router(admin_router)
app.include_router(web_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
