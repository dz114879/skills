"""Immutable rule metadata, exact wording templates, and probe vocabularies."""

from collections.abc import Mapping
import json
from pathlib import Path
from types import MappingProxyType


_catalogue = json.loads(
    Path(__file__).with_name("rules.json").read_text(encoding="utf-8")
)
_vocabularies = _catalogue["vocabularies"]

RULES = MappingProxyType(
    {
        rule: MappingProxyType(
            {**metadata, "catalogue_sections": tuple(metadata["catalogue_sections"])}
        )
        for rule, metadata in _catalogue["rules"].items()
    }
)
VERIFY_TEMPLATES = MappingProxyType(_catalogue["verify_templates"])
VERIFY_KEYS: Mapping[str, str] = MappingProxyType(
    {text: key for key, text in VERIFY_TEMPLATES.items()}
)
EVIDENCE_TEMPLATES = MappingProxyType(_catalogue["evidence_templates"])

SKIP_SEGMENTS: frozenset[str] = frozenset(_vocabularies["skip_segments"])
TEST_GLOBS: tuple[str, ...] = tuple(_vocabularies["test_globs"])
DIFF_FLAGS: tuple[str, ...] = tuple(_vocabularies["diff_flags"])
PY07_DEPENDENCY_SUFFIXES: frozenset[str] = frozenset(
    _vocabularies["py07_dependency_suffixes"]
)
PY08_ROUNDTRIP_PAIRS: frozenset[tuple[str, str]] = frozenset(
    tuple(pair) for pair in _vocabularies["py08_roundtrip_pairs"]
)
PY08_COPY_CALLS: frozenset[str] = frozenset(_vocabularies["py08_copy_calls"])
PY09_LOCK_TOKENS: frozenset[str] = frozenset(_vocabularies["py09_lock_tokens"])
PY09_TX_TOKENS = MappingProxyType(
    {
        name: frozenset(tokens)
        for name, tokens in _vocabularies["py09_tx_tokens"].items()
    }
)
PY09_IO_CALLS = MappingProxyType(
    {name: tuple(calls) for name, calls in _vocabularies["py09_io_calls"].items()}
)
PY10B_PATCH_METHODS = MappingProxyType(
    {
        name: frozenset(methods)
        for name, methods in _vocabularies["py10b_patch_methods"].items()
    }
)
PY10C_FRAMEWORK_DECORATORS: frozenset[str] = frozenset(
    _vocabularies["py10c_framework_decorators"]
)


def verify_text(variant_key: str) -> str:
    return VERIFY_TEMPLATES[variant_key]


def evidence_text(variant_key: str, **kwargs: object) -> str:
    template = EVIDENCE_TEMPLATES[variant_key]
    return template.format(**kwargs) if kwargs else template
