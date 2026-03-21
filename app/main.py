import ipaddress
import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware

from app.api.routes import admin_router, auth_router, erpnext_router, ota_router, status_router
from app.config import get_settings
from app.db import SessionLocal, engine
from app.services.process_job_runner import start_process_job_runner, stop_process_job_runner
from app.services.process_jobs import build_process_job_summary
from app.services.server_version import get_server_version
from app.web.public_routes import router as public_web_router
from app.web.routes import router as web_router

settings = get_settings()
server_version = get_server_version()

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)

app = FastAPI(title=settings.app_name)
session_secret = settings.session_secret or settings.jwt_secret
trusted_proxy_nets: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
for raw in settings.trusted_proxy_net_list:
    try:
        trusted_proxy_nets.append(ipaddress.ip_network(raw, strict=False))
    except ValueError:
        logger.warning("Invalid TRUSTED_PROXY_NETS entry: %s", raw)
app.add_middleware(
    SessionMiddleware,
    secret_key=session_secret,
    https_only=not settings.allow_insecure_http,
    max_age=settings.admin_session_max_age_seconds,
    same_site=settings.admin_session_same_site,
)


@app.middleware("http")
async def enforce_https(request: Request, call_next):
    correlation_id = request.headers.get("X-Correlation-Id", "").strip() or str(uuid.uuid4())
    request.state.correlation_id = correlation_id
    if not settings.allow_insecure_http:
        scheme = request.url.scheme
        forwarded_proto = request.headers.get("x-forwarded-proto")
        if forwarded_proto and request.client and trusted_proxy_nets:
            try:
                client_ip = ipaddress.ip_address(request.client.host)
            except ValueError:
                client_ip = None
            if client_ip and any(client_ip in net for net in trusted_proxy_nets):
                scheme = forwarded_proto.split(",")[0].strip()
        if scheme != "https":
            response = JSONResponse(status_code=400, content={"detail": "HTTPS required"})
            response.headers["X-Correlation-Id"] = correlation_id
            response.headers["X-Process-Api-Version"] = "1"
            response.headers["X-License-Server-Version"] = server_version
            return response
    response = await call_next(request)
    response.headers["X-Correlation-Id"] = correlation_id
    response.headers["X-Process-Api-Version"] = "1"
    response.headers["X-License-Server-Version"] = server_version
    return response


app.include_router(auth_router)
app.include_router(auth_router, prefix="/api", include_in_schema=False)
app.include_router(status_router)
app.include_router(ota_router)
app.include_router(ota_router, prefix="/api", include_in_schema=False)
app.include_router(erpnext_router)
app.include_router(admin_router)
app.include_router(public_web_router)
app.include_router(web_router)


@app.on_event("startup")
async def startup_process_job_runner() -> None:
    await start_process_job_runner()


@app.on_event("shutdown")
async def shutdown_process_job_runner() -> None:
    await stop_process_job_runner()


def build_readiness_payload() -> tuple[dict, int]:
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        process_summary = build_process_job_summary(db, window_hours=24)
        status = "ok" if process_summary["health_status"] == "ok" else "degraded"
        status_code = 200 if status == "ok" else 503
        return {
            "status": status,
            "database": "ok",
            "process_jobs": process_summary,
            "process_job_runner_enabled": settings.process_job_runner_enabled,
        }, status_code
    except Exception as exc:
        logger.exception("Readiness check failed")
        return {
            "status": "error",
            "database": "error",
            "detail": str(exc),
            "process_job_runner_enabled": settings.process_job_runner_enabled,
        }, 503
    finally:
        db.close()


@app.get("/health/live")
def health_live() -> dict:
    return {"status": "ok"}


@app.get("/health/ready")
def health_ready() -> JSONResponse:
    payload, status_code = build_readiness_payload()
    return JSONResponse(status_code=status_code, content=payload)


@app.get("/health")
def health() -> JSONResponse:
    payload, status_code = build_readiness_payload()
    return JSONResponse(status_code=status_code, content=payload)
