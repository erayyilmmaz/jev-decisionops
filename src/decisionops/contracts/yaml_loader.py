"""Safe YAML loading with duplicate-key rejection for Decision Contracts."""

from __future__ import annotations

from typing import Any

import yaml


class DuplicateYamlKeyError(ValueError):
    """YAML mappings must not silently replace an earlier contract field."""

    def __init__(self, key: object, line: int, column: int) -> None:
        self.key = key
        self.line = line
        self.column = column
        super().__init__(f"duplicate key {key!r} at line {line}, column {column}")


class UniqueKeySafeLoader(yaml.SafeLoader):
    """SafeLoader variant that rejects duplicate mapping keys."""


def _construct_unique_mapping(
    loader: UniqueKeySafeLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[object, Any]:
    mapping: dict[object, Any] = {}

    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise DuplicateYamlKeyError(
                key=key,
                line=key_node.start_mark.line + 1,
                column=key_node.start_mark.column + 1,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)

    return mapping


UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def load_yaml(text: str) -> object:
    """Load one safe YAML document and reject implicit duplicate-field replacement."""

    return yaml.load(text, Loader=UniqueKeySafeLoader)
