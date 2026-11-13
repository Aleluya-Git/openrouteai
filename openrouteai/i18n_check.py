"""Comprobación de traducciones: toda cadena tr("...") del código tiene traducción y
los marcadores {…} coinciden. La usa autoprueba.py."""
from __future__ import annotations

import ast
import os
import re
import string
from typing import Dict, List, Set, Tuple

PKG = os.path.dirname(os.path.abspath(__file__))


def source_keys() -> Set[str]:
    keys = set()
    for root, _, files in os.walk(PKG):
        for f in files:
            if not f.endswith(".py") or f in ("translations.py",):
                continue
            tree = ast.parse(open(os.path.join(root, f), encoding="utf-8").read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "tr" and node.args:
                    a = node.args[0]
                    if isinstance(a, ast.Constant) and isinstance(a.value, str):
                        keys.add(a.value)
                if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == "METRIC_NAMES":
                    keys.update(v.value for v in node.value.values)
    return keys


def fields(s: str) -> Set[str]:
    return {f for _, f, _, _ in string.Formatter().parse(s) if f}


def check() -> Tuple[List[str], List[str]]:
    from .translations import ROWS, TRANSLATIONS
    keys = source_keys()
    missing = [k for k in sorted(keys) if any(k not in TRANSLATIONS[l] for l in TRANSLATIONS)]
    bad = []
    for row in ROWS:
        f0 = fields(row[0])
        for t in row[1:]:
            if fields(t) != f0:
                bad.append(f"{row[0]!r} -> {t!r}")
    return missing, bad
