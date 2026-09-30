"""Guards for the supported Python floor (3.8).

Function and module-level annotations are evaluated at import time, so
`X | None` or `list[int]` in them crash the package on Python 3.8/3.9.
"""

import ast
import pathlib

PACKAGE = pathlib.Path(__file__).resolve().parent.parent / "django_inspector"
_BUILTIN_GENERICS = {"list", "dict", "tuple", "set", "frozenset", "type"}


def _annotations(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            for arg in args.posonlyargs + args.args + args.kwonlyargs + [args.vararg, args.kwarg]:
                if arg is not None and arg.annotation is not None:
                    yield arg.annotation
            if node.returns is not None:
                yield node.returns
        elif isinstance(node, ast.AnnAssign):
            yield node.annotation


def _is_py310_only(annotation):
    for node in ast.walk(annotation):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            return True
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id in _BUILTIN_GENERICS
        ):
            return True
    return False


def test_annotations_are_importable_on_python_38():
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for annotation in _annotations(tree):
            if _is_py310_only(annotation):
                offenders.append("%s:%d" % (path.relative_to(PACKAGE.parent), annotation.lineno))
    assert offenders == []
