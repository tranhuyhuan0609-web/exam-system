"""G06 Vertical Packs, Localization & Accessibility non-invasive extension registry."""

from __future__ import annotations

from dataclasses import dataclass, replace


class VerticalPackError(ValueError):
    """Raised when vertical pack isolation invariants or activation rules are violated."""


@dataclass(frozen=True)
class VerticalPackConfig:
    code: str
    display_name: str
    taxonomy_root_prefix: str
    assessment_framework_code: str
    scoring_policy_code: str
    default_locale: str = "vi-VN"
    is_core_k12: bool = False
    active: bool = False


class VerticalPackRegistry:
    """In-memory isolated registry ensuring expansion verticals never alter core K-12 configs."""

    def __init__(self, initial_packs: tuple[VerticalPackConfig, ...] = ()) -> None:
        self._packs: dict[str, VerticalPackConfig] = {}
        self._prefixes: dict[str, str] = {}
        for pack in initial_packs:
            self.register(pack)

    def register(self, pack: VerticalPackConfig) -> VerticalPackConfig:
        code = pack.code.strip().upper()
        prefix = pack.taxonomy_root_prefix.strip().upper()
        if not code or not prefix:
            raise VerticalPackError("pack code and taxonomy_root_prefix are required")
        if code in self._packs:
            raise VerticalPackError(f"duplicate vertical pack code: {code}")
        owner = self._prefixes.get(prefix)
        if owner is not None and owner != code:
            raise VerticalPackError(
                f"taxonomy_root_prefix {prefix!r} already reserved by pack {owner!r}"
            )
        normalized = replace(pack, code=code, taxonomy_root_prefix=prefix)
        self._packs[code] = normalized
        self._prefixes[prefix] = code
        return normalized

    def activate(self, code: str) -> VerticalPackConfig:
        key = code.strip().upper()
        if key not in self._packs:
            raise VerticalPackError(f"unknown vertical pack: {code!r}")
        updated = replace(self._packs[key], active=True)
        self._packs[key] = updated
        return updated

    def deactivate(self, code: str) -> VerticalPackConfig:
        key = code.strip().upper()
        if key not in self._packs:
            raise VerticalPackError(f"unknown vertical pack: {code!r}")
        pack = self._packs[key]
        if pack.is_core_k12:
            raise VerticalPackError("cannot deactivate core K-12 vertical pack")
        updated = replace(pack, active=False)
        self._packs[key] = updated
        return updated

    def get(self, code: str) -> VerticalPackConfig:
        key = code.strip().upper()
        if key not in self._packs:
            raise VerticalPackError(f"unknown vertical pack: {code!r}")
        return self._packs[key]

    def list_active(self) -> tuple[VerticalPackConfig, ...]:
        return tuple(p for p in self._packs.values() if p.active)


def resolve_localized_label(
    *,
    bundles: dict[str, dict[str, str]],
    key: str,
    requested_locale: str,
    default_locale: str = "vi-VN",
) -> str:
    """Resolve UI/vertical label via exact locale -> base language -> vi-VN fallback -> raw key."""
    normalized_req = requested_locale.strip()
    candidates = [normalized_req]
    if "-" in normalized_req:
        candidates.append(normalized_req.split("-", 1)[0])
    candidates.append(default_locale)
    if "-" in default_locale:
        candidates.append(default_locale.split("-", 1)[0])

    for loc in candidates:
        bundle = bundles.get(loc)
        if bundle and key in bundle and bundle[key].strip():
            return bundle[key]
    return key
