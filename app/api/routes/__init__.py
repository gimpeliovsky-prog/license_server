from app.api.routes.admin import router as admin_router
from app.api.routes.auth import router as auth_router
from app.api.routes.erpnext import router as erpnext_router
from app.api.routes.ota import router as ota_router
from app.api.routes.status import router as status_router

__all__ = ["admin_router", "auth_router", "erpnext_router", "ota_router", "status_router"]
