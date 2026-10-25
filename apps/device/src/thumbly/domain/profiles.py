"""App profiles: per-app deep links, task workflows and quirks an AI client follows.

One TOML file per bundle identifier. Deep links are deterministic entry points Thumbly opens
through ``open_url``; workflows are recipes written in screen landmarks, executed by the agent
one screenshot at a time. Coordinates never belong here: they drift with app updates.
"""

import tomllib
from importlib import resources
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, model_validator

from thumbly.domain.errors import ThumblyError

STEP_KINDS = (
    "open",
    "tap",
    "long_press",
    "swipe",
    "send_text",
    "type_text",
    "key",
    "button",
    "wait",
    "check",
)


class ProfileModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Verified(ProfileModel):
    date: str = Field(max_length=10)  # YYYY-MM-DD
    app: str = Field(default="", max_length=40)
    ios: str = Field(default="", max_length=20)
    device: str = Field(default="", max_length=40)


class DeepLink(ProfileModel):
    url: str = Field(max_length=2048)  # may contain {placeholders}
    expect: str = Field(default="", max_length=400)
    status: Literal["verified", "untested", "broken"] = "untested"  # broken: tested, app ignores it
    params: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def placeholders(self):
        found = _placeholders(self.url)
        if self.params and set(self.params) != found:
            raise ValueError(f"params {self.params} do not match placeholders {sorted(found)} in {self.url}")
        self.params = sorted(found)
        return self


class Step(ProfileModel):
    """Exactly one action key, plus optional ``landmark`` and ``expect`` for the agent."""

    open: str | None = None  # deep link name
    tap: str | None = None  # landmark
    long_press: str | None = None
    swipe: str | None = None  # e.g. "up from the bottom edge"
    send_text: str | None = None  # text, may contain {placeholders}
    type_text: str | None = None
    key: str | None = None  # "cmd+enter"
    button: str | None = None
    wait: float | None = Field(default=None, ge=0, le=30)
    check: str | None = None  # observation only
    expect: str = Field(default="", max_length=400)

    @model_validator(mode="after")
    def one_action(self):
        set_keys = [k for k in STEP_KINDS if getattr(self, k) is not None]
        if len(set_keys) != 1:
            raise ValueError(f"a step needs exactly one of {STEP_KINDS}, got {set_keys}")
        return self

    @property
    def kind(self) -> str:
        return next(k for k in STEP_KINDS if getattr(self, k) is not None)


class Workflow(ProfileModel):
    goal: str = Field(max_length=200)
    inputs: list[str] = Field(default_factory=list, max_length=8)
    steps: list[Step] = Field(min_length=1, max_length=30)
    stop_if: list[str] = Field(default_factory=list, max_length=12)
    status: Literal["verified", "untested"] = "untested"


class AppProfile(ProfileModel):
    bundle_id: str = Field(max_length=255, pattern=r"^[A-Za-z0-9.\-]+$")
    name: str = Field(max_length=80)
    schemes: list[str] = Field(default_factory=list, max_length=16)
    cold_launch_for_url: bool = True
    text_input: Literal["send_text", "type_text"] = "send_text"
    summary: str = Field(default="", max_length=400)
    verified: Verified
    deep_links: dict[str, DeepLink] = Field(default_factory=dict)
    workflows: dict[str, Workflow] = Field(default_factory=dict)
    quirks: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def references(self):
        for name, flow in self.workflows.items():
            for step in flow.steps:
                if step.open is not None and step.open not in self.deep_links:
                    raise ValueError(f"workflow {name} opens unknown deep link {step.open}")
        return self

    def link(self, name: str, params: dict[str, str]) -> str:
        """Fill a deep link's placeholders; every placeholder must be supplied, nothing else.

        Values are given as plain text. A placeholder inside the query or fragment part of the
        template is percent-encoded (spaces become ``%20``); a placeholder in the scheme, host or
        path part is inserted as written, so it must not contain whitespace."""
        link = self.deep_links.get(name)
        if link is None:
            raise ThumblyError("LINK_NOT_FOUND", f"{self.bundle_id} has no deep link {name}", 404)
        missing = [p for p in link.params if p not in params]
        extra = [p for p in params if p not in link.params]
        if missing or extra:
            raise ThumblyError("LINK_PARAMS", f"missing {missing}, unexpected {extra}", 400)
        filled: dict[str, str] = {}
        for key, value in params.items():
            if any(ord(c) < 32 or c == "\x7f" for c in value):
                raise ThumblyError("LINK_PARAMS", f"{key} must not contain control characters", 400)
            if _in_query(link.url, key):
                filled[key] = quote(value, safe="")
            elif any(c.isspace() for c in value):
                raise ThumblyError("LINK_PARAMS", f"{key} must not contain whitespace", 400)
            else:
                filled[key] = value
        return link.url.format_map(filled)

    def describe(self) -> dict:
        """The tool payload: the profile with each step spelled as ``{kind, value, expect}``."""
        data = self.model_dump()
        for name, flow in self.workflows.items():
            data["workflows"][name]["steps"] = [
                {"kind": step.kind, "value": getattr(step, step.kind), "expect": step.expect}
                for step in flow.steps
            ]
        return data


