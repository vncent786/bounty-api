"""Central named credential resolution with secret-safe representations."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path


_CREDENTIAL_REF_RE = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")


@dataclass(frozen=True)
class CredentialAlternative:
    """One complete set of environment keys that can satisfy a named reference."""

    env_keys: tuple[str, ...]
    label: str = ""
    file_paths: tuple[str, ...] = ()
    runtime_managed: bool = False

    def __post_init__(self) -> None:
        if not self.env_keys and not self.file_paths and not self.runtime_managed:
            raise ValueError("credential alternative must declare keys, files, or runtime management")


@dataclass(frozen=True)
class CredentialDefinition:
    name: str
    alternatives: tuple[CredentialAlternative, ...]
    description: str = ""

    def __post_init__(self) -> None:
        if not _CREDENTIAL_REF_RE.fullmatch(self.name):
            raise ValueError(f"invalid credential reference: {self.name}")
        if not self.alternatives:
            raise ValueError("credential definition requires at least one alternative")


class MissingCredentialError(RuntimeError):
    """Raised without secret values or environment-variable names."""

    def __init__(self, credential_ref: str):
        self.credential_ref = credential_ref
        super().__init__(f"credential_missing:{credential_ref}")


class CredentialBundle(Mapping[str, str]):
    """Resolved secret values whose repr and string conversion never disclose values."""

    __slots__ = ("credential_ref", "_values")

    def __init__(self, credential_ref: str | None, values: Mapping[str, str] | None = None):
        self.credential_ref = credential_ref
        self._values = dict(values or {})

    def __getitem__(self, key: str) -> str:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        return f"CredentialBundle(ref={self.credential_ref!r}, fields={len(self._values)})"

    __str__ = __repr__

    @property
    def redaction_values(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {value for value in self._values.values() if isinstance(value, str) and value},
                key=len,
                reverse=True,
            )
        )


class CredentialResolver:
    """Resolve named references from one centrally controlled environment mapping."""

    def __init__(
        self,
        environment: Mapping[str, str] | None = None,
        base_path: str | Path | None = None,
    ):
        self._environment = environment if environment is not None else os.environ
        self._base_path = Path(base_path) if base_path is not None else Path.cwd()
        self._definitions: dict[str, CredentialDefinition] = {}

    def register(self, definition: CredentialDefinition) -> None:
        if definition.name in self._definitions:
            raise ValueError(f"credential reference already registered: {definition.name}")
        self._definitions[definition.name] = definition

    def resolve(self, credential_ref: str | None) -> CredentialBundle:
        if credential_ref is None:
            return CredentialBundle(None)
        definition = self._definitions.get(credential_ref)
        if definition is None:
            raise MissingCredentialError(credential_ref)
        for alternative in definition.alternatives:
            if alternative.runtime_managed:
                return CredentialBundle(credential_ref)
            values = {
                key: str(self._environment.get(key, "")).strip()
                for key in alternative.env_keys
            }
            files = {
                f"file:{path}": str((self._base_path / path).resolve())
                for path in alternative.file_paths
                if (self._base_path / path).is_file()
            }
            if all(values.values()) and len(files) == len(alternative.file_paths):
                values.update(files)
                return CredentialBundle(credential_ref, values)
        raise MissingCredentialError(credential_ref)

    def references(self) -> tuple[str, ...]:
        return tuple(sorted(self._definitions))

    @classmethod
    def with_bounty_defaults(
        cls,
        environment: Mapping[str, str] | None = None,
        base_path: str | Path | None = None,
    ) -> "CredentialResolver":
        resolver = cls(environment=environment, base_path=base_path)
        definitions = (
            CredentialDefinition(
                name="x_owned_session",
                alternatives=(CredentialAlternative(("BOUNTY_X_AUTH_TOKEN",)),),
                description="Owned authenticated X web session.",
            ),
            CredentialDefinition(
                name="x_official_api",
                alternatives=(CredentialAlternative(("BOUNTY_X_BEARER_TOKEN",)),),
                description="Optional official X API route.",
            ),
            CredentialDefinition(
                name="instagram_owned_session",
                alternatives=(
                    CredentialAlternative(("BOUNTY_IG_COOKIE_PATH",), "cookie_file"),
                    CredentialAlternative(
                        (),
                        "default_cookie_file",
                        ("data/ig_cookies.json",),
                    ),
                    CredentialAlternative(
                        ("BOUNTY_IG_USERNAME", "BOUNTY_IG_PASSWORD"),
                        "username_password",
                    ),
                ),
                description="Owned Instagram web session.",
            ),
            CredentialDefinition(
                name="tiktok_owned_session",
                alternatives=(CredentialAlternative((), "persistent_browser_profile", runtime_managed=True),),
                description="Owned authenticated TikTok browser profile.",
            ),
            CredentialDefinition(
                name="reddit_mobile_device",
                alternatives=(CredentialAlternative((), "runtime_device_token", runtime_managed=True),),
                description="Runtime-acquired Reddit mobile device session.",
            ),
            CredentialDefinition(
                name="douyin_owned_session",
                alternatives=(CredentialAlternative((), "persistent_browser_profile", runtime_managed=True),),
                description="Owned Douyin browser session.",
            ),
            CredentialDefinition(
                name="xiaohongshu_owned_session",
                alternatives=(CredentialAlternative((), "persistent_browser_profile", runtime_managed=True),),
                description="Owned Xiaohongshu browser session.",
            ),
            CredentialDefinition(
                name="brave_search_api",
                alternatives=(
                    CredentialAlternative(("BOUNTY_BRAVE_SEARCH_API_KEY",)),
                ),
                description="Optional Brave Search API route.",
            ),
            CredentialDefinition(
                name="oag_api",
                alternatives=(CredentialAlternative(("BOUNTY_OAG_API_KEY",)),),
                description="Disabled paid OAG placeholder; no adapter is registered.",
            ),
            CredentialDefinition(
                name="forwardkeys_api",
                alternatives=(CredentialAlternative(("BOUNTY_FORWARDKEYS_API_KEY",)),),
                description="Disabled paid ForwardKeys placeholder; no adapter is registered.",
            ),
        )
        for definition in definitions:
            resolver.register(definition)
        return resolver
