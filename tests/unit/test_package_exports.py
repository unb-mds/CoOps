"""Every name a package imports for re-export is listed in ``__all__``.

This exists because of a near-miss on #176. Resolving a merge conflict in
``coops/domain/ports/__init__.py`` — both branches had appended an import block
and its ``__all__`` entries — the resolution imported ``validate_repo_name`` but
left it out of ``__all__``.

The obvious check passed clean::

    import coops.domain.ports as p
    missing = [n for n in p.__all__ if not hasattr(p, n)]   # -> []

It passes because it only inspects names that are *already listed*. A dropped
export is invisible to it: the check confirms a property of what survived, not
of what was supposed to be there. That is the same shape as the row-count guard
that stayed green while 230 identities lost their names, and as #168's acceptance
that measured mapping while every record mapped to ``author=None``.

The check that does catch it runs the other direction: parse the module, collect
every name bound by an ``ImportFrom``, and diff against ``__all__``. Anything
imported and not exported is either a deliberate private re-export or a dropped
one, and the two are indistinguishable from outside — so the package declares its
intentional omissions in ``_PRIVATE_IMPORTS`` and the test holds the rest.
"""

from __future__ import annotations

import ast
import importlib
import pkgutil
from pathlib import Path

import pytest

import coops

#: Names a package imports for its own use rather than to re-export. Keep this
#: empty where possible; an entry is a claim that the omission is deliberate.
_PRIVATE_IMPORTS: dict[str, set[str]] = {}


def _packages_with_dunder_all() -> list[str]:
    found = []
    root = Path(coops.__file__).parent
    for module in pkgutil.walk_packages([str(root)], prefix="coops."):
        if not module.ispkg:
            continue
        init = Path(root.parent) / (module.name.replace(".", "/")) / "__init__.py"
        if not init.exists():
            continue
        tree = ast.parse(init.read_text(encoding="utf-8"))
        if any(
            isinstance(n, ast.Assign)
            and any(getattr(t, "id", None) == "__all__" for t in n.targets)
            for n in ast.walk(tree)
        ):
            found.append(module.name)
    return sorted(found)


_PACKAGES = _packages_with_dunder_all()


def test_the_scan_found_packages_to_check():
    """A control. An empty package list would make every test below vacuous —
    green, and measuring nothing."""
    assert _PACKAGES, "no packages with __all__ were discovered; the scan is broken"
    assert "coops.domain.ports" in _PACKAGES, _PACKAGES


@pytest.mark.parametrize("package", _PACKAGES)
def test_every_imported_name_is_exported(package):
    init = Path(importlib.import_module(package).__file__)
    tree = ast.parse(init.read_text(encoding="utf-8"))

    imported: set[str] = set()
    exported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level:  # relative only
            imported |= {a.asname or a.name for a in node.names if a.name != "*"}
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == "__all__" for t in node.targets
        ):
            exported = set(ast.literal_eval(node.value))

    dropped = imported - exported - _PRIVATE_IMPORTS.get(package, set())
    assert not dropped, (
        f"{package} imports {sorted(dropped)} but does not list them in __all__. "
        "Either export them or declare the omission in _PRIVATE_IMPORTS."
    )


@pytest.mark.parametrize("package", _PACKAGES)
def test_every_exported_name_resolves(package):
    """The weaker direction, kept for completeness — it cannot catch a dropped
    export, but it does catch an ``__all__`` entry that names nothing."""
    module = importlib.import_module(package)
    missing = [n for n in module.__all__ if not hasattr(module, n)]
    assert not missing, f"{package}.__all__ names unresolvable: {missing}"
