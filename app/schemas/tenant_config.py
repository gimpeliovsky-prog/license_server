from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TenantConfigAccessPayload(BaseModel):
    client_profile: str = "STANDARD"
    pick_list_images_enabled: bool = False
    erp_custom_fields_enabled: bool = False
    box_count_feature_enabled: bool = False
    or_qty_label: str | None = None
    or_qty_required: bool = True

    model_config = ConfigDict(extra="allow")


class TenantScaleHostPayload(BaseModel):
    host: str
    name: str | None = None

    model_config = ConfigDict(extra="allow")


class TenantConfigScalesPayload(BaseModel):
    scale_enabled: bool = False
    scale_unit: str = "kg"
    hosts: list[TenantScaleHostPayload] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow")


class TenantConfigPayload(BaseModel):
    access: TenantConfigAccessPayload = Field(default_factory=TenantConfigAccessPayload)
    barcodes: list[dict[str, Any]] = Field(default_factory=list)
    scales: TenantConfigScalesPayload = Field(default_factory=TenantConfigScalesPayload)

    model_config = ConfigDict(extra="allow")


class TenantConfigSnapshot(BaseModel):
    config_revision: str = ""
    settings_scope: str = "tenant"
    updated_at: str | None = None
    updated_by: str | None = None
    payload: TenantConfigPayload = Field(default_factory=TenantConfigPayload)

    model_config = ConfigDict(extra="allow")
