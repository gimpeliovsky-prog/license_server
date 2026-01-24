import ipaddress
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from app.api.routes import admin_router, auth_router, erpnext_router, ota_router, status_router
from app.config import get_settings
from app.web.routes import router as web_router

settings = get_settings()

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
            return JSONResponse(status_code=400, content={"detail": "HTTPS required"})
    return await call_next(request)


app.include_router(auth_router)
app.include_router(auth_router, prefix="/api", include_in_schema=False)
app.include_router(status_router)
app.include_router(ota_router)
app.include_router(ota_router, prefix="/api", include_in_schema=False)
app.include_router(erpnext_router)
app.include_router(admin_router)
app.include_router(web_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
