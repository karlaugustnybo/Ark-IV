"""Route an entire request to one validated coverage dataset."""

from __future__ import annotations

import json
import os
from contextvars import ContextVar
from pathlib import Path
from urllib.parse import parse_qs

from litestar.middleware.base import AbstractMiddleware

from backend.config import Settings, get_settings

PROFILE_IDS = ("resident", "with-passage")
LABELS = {"resident": "Default seasons", "with-passage": "Include passage"}
current_profile: ContextVar[str] = ContextVar("coverage_profile", default="resident")
current_settings: ContextVar[Settings | None] = ContextVar("coverage_settings", default=None)


class RequestSettings:
    """Keep existing handlers and their test seams while scoping paths per request."""

    def __getattr__(self, name):
        return getattr(current_settings.get() or get_settings(), name)


def load_profiles(default: Settings) -> dict[str, Settings]:
    path = default.coverage_profiles_path
    if path is None:
        return {"resident": default}
    from ark_pipeline.cli.map_serving import reject_benchmark_bundle, validate_config

    manifest = json.loads(path.read_text())
    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported coverage profile manifest")
    result = {}
    for key, record in manifest["profiles"].items():
        if key not in PROFILE_IDS:
            raise ValueError(f"Unknown coverage profile: {key}")
        config_path = Path(record["serving_config"])
        if not config_path.is_absolute():
            config_path = path.parent / config_path
        config = validate_config(config_path)
        if "EXPORT_DIR" not in config["environment"]:
            raise ValueError("Coverage profile requires matching PostgreSQL exports")
        metadata = json.loads(Path(config["environment"]["MAP_METADATA_PATH"]).read_text())
        seasons = metadata.get("spatial_semantics", {}).get("seasonality")
        if key == "with-passage" and (not seasons or set(seasons) != {1, 2, 3, 4, 5}):
            raise ValueError(
                "Passage profile must be built with migration-inclusive spatial semantics"
            )
        if key == "resident" and set(seasons or []) != {1, 2, 3, 5}:
            raise ValueError("Resident profile must declare default seasonal semantics")
        reject_benchmark_bundle(config_path, config)
        # Profile selection must encompass SQL membership and score calculation,
        # not just the visible tile archive. Credentials remain in environment.
        database = os.environ.get(record["database_url_env"])
        if not database:
            raise ValueError(f"Missing database environment variable for {key}")
        values = default.model_dump()
        values.update({name.lower(): value for name, value in config["environment"].items()})
        values["database_url"] = database
        result[key] = Settings(_env_file=None, **values)
    if "resident" not in result:
        raise ValueError("A resident coverage profile is required")
    if (
        "with-passage" in result
        and result["with-passage"].database_url == result["resident"].database_url
    ):
        raise ValueError("Coverage profiles must use separate serving databases")
    return result


class CoverageProfileMiddleware(AbstractMiddleware):
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        query = parse_qs(
            scope.get("query_string", b"").decode("ascii", errors="replace"), keep_blank_values=True
        )
        selections = query.get("coverage_profile", ["resident"])
        key = selections[0]
        registry = getattr(scope["app"].state, "coverage_settings", {"resident": get_settings()})
        if len(selections) != 1 or key not in PROFILE_IDS or key not in registry:
            status = 409 if len(selections) == 1 and key in PROFILE_IDS else 400
            body = json.dumps(
                {
                    "detail": "This coverage profile has not been built and configured"
                    if status == 409
                    else "Unknown coverage profile"
                }
            ).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": status,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        profile_token = current_profile.set(key)
        settings_token = current_settings.set(registry[key])
        try:
            await self.app(scope, receive, send)
        finally:
            current_settings.reset(settings_token)
            current_profile.reset(profile_token)
