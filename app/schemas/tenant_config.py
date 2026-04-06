import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_SCALE_HOST_PATTERN = re.compile(r"^(?:(?i:https?)://)?(\d{1,3}(?:\.\d{1,3}){3})(?::(\d{1,5}))?/?$")
_KNOWN_AI_BEHAVIOR_CLASSES = {
    "direct_buyer",
    "explorer",
    "unclear_request",
    "price_sensitive",
    "frustrated",
    "service_request",
    "returning_customer",
    "silent_or_low_signal",
}
_KNOWN_AI_INTENTS = {
    "human_handoff",
    "service_request",
    "add_to_order",
    "confirm_order",
    "order_detail",
    "browse_catalog",
    "find_product",
    "low_signal",
}
_KNOWN_AI_STAGES = {
    "new",
    "identify",
    "discover",
    "clarify",
    "order_build",
    "confirm",
    "invoice",
    "service",
    "handoff",
    "closed",
}
_KNOWN_AI_CHANNELS = {"telegram", "whatsapp", "webchat"}
_KNOWN_MATCH_TYPES = {"regex", "contains", "exact"}
_KNOWN_REGEX_FLAGS = {"IGNORECASE", "MULTILINE", "DOTALL"}


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


class TenantAIHandoffRulesPayload(BaseModel):
    enabled: bool = True
    clarification_failure_limit: int = 2
    allow_customer_requested_handoff: bool = True
    frustrated_customer_handoff: bool = True

    model_config = ConfigDict(extra="allow")

    @field_validator("clarification_failure_limit", mode="before")
    @classmethod
    def normalize_clarification_failure_limit(cls, value: Any) -> int:
        try:
            normalized = int(value if value not in (None, "") else 2)
        except (TypeError, ValueError) as exc:
            raise ValueError("Clarification failure limit must be an integer") from exc
        if normalized < 1:
            raise ValueError("Clarification failure limit must be at least 1")
        return normalized


class TenantAIToolsPolicyPayload(BaseModel):
    allowed_tools: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow")

    @field_validator("allowed_tools", mode="before")
    @classmethod
    def normalize_allowed_tools(cls, value: Any) -> list[str]:
        if value in (None, ""):
            return []
        if not isinstance(value, list):
            raise ValueError("Allowed tools must be a JSON array")
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            tool_name = str(item or "").strip()
            if not tool_name or tool_name in seen:
                continue
            normalized.append(tool_name)
            seen.add(tool_name)
        return normalized


