import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_SCALE_HOST_PATTERN = re.compile(r"^(?:(?i:https?)://)?(\d{1,3}(?:\.\d{1,3}){3})(?::(\d{1,5}))?/?$")


def _normalize_scale_host(raw: Any) -> str:
    value = str(raw or "").strip()
    if not value:
        raise ValueError("Scale host is required")

    match = _SCALE_HOST_PATTERN.fullmatch(value)
    if not match:
        raise ValueError("Scale host must be a valid IPv4 address, optionally with port")

    host = match.group(1)
    port = match.group(2) or ""
    octets = host.split(".")
    if len(octets) != 4:
        raise ValueError("Scale host must be a valid IPv4 address")
    for octet in octets:
        try:
            parsed = int(octet)
        except ValueError as exc:
            raise ValueError("Scale host must be a valid IPv4 address") from exc
        if parsed < 0 or parsed > 255:
            raise ValueError("Scale host must be a valid IPv4 address")

    if port:
        try:
            parsed_port = int(port)
        except ValueError as exc:
            raise ValueError("Scale host port must be numeric") from exc
        if parsed_port < 1 or parsed_port > 65535:
            raise ValueError("Scale host port must be between 1 and 65535")

    return f"{host}:{port}" if port else host


class TenantBarcodeConfigPayload(BaseModel):
    id: str = ""
    name: str = ""
    type: str = "FIXED_OFFSET"
    prefix: str = ""
    pattern: str = ""
    itemCodeStart: int = 0
    itemCodeLength: int = 0
    weightStart: int = 0
    weightLength: int = 0
    weightDecimals: int = 3
    lotStart: int = 0
    lotLength: int = 0
    expectedItemCodeFragment: str | None = None
    resolvedItemCode: str | None = None
    isActive: bool = True

    model_config = ConfigDict(extra="allow")


class TenantConfigAccessPayload(BaseModel):
    client_profile: str = "STANDARD"
    pick_list_images_enabled: bool = False
    erp_custom_fields_enabled: bool = False
    box_count_feature_enabled: bool = False
    or_qty_label: str | None = None
    or_qty_required: bool = True

    model_config = ConfigDict(extra="allow")


class TenantScaleHostPayload(BaseModel):
    number: str = ""
    host: str
    note: str | None = None

    model_config = ConfigDict(extra="allow")

    @model_validator(mode="before")
    @classmethod
    def upgrade_legacy_shape(cls, value: Any) -> Any:
        if isinstance(value, dict):
            sanitized = dict(value)
            if not sanitized.get("number"):
                sanitized["number"] = str(sanitized.get("id") or sanitized.get("host") or "").strip()
            if "note" not in sanitized and "name" in sanitized:
                sanitized["note"] = sanitized.get("name")
            return sanitized
        return value

    @field_validator("number", mode="before")
    @classmethod
    def validate_number(cls, value: Any) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("Scale number is required")
        return normalized

    @field_validator("host", mode="before")
    @classmethod
    def validate_host(cls, value: Any) -> str:
        return _normalize_scale_host(value)

    @field_validator("note", mode="before")
    @classmethod
    def normalize_note(cls, value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None


class TenantConfigScalesPayload(BaseModel):
    scale_enabled: bool = False
    scale_unit: str = "kg"
    hosts: list[TenantScaleHostPayload] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow")

    @field_validator("scale_unit", mode="before")
    @classmethod
    def normalize_scale_unit(cls, value: Any) -> str:
        normalized = str(value or "kg").strip().lower()
        if normalized not in {"kg", "g"}:
            raise ValueError("Scale unit must be either 'kg' or 'g'")
        return normalized

    @model_validator(mode="after")
    def deduplicate_hosts(self) -> "TenantConfigScalesPayload":
        deduplicated_hosts: list[TenantScaleHostPayload] = []
        seen: set[str] = set()
        for host in self.hosts:
            key = host.number.lower()
            if key in seen:
                continue
            seen.add(key)
            deduplicated_hosts.append(host)
        self.hosts = deduplicated_hosts
        return self


class TenantConfigPayload(BaseModel):
    access: TenantConfigAccessPayload = Field(default_factory=TenantConfigAccessPayload)
    barcodes: list[TenantBarcodeConfigPayload] = Field(default_factory=list)
    scales: TenantConfigScalesPayload = Field(default_factory=TenantConfigScalesPayload)

    model_config = ConfigDict(extra="allow")

    @model_validator(mode="before")
    @classmethod
    def drop_legacy_fields(cls, value: Any) -> Any:
        if isinstance(value, dict):
            sanitized = dict(value)
            sanitized.pop("fulfillment_rules", None)
            return sanitized
        return value


class TenantConfigSnapshot(BaseModel):
    config_revision: str = ""
    settings_scope: str = "tenant"
    updated_at: str | None = None
    updated_by: str | None = None
    payload: TenantConfigPayload = Field(default_factory=TenantConfigPayload)

    model_config = ConfigDict(extra="allow")
