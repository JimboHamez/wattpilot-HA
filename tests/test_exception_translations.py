"""Consistency checks between the raised translation keys and the string catalogs.

Service handlers and entity actions raise with a ``translation_key`` rather than
a literal message (quality-scale rule ``exception-translations``). Nothing at runtime notices when a
key has no ``exceptions`` entry — Home Assistant silently falls back to showing the
bare key — so assert the mapping here.

Dependency-free: the keys are read out of the integration's module sources with ``ast``,
so this runs without Home Assistant or the client library installed.
"""

from __future__ import annotations

import ast
import json
import os
import re

from .conftest import COMPONENT_DIR

TRANSLATION_FILES = [
    os.path.join(COMPONENT_DIR, "strings.json"),
    os.path.join(COMPONENT_DIR, "translations", "en.json"),
    os.path.join(COMPONENT_DIR, "translations", "de.json"),
]

PLACEHOLDER = re.compile(r"{(\w+)}")


def _trees(*names: str) -> list[ast.Module]:
    """Return the parsed sources of the named modules, or of every module if none are named."""
    files = names or sorted(name for name in os.listdir(COMPONENT_DIR) if name.endswith(".py"))
    trees = []
    for name in files:
        with open(os.path.join(COMPONENT_DIR, name), encoding="utf-8") as handle:
            trees.append(ast.parse(handle.read()))
    return trees


def _raises(*modules: str) -> dict[str, set[str]]:
    """Return every translation key raised by the integration, with its placeholder names.

    Args:
        modules: Limit the search to these module file names; all modules by default.
    """
    found: dict[str, set[str]] = {}
    for node in (node for tree in _trees(*modules) for node in ast.walk(tree)):
        if not isinstance(node, ast.Call):
            continue
        kwargs = {kw.arg: kw.value for kw in node.keywords}
        key = kwargs.get("translation_key")
        if not isinstance(key, ast.Constant):
            continue
        placeholders = kwargs.get("translation_placeholders")
        names: set[str] = set()
        if isinstance(placeholders, ast.Dict):
            names = {k.value for k in placeholders.keys if isinstance(k, ast.Constant)}
        found[str(key.value)] = names
    return found


def _exceptions(path: str) -> dict[str, str]:
    """Return the ``exceptions`` messages of a translation file, keyed by translation key."""
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    return {key: value["message"] for key, value in data.get("exceptions", {}).items()}


def test_services_raise_with_translation_keys():
    """Every raise in services.py carries a translation key."""
    assert _raises("services.py"), "services.py should raise with translation keys"


def test_entity_actions_raise_with_translation_keys():
    """The shared entity-action errors carry translation keys too."""
    raised = _raises("entities.py")
    assert {"entity_action_failed", "entity_write_failed"} <= set(raised)


def test_every_raised_key_is_translated():
    """Each raised key has a message in every catalog, and none are orphaned."""
    raised = set(_raises())
    for path in TRANSLATION_FILES:
        translated = set(_exceptions(path))
        assert not raised - translated, f"{os.path.basename(path)} is missing: {sorted(raised - translated)}"
        assert not translated - raised, f"{os.path.basename(path)} has orphans: {sorted(translated - raised)}"


def test_messages_only_use_supplied_placeholders():
    """A message never interpolates a placeholder the raise does not pass."""
    raised = _raises()
    for path in TRANSLATION_FILES:
        for key, message in _exceptions(path).items():
            unknown = set(PLACEHOLDER.findall(message)) - raised[key]
            assert not unknown, f"{os.path.basename(path)}: {key} uses unsupplied placeholder(s) {sorted(unknown)}"


def test_translations_agree_on_placeholders():
    """The localised messages interpolate the same placeholders as the English ones."""
    english = {key: set(PLACEHOLDER.findall(msg)) for key, msg in _exceptions(TRANSLATION_FILES[1]).items()}
    for path in TRANSLATION_FILES[2:]:
        for key, message in _exceptions(path).items():
            assert set(PLACEHOLDER.findall(message)) == english[key], (
                f"{os.path.basename(path)}: {key} placeholders differ from en.json"
            )


def test_strings_and_english_translation_match():
    """strings.json and translations/en.json carry identical exception messages."""
    assert _exceptions(TRANSLATION_FILES[0]) == _exceptions(TRANSLATION_FILES[1])
