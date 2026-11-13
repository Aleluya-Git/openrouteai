"""Parser y serializador de S-expressions de KiCad.

Representación:
  - Una lista KiCad ``(head a b (c d))`` es una ``list`` de Python: ``['head', 'a', 'b', ['c', 'd']]``.
  - Los átomos sin comillas son ``str`` normales (se conserva el texto original,
    así los números no pierden precisión al reescribir el archivo).
  - Las cadenas entre comillas son ``QStr`` (subclase de ``str``) para volver a
    escribirlas con comillas.

El árbol completo se conserva, de modo que la exportación reescribe el archivo
original añadiendo/modificando solo lo necesario.
"""
from __future__ import annotations

from typing import Iterator, List, Optional, Union


class QStr(str):
    """Cadena que en el archivo original iba entre comillas."""
    __slots__ = ()


Node = Union[str, QStr, list]


class SExprError(ValueError):
    pass


def _tokenize(text: str) -> Iterator[Union[str, QStr]]:
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif c == "(" or c == ")":
            yield c
            i += 1
        elif c == '"':
            i += 1
            buf = []
            while i < n:
                ch = text[i]
                if ch == "\\" and i + 1 < n:
                    nxt = text[i + 1]
                    buf.append({"n": "\n", "t": "\t", "r": "\r"}.get(nxt, nxt))
                    i += 2
                    continue
                if ch == '"':
                    break
                buf.append(ch)
                i += 1
            else:
                raise SExprError("Cadena sin cerrar")
            i += 1
            yield QStr("".join(buf))
        else:
            j = i
            while j < n and text[j] not in " \t\r\n()\"":
                j += 1
            yield text[i:j]
            i = j


def parse(text: str) -> list:
    """Parsea un documento S-expr y devuelve la lista raíz."""
    stack: List[list] = []
    root: Optional[list] = None
    for tok in _tokenize(text):
        if tok == "(" and not isinstance(tok, QStr):
            new: list = []
            if stack:
                stack[-1].append(new)
            stack.append(new)
        elif tok == ")" and not isinstance(tok, QStr):
            if not stack:
                raise SExprError("Paréntesis ')' inesperado")
            done = stack.pop()
            if not stack:
                root = done
        else:
            if not stack:
                raise SExprError("Átomo fuera de lista")
            stack[-1].append(tok)
    if stack:
        raise SExprError("Faltan paréntesis de cierre")
    if root is None:
        raise SExprError("Documento vacío")
    return root


def parse_file(path: str) -> list:
    with open(path, "r", encoding="utf-8") as fh:
        return parse(fh.read())


# --------------------------------------------------------------------- escritura
def _atom(a) -> str:
    if isinstance(a, QStr):
        s = str(a).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{s}"'
    if isinstance(a, bool):
        return "yes" if a else "no"
    if isinstance(a, float):
        return fmt_num(a)
    if isinstance(a, int):
        return str(a)
    s = str(a)
    if s == "" or any(ch in s for ch in ' ()"\t\n'):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def fmt_num(v: float) -> str:
    """Formatea un número como lo hace KiCad (sin ceros sobrantes)."""
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    if s in ("-0", ""):
        s = "0"
    return s


def dumps(node: Node, indent: int = 0) -> str:
    if not isinstance(node, list):
        return _atom(node)
    if not node:
        return "()"
    has_sub = any(isinstance(c, list) for c in node[1:])
    if not has_sub:
        return "(" + " ".join(_atom(c) for c in node) + ")"
    pad = "\t" * (indent + 1)
    out = ["(" + _atom(node[0])]
    # Átomos iniciales en la misma línea
    k = 1
    while k < len(node) and not isinstance(node[k], list):
        out[0] += " " + _atom(node[k])
        k += 1
    simple_inline = node[0] in ("at", "xy", "pts", "start", "end", "mid", "size", "layers",
                                "font", "effects", "stroke", "drill", "net", "uuid")
    if simple_inline and len(node) < 40:
        return "(" + " ".join(dumps(c, 0) if isinstance(c, list) else _atom(c) for c in node) + ")"
    for c in node[k:]:
        out.append("\n" + pad + dumps(c, indent + 1))
    out.append("\n" + "\t" * indent + ")")
    return "".join(out)


def dump_file(node: Node, path: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(dumps(node))
        fh.write("\n")


# --------------------------------------------------------------------- consultas
def head(node: Node) -> Optional[str]:
    return node[0] if isinstance(node, list) and node else None


def find(node: list, name: str) -> Optional[list]:
    """Primer hijo directo con cabecera ``name``."""
    for c in node[1:]:
        if isinstance(c, list) and c and c[0] == name:
            return c
    return None


def find_all(node: list, name: str) -> List[list]:
    return [c for c in node[1:] if isinstance(c, list) and c and c[0] == name]


def value(node: list, name: str, default=None, idx: int = 1):
    sub = find(node, name)
    if sub is None or len(sub) <= idx:
        return default
    return sub[idx]


def fnum(x, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def walk(node: Node):
    """Recorre el árbol en profundidad (incluye el propio nodo)."""
    if isinstance(node, list):
        yield node
        for c in node[1:]:
            yield from walk(c)
