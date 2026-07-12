"""Safety policy: command allow/deny lists, env inheritance, and sandbox profiles.

A pipeline may carry a top-level ``policy`` block that constrains how its tasks
run — useful when executing untrusted or semi-trusted pipeline definitions:

* **Command policy** — an allowlist and/or denylist of command patterns. Denies
  win, and when an allowlist is set anything not matching it is refused
  (fail-closed). A denied command is never executed.
* **Environment inheritance** — controls which parent-process environment
  variables a task's command inherits: all, none, or an explicit allowlist of
  names. A task's own ``env`` is always applied on top.
* **Sandbox profiles** — a declarative model of named isolation profiles that
  tasks can reference. It is validated and recorded now; enforcement is left to
  future platform-specific backends.

Parsing is strict: an invalid ``policy`` block raises :class:`SpecError`.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Tuple

from limbo.errors import SpecError

_ENV_MODES = ("all", "none")


@dataclass(frozen=True)
class CommandPolicy:
    """Allow/deny rules for task commands. Deny wins; allowlist is fail-closed."""

    allow: Tuple[str, ...] = ()
    deny: Tuple[str, ...] = ()

    def violation(self, command: str) -> Optional[str]:
        """Return a reason string if ``command`` is disallowed, else ``None``."""

        for pattern in self.deny:
            if _matches(pattern, command):
                return f"command matches denylist entry {pattern!r}"
        if self.allow and not any(_matches(pattern, command) for pattern in self.allow):
            return "command is not permitted by the allowlist"
        return None


@dataclass(frozen=True)
class EnvPolicy:
    """Controls which parent environment variables a task inherits."""

    inherit: str = "all"
    allow: Tuple[str, ...] = ()

    def resolve(self, parent_env: Mapping[str, str], task_env: Mapping[str, str]) -> Dict[str, str]:
        """Build the effective environment for a task under this policy."""

        if self.allow:
            base = {name: value for name, value in parent_env.items() if name in self.allow}
        elif self.inherit == "none":
            base = {}
        else:  # "all"
            base = dict(parent_env)
        base.update(task_env)
        return base


@dataclass(frozen=True)
class SandboxProfile:
    """A declarative isolation profile (a model for future enforcement)."""

    name: str
    network: bool = False
    allow_paths: Tuple[str, ...] = ()
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "network": self.network,
            "allow_paths": list(self.allow_paths),
            "description": self.description,
        }


@dataclass(frozen=True)
class Policy:
    """The full safety policy attached to a pipeline."""

    commands: CommandPolicy = field(default_factory=CommandPolicy)
    env: EnvPolicy = field(default_factory=EnvPolicy)
    sandbox_profiles: Mapping[str, SandboxProfile] = field(default_factory=dict)


NO_POLICY = Policy()


def _matches(pattern: str, command: str) -> bool:
    command = command.strip()
    tokens = command.split()
    first = tokens[0] if tokens else ""
    return fnmatch.fnmatch(command, pattern) or fnmatch.fnmatch(first, pattern)


def parse_policy(value: Any) -> Policy:
    """Validate and build a :class:`Policy` from a pipeline's ``policy`` block."""

    if value is None:
        return NO_POLICY
    if not isinstance(value, Mapping):
        raise SpecError("policy must be an object")

    unknown = set(value) - {"commands", "env", "sandbox_profiles"}
    if unknown:
        raise SpecError(f"unknown policy field(s): {', '.join(sorted(unknown))}")

    commands = _parse_command_policy(value.get("commands"))
    env = _parse_env_policy(value.get("env"))
    sandbox_profiles = _parse_sandbox_profiles(value.get("sandbox_profiles"))
    return Policy(commands=commands, env=env, sandbox_profiles=sandbox_profiles)


def _parse_command_policy(value: Any) -> CommandPolicy:
    if value is None:
        return CommandPolicy()
    if not isinstance(value, Mapping):
        raise SpecError("policy.commands must be an object")
    unknown = set(value) - {"allow", "deny"}
    if unknown:
        raise SpecError(f"unknown policy.commands field(s): {', '.join(sorted(unknown))}")
    return CommandPolicy(
        allow=_string_tuple(value.get("allow", []), "policy.commands.allow"),
        deny=_string_tuple(value.get("deny", []), "policy.commands.deny"),
    )


def _parse_env_policy(value: Any) -> EnvPolicy:
    if value is None:
        return EnvPolicy()
    if not isinstance(value, Mapping):
        raise SpecError("policy.env must be an object")
    unknown = set(value) - {"inherit", "allow"}
    if unknown:
        raise SpecError(f"unknown policy.env field(s): {', '.join(sorted(unknown))}")
    inherit = value.get("inherit", "all")
    if inherit not in _ENV_MODES:
        raise SpecError(f"policy.env.inherit must be one of {', '.join(_ENV_MODES)}")
    return EnvPolicy(inherit=inherit, allow=_string_tuple(value.get("allow", []), "policy.env.allow"))


def _parse_sandbox_profiles(value: Any) -> Dict[str, SandboxProfile]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise SpecError("policy.sandbox_profiles must be an object")
    profiles: Dict[str, SandboxProfile] = {}
    for name, raw in value.items():
        if not isinstance(name, str) or not name:
            raise SpecError("sandbox profile names must be non-empty strings")
        if not isinstance(raw, Mapping):
            raise SpecError(f"sandbox profile {name!r} must be an object")
        unknown = set(raw) - {"network", "allow_paths", "description"}
        if unknown:
            raise SpecError(f"sandbox profile {name!r}: unknown field(s): {', '.join(sorted(unknown))}")
        network = raw.get("network", False)
        if not isinstance(network, bool):
            raise SpecError(f"sandbox profile {name!r}: network must be a boolean")
        description = raw.get("description", "")
        if not isinstance(description, str):
            raise SpecError(f"sandbox profile {name!r}: description must be a string")
        profiles[name] = SandboxProfile(
            name=name,
            network=network,
            allow_paths=_string_tuple(raw.get("allow_paths", []), f"sandbox profile {name!r} allow_paths"),
            description=description,
        )
    return profiles


def _string_tuple(value: Any, label: str) -> Tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise SpecError(f"{label} must be a list of non-empty strings")
    return tuple(value)
