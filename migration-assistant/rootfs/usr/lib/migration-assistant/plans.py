"""Loading and validation of migration plans.

A plan is a small YAML file that describes where an app moves from and to. It
is the only thing a maintainer has to write to make an app migratable, no code
in this app has to change for it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

SHIPPED_PLANS = Path("/usr/share/migration-assistant/plans")
USER_PLANS = Path("/config/plans")


class PlanError(Exception):
    """Raised when a plan file cannot be used."""


@dataclass
class Endpoint:
    """One side of a migration."""

    slug: str
    name: str = ""
    repository: str = ""
    min_version: str = ""
    max_version: str = ""


@dataclass
class Plan:
    """A single migration description."""

    id: str
    name: str
    source: Endpoint
    target: Endpoint
    description: str = ""
    notes: str = ""
    origin: str = "shipped"
    options: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return the plan in the shape the frontend expects."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "notes": self.notes,
            "origin": self.origin,
            "source": vars(self.source),
            "target": vars(self.target),
        }


def _endpoint(raw: Any, side: str, plan_id: str) -> Endpoint:
    """Build an endpoint out of the raw YAML."""
    if not isinstance(raw, dict):
        raise PlanError(f"{plan_id}: '{side}' has to be a mapping")
    slug = raw.get("slug")
    if not isinstance(slug, str) or "_" not in slug:
        raise PlanError(
            f"{plan_id}: '{side}.slug' has to be a full app slug, "
            "for example '396f0234_cloudflared'"
        )
    return Endpoint(
        slug=slug,
        name=str(raw.get("name") or ""),
        repository=str(raw.get("repository") or ""),
        min_version=str(raw.get("min_version") or ""),
        max_version=str(raw.get("max_version") or ""),
    )


def parse(raw: Any, origin: str, fallback_id: str) -> Plan:
    """Turn the contents of a plan file into a plan."""
    if not isinstance(raw, dict):
        raise PlanError(f"{fallback_id}: the plan has to be a mapping")

    version = raw.get("version", 1)
    if version != 1:
        raise PlanError(f"{fallback_id}: unsupported plan version {version!r}")

    plan_id = str(raw.get("id") or fallback_id)
    source = _endpoint(raw.get("source"), "source", plan_id)
    target = _endpoint(raw.get("target"), "target", plan_id)

    if source.slug == target.slug:
        raise PlanError(f"{plan_id}: source and target are the same app")
    if not target.repository:
        raise PlanError(f"{plan_id}: 'target.repository' is required")

    options = raw.get("options") or {}
    if not isinstance(options, dict):
        raise PlanError(f"{plan_id}: 'options' has to be a mapping")
    for key, expected in (("rename", dict), ("defaults", dict), ("remove", list)):
        if key in options and not isinstance(options[key], expected):
            raise PlanError(
                f"{plan_id}: 'options.{key}' has to be a "
                f"{'mapping' if expected is dict else 'list'}"
            )

    return Plan(
        id=plan_id,
        name=str(raw.get("name") or plan_id),
        description=str(raw.get("description") or ""),
        notes=str(raw.get("notes") or ""),
        source=source,
        target=target,
        options=options,
        origin=origin,
    )


def _load_dir(directory: Path, origin: str) -> tuple[list[Plan], list[str]]:
    """Load every plan of a directory, collecting errors instead of raising."""
    plans: list[Plan] = []
    errors: list[str] = []
    if not directory.is_dir():
        return plans, errors

    for path in sorted(directory.glob("*.y*ml")):
        try:
            plans.append(parse(yaml.safe_load(path.read_text()), origin, path.stem))
        except (PlanError, yaml.YAMLError, OSError) as err:
            errors.append(f"{path.name}: {err}")
    return plans, errors


def load_all() -> tuple[list[Plan], list[str]]:
    """Load the shipped plans and, when enabled, the ones from /config."""
    plans, errors = _load_dir(SHIPPED_PLANS, "shipped")

    if os.environ.get("EXTRA_PLANS", "false").lower() == "true":
        extra, extra_errors = _load_dir(USER_PLANS, "user")
        known = {plan.id for plan in plans}
        for plan in extra:
            if plan.id in known:
                errors.append(f"{plan.id}: a shipped plan with this id already exists")
                continue
            plans.append(plan)
        errors.extend(extra_errors)

    return plans, errors