class TenantAIHandoffTargetPayload(BaseModel):
    target_type: str = "none"
    destination: str | None = None
    instructions: str | None = None

    model_config = ConfigDict(extra="allow")

    @field_validator("target_type", mode="before")
    @classmethod
    def normalize_target_type(cls, value: Any) -> str:
        normalized = str(value or "none").strip().lower()
        if normalized not in {"none", "email", "telegram", "dashboard"}:
            raise ValueError("Handoff target type must be one of: none, email, telegram, dashboard")
        return normalized

    @field_validator("destination", "instructions", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None


class TenantAIClassificationRulePayload(BaseModel):
    match_type: str = "regex"
    pattern: str
    target: str
    confidence: float = 0.8
    flags: list[str] = Field(default_factory=lambda: ["IGNORECASE"])

    model_config = ConfigDict(extra="allow")

    @field_validator("match_type", mode="before")
    @classmethod
    def normalize_match_type(cls, value: Any) -> str:
        normalized = str(value or "regex").strip().lower()
        if normalized not in _KNOWN_MATCH_TYPES:
            raise ValueError("Match type must be one of: regex, contains, exact")
        return normalized

    @field_validator("pattern", "target", mode="before")
    @classmethod
    def normalize_required_text(cls, value: Any) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("Pattern and target are required")
        return normalized

    @field_validator("confidence", mode="before")
    @classmethod
    def normalize_confidence(cls, value: Any) -> float:
        try:
            normalized = float(value if value not in (None, "") else 0.8)
        except (TypeError, ValueError) as exc:
            raise ValueError("Confidence must be numeric") from exc
        if normalized < 0 or normalized > 1:
            raise ValueError("Confidence must be between 0 and 1")
        return normalized

    @field_validator("flags", mode="before")
    @classmethod
    def normalize_flags(cls, value: Any) -> list[str]:
        if value in (None, ""):
            return ["IGNORECASE"]
        if not isinstance(value, list):
            raise ValueError("Flags must be a JSON array")
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            flag_name = str(item or "").strip().upper()
            if not flag_name or flag_name in seen:
                continue
            if flag_name not in _KNOWN_REGEX_FLAGS:
                raise ValueError(f"Unsupported regex flag '{flag_name}'")
            normalized.append(flag_name)
            seen.add(flag_name)
        return normalized or ["IGNORECASE"]


class TenantAIClassificationConfigPayload(BaseModel):
    behavior_rules: list[TenantAIClassificationRulePayload] = Field(default_factory=list)
    intent_rules: list[TenantAIClassificationRulePayload] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow")

    @field_validator("behavior_rules", mode="after")
    @classmethod
    def validate_behavior_targets(
        cls, value: list[TenantAIClassificationRulePayload]
    ) -> list[TenantAIClassificationRulePayload]:
        for rule in value:
            if rule.target not in _KNOWN_AI_BEHAVIOR_CLASSES:
                raise ValueError(f"Unknown behavior target '{rule.target}'")
        return value

    @field_validator("intent_rules", mode="after")
    @classmethod
    def validate_intent_targets(
        cls, value: list[TenantAIClassificationRulePayload]
    ) -> list[TenantAIClassificationRulePayload]:
        for rule in value:
            if rule.target not in _KNOWN_AI_INTENTS:
                raise ValueError(f"Unknown intent target '{rule.target}'")
        return value


class TenantAIPromptOverridesPayload(BaseModel):
    core_policy: list[str] = Field(default_factory=list)
    language_policy: list[str] = Field(default_factory=list)
    catalog_policy: list[str] = Field(default_factory=list)
    order_policy: list[str] = Field(default_factory=list)
    service_policy: list[str] = Field(default_factory=list)
    stage_prompts: dict[str, list[str]] = Field(default_factory=dict)
    behavior_prompts: dict[str, list[str]] = Field(default_factory=dict)
    channel_prompts: dict[str, list[str]] = Field(default_factory=dict)
    handoff_messages: dict[str, str] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow")

    @field_validator(
        "core_policy",
        "language_policy",
        "catalog_policy",
        "order_policy",
        "service_policy",
        mode="before",
    )
    @classmethod
    def normalize_string_lists(cls, value: Any) -> list[str]:
        if value in (None, ""):
            return []
        if not isinstance(value, list):
            raise ValueError("Prompt override sections must be JSON arrays")
        return [str(item).strip() for item in value if str(item).strip()]

    @field_validator("stage_prompts", mode="before")
    @classmethod
    def normalize_stage_prompts(cls, value: Any) -> dict[str, list[str]]:
        if value in (None, ""):
            return {}
        if not isinstance(value, dict):
            raise ValueError("Stage prompts must be a JSON object")
        normalized: dict[str, list[str]] = {}
        for key, lines in value.items():
            stage = str(key or "").strip()
            if stage not in _KNOWN_AI_STAGES:
                raise ValueError(f"Unknown stage prompt key '{stage}'")
            if not isinstance(lines, list):
                raise ValueError(f"Stage prompt '{stage}' must be a JSON array")
            normalized[stage] = [str(item).strip() for item in lines if str(item).strip()]
        return normalized

    @field_validator("behavior_prompts", mode="before")
    @classmethod
    def normalize_behavior_prompts(cls, value: Any) -> dict[str, list[str]]:
        if value in (None, ""):
            return {}
        if not isinstance(value, dict):
            raise ValueError("Behavior prompts must be a JSON object")
        normalized: dict[str, list[str]] = {}
        for key, lines in value.items():
            behavior = str(key or "").strip()
            if behavior not in _KNOWN_AI_BEHAVIOR_CLASSES:
                raise ValueError(f"Unknown behavior prompt key '{behavior}'")
            if not isinstance(lines, list):
                raise ValueError(f"Behavior prompt '{behavior}' must be a JSON array")
            normalized[behavior] = [str(item).strip() for item in lines if str(item).strip()]
        return normalized

    @field_validator("channel_prompts", mode="before")
    @classmethod
    def normalize_channel_prompts(cls, value: Any) -> dict[str, list[str]]:
        if value in (None, ""):
            return {}
        if not isinstance(value, dict):
            raise ValueError("Channel prompts must be a JSON object")
        normalized: dict[str, list[str]] = {}
        for key, lines in value.items():
            channel = str(key or "").strip()
            if channel not in _KNOWN_AI_CHANNELS:
                raise ValueError(f"Unknown channel prompt key '{channel}'")
            if not isinstance(lines, list):
                raise ValueError(f"Channel prompt '{channel}' must be a JSON array")
            normalized[channel] = [str(item).strip() for item in lines if str(item).strip()]
        return normalized

    @field_validator("handoff_messages", mode="before")
    @classmethod
    def normalize_handoff_messages(cls, value: Any) -> dict[str, str]:
        if value in (None, ""):
            return {}
        if not isinstance(value, dict):
            raise ValueError("Handoff messages must be a JSON object")
        normalized: dict[str, str] = {}
        for key, message in value.items():
            lang = str(key or "").strip()
            text = str(message or "").strip()
            if not lang or not text:
                continue
            normalized[lang] = text
        return normalized


class TenantAIConfigPayload(BaseModel):
    allow_invoice: bool = True
    allow_license_ops: bool = True
    allow_discount_promises: bool = False
    allow_free_text_catalog_answers: bool = True
    handoff_rules: TenantAIHandoffRulesPayload = Field(default_factory=TenantAIHandoffRulesPayload)
    handoff_target: TenantAIHandoffTargetPayload = Field(default_factory=TenantAIHandoffTargetPayload)
    tools_policy: TenantAIToolsPolicyPayload = Field(default_factory=TenantAIToolsPolicyPayload)
    classification: TenantAIClassificationConfigPayload = Field(default_factory=TenantAIClassificationConfigPayload)
    prompt_overrides: TenantAIPromptOverridesPayload = Field(default_factory=TenantAIPromptOverridesPayload)

    model_config = ConfigDict(extra="allow")


class TenantConfigPayload(BaseModel):
    access: TenantConfigAccessPayload = Field(default_factory=TenantConfigAccessPayload)
    barcodes: list[TenantBarcodeConfigPayload] = Field(default_factory=list)
    scales: TenantConfigScalesPayload = Field(default_factory=TenantConfigScalesPayload)
    ai: TenantAIConfigPayload = Field(default_factory=TenantAIConfigPayload)

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
