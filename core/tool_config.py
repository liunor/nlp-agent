"""Strict Pydantic-v2 configuration for tools, policies, profiles, and MCP."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.runtime_config import load_runtime_config
from core.tool_runtime import ToolRisk, ToolScope


class StrictConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RoleToolPolicy(StrictConfigModel):
    allowed_tools: set[str] = Field(default_factory=set)
    allowed_capabilities: set[str] = Field(default_factory=set)
    denied_tools: set[str] = Field(default_factory=set)
    denied_capabilities: set[str] = Field(default_factory=set)


class ToolPoliciesConfig(StrictConfigModel):
    coordinator: RoleToolPolicy = Field(
        default_factory=lambda: RoleToolPolicy(
            allowed_tools={
                "spawn_worker",
                "send_message",
                "TaskStop",
                "read_local_file",
                "get_current_time",
            },
            allowed_capabilities={"context.manage"},
            denied_capabilities={"business.write"},
        )
    )
    worker: RoleToolPolicy = Field(
        default_factory=lambda: RoleToolPolicy(
            denied_capabilities={"runtime.control", "worker.manage"}
        )
    )


class CustomToolManifest(StrictConfigModel):
    """Required metadata contract for one custom-tool provider."""

    id: str
    version: str
    category: str = "general"
    prompt_priority: int = Field(default=100, ge=-1000, le=1000)
    scopes: set[ToolScope] = Field(default_factory=lambda: {ToolScope.WORKER})
    capabilities: set[str] = Field(default_factory=set)
    risk: ToolRisk = ToolRisk.MEDIUM
    enabled: bool = True

    @field_validator("id", "version")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("manifest id and version cannot be blank")
        return value


class CustomToolsConfig(StrictConfigModel):
    modules: list[str] = Field(default_factory=list)
    entrypoint_group: str = "nlp_agent.tools"
    manifests: dict[str, CustomToolManifest] = Field(default_factory=dict)


class MCPServerConfig(StrictConfigModel):
    transport: Literal["stdio", "sse", "streamable_http"] | None = None
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str = ""
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    allow_private_network: bool = False
    enabled_tools: list[str] = Field(default_factory=lambda: ["*"])
    read_only_tools: set[str] = Field(default_factory=set)
    idempotent_tools: set[str] = Field(default_factory=set)
    high_risk_tools: set[str] = Field(default_factory=set)
    session_exclusive_tools: set[str] = Field(default_factory=set)
    global_exclusive_tools: set[str] = Field(default_factory=set)
    retry_attempts: int = Field(default=2, ge=1, le=5)
    max_concurrency: int = Field(default=1, ge=1, le=100)
    timeout_s: float = Field(default=30.0, gt=0, le=1800)
    scopes: set[ToolScope] = Field(default_factory=lambda: {ToolScope.WORKER})

    @model_validator(mode="after")
    def validate_transport(self) -> "MCPServerConfig":
        transport = self.transport
        if transport is None:
            transport = "stdio" if self.command else "streamable_http" if self.url else None
            object.__setattr__(self, "transport", transport)
        if transport == "stdio" and not self.command:
            raise ValueError("stdio MCP server requires command")
        if transport in {"sse", "streamable_http"} and not self.url:
            raise ValueError(f"{transport} MCP server requires url")
        return self


DEFAULT_BLOCKED_CIDRS: tuple[str, ...] = (
    "0.0.0.0/8",
    "10.0.0.0/8",
    "100.64.0.0/10",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "::1/128",
    "fc00::/7",
    "fe80::/10",
)


class WebNetworkConfig(StrictConfigModel):
    connect_timeout_s: float = Field(default=5.0, gt=0, le=60)
    read_timeout_s: float = Field(default=20.0, gt=0, le=120)
    max_redirects: int = Field(default=5, ge=0, le=10)
    max_response_bytes: int = Field(default=5_000_000, ge=10_000, le=50_000_000)
    blocked_cidrs: list[str] = Field(
        default_factory=lambda: list(DEFAULT_BLOCKED_CIDRS)
    )

    @field_validator("blocked_cidrs")
    @classmethod
    def validate_blocked_cidrs(cls, values: list[str]) -> list[str]:
        import ipaddress

        for value in values:
            try:
                ipaddress.ip_network(value, strict=False)
            except ValueError as error:
                raise ValueError(f"invalid blocked CIDR {value!r}: {error}") from error
        return values


class WebFetchConfig(StrictConfigModel):
    max_chars: int = Field(default=20_000, ge=500, le=50_000)
    cache_ttl_s: int = Field(default=300, ge=0, le=3600)
    allowed_content_types: list[str] = Field(
        default_factory=lambda: ["text/html", "text/plain", "application/json"]
    )


class WebToolsConfig(StrictConfigModel):
    enabled: bool = True
    proxy_url: str = ""
    user_agent: str = "Nova/1.0 (+web-fetch)"
    network: WebNetworkConfig = Field(default_factory=WebNetworkConfig)
    fetch: WebFetchConfig = Field(default_factory=WebFetchConfig)


class VisionPreprocessingConfig(StrictConfigModel):
    auto_rotate: bool = True
    max_dimension: int = Field(default=4096, ge=48, le=16_384)
    retry_low_confidence_once: bool = True


class VisionOCRConfig(StrictConfigModel):
    provider: str = "rapidocr"
    language_default: Literal["auto", "zh", "en"] = "auto"
    confidence_threshold: float = Field(default=0.75, ge=0, le=1)

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        provider = value.strip()
        if provider not in {"rapidocr", "none"}:
            raise ValueError(
                "vision OCR provider must be 'rapidocr' or 'none'"
            )
        return provider


class VisionVLMConfig(StrictConfigModel):
    model_route: str = "vision-worker"
    max_image_bytes: int = Field(default=6_000_000, ge=1_024, le=100_000_000)
    send_ocr_context: bool = True

    @field_validator("model_route")
    @classmethod
    def validate_model_route(cls, value: str) -> str:
        route = value.strip()
        if not route:
            raise ValueError("vision VLM model route cannot be blank")
        return route


class VisionResultConfig(StrictConfigModel):
    max_chars: int = Field(default=20_000, ge=500, le=50_000)
    retain_raw_ocr: bool = True


class VisionToolsConfig(StrictConfigModel):
    enabled: bool = True
    max_file_bytes: int = Field(default=10_000_000, ge=1_024, le=100_000_000)
    max_pixels: int = Field(default=40_000_000, ge=2_304, le=200_000_000)
    max_pages: int = Field(default=10, ge=1, le=100)
    allowed_media_types: list[Literal["image/jpeg", "image/png", "image/webp"]] = Field(
        default_factory=lambda: ["image/jpeg", "image/png", "image/webp"],
        min_length=1,
    )
    allow_remote_url: bool = False
    preprocessing: VisionPreprocessingConfig = Field(default_factory=VisionPreprocessingConfig)
    ocr: VisionOCRConfig = Field(default_factory=VisionOCRConfig)
    vlm: VisionVLMConfig = Field(default_factory=VisionVLMConfig)
    result: VisionResultConfig = Field(default_factory=VisionResultConfig)

    @field_validator("allowed_media_types")
    @classmethod
    def validate_allowed_media_types(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("vision allowed_media_types cannot contain duplicates")
        return values

    @model_validator(mode="after")
    def validate_vlm_image_limit(self) -> "VisionToolsConfig":
        if self.vlm.max_image_bytes > self.max_file_bytes:
            raise ValueError("vision vlm.max_image_bytes must be <= max_file_bytes")
        return self


class ToolRuntimeConfig(StrictConfigModel):
    policies: ToolPoliciesConfig = Field(default_factory=ToolPoliciesConfig)
    custom: CustomToolsConfig = Field(default_factory=CustomToolsConfig)
    mcp_servers: dict[str, MCPServerConfig] = Field(default_factory=dict)
    web: WebToolsConfig = Field(default_factory=WebToolsConfig)
    vision: VisionToolsConfig = Field(default_factory=VisionToolsConfig)


class WorkerProfileSpec(StrictConfigModel):
    name: str
    description: str = ""
    model: str | None = None
    execution_mode: Literal["react", "one_shot"] = "react"
    requires_native_search: bool = False
    inherit_tool_policy: bool = True
    skills: list[str] = Field(default_factory=list)
    capabilities: set[str] = Field(default_factory=set)
    allowed_tools: set[str] = Field(default_factory=set)
    denied_tools: set[str] = Field(default_factory=set)

    @model_validator(mode="after")
    def validate_native_search_profile(self) -> "WorkerProfileSpec":
        if self.requires_native_search and not self.model:
            raise ValueError("native-search Worker Profile requires an explicit model preset")
        if self.requires_native_search and self.execution_mode != "one_shot":
            raise ValueError("native-search Worker Profile must use execution_mode=one_shot")
        if self.requires_native_search and self.inherit_tool_policy:
            raise ValueError("native-search Worker Profile cannot inherit global tool grants")
        return self


class AgentRuntimeConfig(StrictConfigModel):
    tools: ToolRuntimeConfig = Field(default_factory=ToolRuntimeConfig)
    worker_profiles: dict[str, WorkerProfileSpec] = Field(default_factory=dict)


def load_agent_runtime_config(path: Path | None = None) -> AgentRuntimeConfig:
    if path is None:
        raw = load_runtime_config()
    else:
        import yaml

        with path.open("r", encoding="utf-8") as file:
            raw = yaml.safe_load(file) or {}
    payload = {
        "tools": raw.get("tools", {}),
        "worker_profiles": raw.get("worker_profiles", {}),
    }
    profiles = payload["worker_profiles"]
    if isinstance(profiles, dict):
        payload["worker_profiles"] = {
            name: ({"name": name, **value} if isinstance(value, dict) else value)
            for name, value in profiles.items()
        }
    return AgentRuntimeConfig.model_validate(payload)