def _in_query(template: str, name: str) -> bool:
    """Whether ``{name}`` sits after the first ``?`` or ``#`` of the template."""
    position = template.find("{" + name + "}")
    boundary = min((i for i in (template.find("?"), template.find("#")) if i >= 0), default=-1)
    return position >= 0 and boundary >= 0 and position > boundary


def _placeholders(template: str) -> set[str]:
    found: set[str] = set()
    depth, current = 0, ""
    for char in template:
        if char == "{":
            depth += 1
            current = ""
        elif char == "}" and depth:
            depth -= 1
            if not current.isidentifier():
                raise ValueError(f"bad placeholder {{{current}}} in {template}")
            found.add(current)
        elif depth:
            current += char
    if depth:
        raise ValueError(f"unbalanced braces in {template}")
    return found


class ProfileStore:
    """Profiles shipped in the package plus owner overrides under ``<state_dir>/apps``."""

    def __init__(self, override_dir: Path | None = None):
        self.override_dir = override_dir
        self._profiles: dict[str, AppProfile] | None = None

    def load(self) -> dict[str, AppProfile]:
        profiles: dict[str, AppProfile] = {}
        package = resources.files("thumbly") / "profiles"
        # Dotfiles are editor and copy leftovers (macOS ``._*`` resource forks), never profiles.
        for entry in sorted(package.iterdir(), key=lambda e: e.name):
            if entry.name.endswith(".toml") and not entry.name.startswith("."):
                profiles.update(_parse(entry.name, entry.read_bytes()))
        if self.override_dir is not None and self.override_dir.is_dir():
            for path in sorted(self.override_dir.glob("[!.]*.toml")):
                profiles.update(_parse(path.name, path.read_bytes()))
        self._profiles = profiles
        return profiles

    @property
    def profiles(self) -> dict[str, AppProfile]:
        if self._profiles is None:
            self.load()
        return self._profiles or {}

    def get(self, bundle_id: str) -> AppProfile:
        profile = self.profiles.get(bundle_id)
        if profile is None:
            raise ThumblyError("PROFILE_NOT_FOUND", f"No app profile for {bundle_id}", 404)
        return profile

    def ids(self) -> list[str]:
        return sorted(self.profiles)


def _parse(filename: str, raw: bytes) -> dict[str, AppProfile]:
    try:
        profile = AppProfile.model_validate(tomllib.loads(raw.decode("utf-8")))
    except (tomllib.TOMLDecodeError, ValueError) as exc:
        raise ThumblyError("PROFILE_INVALID", f"{filename}: {exc}", 500) from None
    if filename != f"{profile.bundle_id}.toml":
        raise ThumblyError("PROFILE_INVALID", f"{filename} must be named {profile.bundle_id}.toml", 500)
    return {profile.bundle_id: profile}
