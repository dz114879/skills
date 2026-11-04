"""Test-smell probes and lexical patch windows."""

import ast
from dataclasses import dataclass, field

from .ast_helpers import (
    CandidateRecords,
    bound_targets,
    lexical_nodes,
    local_walk,
    name_path,
    parameters,
    simple_assignment,
    terminal_name,
    write_targets,
)
from .registry import PY10B_PATCH_METHODS, evidence_text, verify_text


def raw_text_origin(node, bindings):
    if isinstance(node, ast.Name):
        return bindings.get(node.id)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return ("path", (node,)) if node.value.endswith((".py", ".md")) else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return raw_text_origin(node.right, bindings) or raw_text_origin(
            node.left, bindings
        )
    if not isinstance(node, ast.Call):
        return None
    name = terminal_name(node.func)
    if name in {"Path", "open"}:
        for arg in node.args:
            origin = raw_text_origin(arg, bindings)
            if origin and origin[0] == "path":
                return "path", (*origin[1], node)
        if name == "Path":
            return "unknown-path", (node,)
    if isinstance(node.func, ast.Attribute):
        origin = raw_text_origin(node.func.value, bindings)
        if origin and origin[0] in {"path", "unknown-path"}:
            if name in {"read", "read_text"}:
                return ("text", (*origin[1], node)) if origin[0] == "path" else None
            for arg in node.args:
                suffix = raw_text_origin(arg, bindings)
                if suffix and suffix[0] == "path":
                    return "path", (*origin[1], *suffix[1], node)
            return origin
    return None


def patch_symbol(node):
    path = name_path(node.func)
    index = None
    if path in PY10B_PATCH_METHODS["target"]:
        index = 0
    elif path in PY10B_PATCH_METHODS["object"]:
        index = 1
    elif path == "monkeypatch.setattr":
        index = 0 if len(node.args) == 2 else 1
    if index is not None and len(node.args) > index:
        target = node.args[index]
        if isinstance(target, ast.Constant) and isinstance(target.value, str):
            return target.value.rsplit(".", 1)[-1]
    return None


def is_patch_constructor(node):
    return (
        isinstance(node, ast.Call)
        and bool(patch_symbol(node))
        and name_path(node.func) != "monkeypatch.setattr"
    )


@dataclass
class PatchWindow:
    patch: ast.Call
    start: tuple[int, int]
    end: tuple[int, int]
    activation: ast.Call | None = None


def patch_windows(node, nodes):
    """Lexical patch regions; resets affect only statement-installed patches."""
    end = (node.end_lineno, node.end_col_offset)
    windows = [
        PatchWindow(decorator, (node.lineno, node.col_offset), end)
        for decorator in node.decorator_list
        if is_patch_constructor(decorator)
    ]
    active = []
    patchers = {}
    for child in nodes:
        if isinstance(child, (ast.With, ast.AsyncWith)):
            for item in child.items:
                patch = item.context_expr
                if is_patch_constructor(patch):
                    windows.append(
                        PatchWindow(
                            patch,
                            (child.body[0].lineno, child.body[0].col_offset),
                            (child.end_lineno, child.end_col_offset),
                        )
                    )
        if isinstance(child, ast.Call):
            call = child
        elif isinstance(child, (ast.Expr, ast.Assign, ast.AnnAssign)):
            call = child.value
        else:
            call = None
        if isinstance(call, ast.Call):
            path = name_path(call.func)
            method = terminal_name(call.func)
            patcher = None
            if isinstance(call.func, ast.Attribute):
                receiver = call.func.value
                patcher = (
                    receiver
                    if isinstance(receiver, ast.Call)
                    else patchers.get(name_path(receiver))
                )
            stopall = path in PY10B_PATCH_METHODS["stop_all"]
            for window in active[:]:
                monkeypatch = name_path(window.patch.func) == "monkeypatch.setattr"
                if (
                    (stopall and not monkeypatch)
                    or (path == "monkeypatch.undo" and monkeypatch)
                    or (method == "stop" and window.patch is patcher)
                ) and window.start <= (call.lineno, call.col_offset):
                    window.end = (call.lineno, call.col_offset)
                    active.remove(window)
            patch = (
                call
                if path == "monkeypatch.setattr"
                else (patcher if method == "start" else None)
            )
            if (
                not isinstance(child, ast.Call)
                and isinstance(patch, ast.Call)
                and patch_symbol(patch)
            ):
                window = PatchWindow(
                    patch, (call.end_lineno, call.end_col_offset), end, call
                )
                windows.append(window)
                active.append(window)
        for target in write_targets(child):
            for bound in bound_targets(target):
                if isinstance(bound, ast.Name):
                    patchers.pop(bound.id, None)
        assignment = simple_assignment(child)
        if assignment:
            name, value = assignment
            if is_patch_constructor(value):
                patchers[name] = value
    return windows


@dataclass
class GetterReturn:
    method: str
    binding: ast.stmt
    initial: ast.Call
    mutations: list[ast.AST] = field(default_factory=list)


class TestProbes(CandidateRecords):
    def __init__(self, source, tree):
        super().__init__(source)
        self.module_bindings = {}
        for statement in lexical_nodes(tree.body):
            self.bind_raw_text(statement, self.module_bindings)

    def smell(self, node, evidence, related=(), *, variant_key):
        self.add(
            "PY10b_test_smell",
            node,
            evidence,
            verify_text(variant_key),
            related=related,
        )

    def bind_raw_text(self, node, bindings):
        assignment = simple_assignment(node)
        if assignment:
            name, value = assignment
            origin = raw_text_origin(value, bindings)
            if origin:
                bindings[name] = origin[0], (*origin[1], node)
            else:
                bindings.pop(name, None)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if isinstance(item.optional_vars, ast.Name):
                    origin = raw_text_origin(item.context_expr, bindings)
                    if origin:
                        bindings[item.optional_vars.id] = origin
                    else:
                        bindings.pop(item.optional_vars.id, None)

    def visit_ClassDef(self, node):
        self.symbols.append(node.name)
        self.generic_visit(node)
        self.symbols.pop()

    def visit_Attribute(self, node):
        if node.attr.startswith("_legacy"):
            self.smell(
                node,
                evidence_text("PY10b_legacy", attribute=node.attr),
                variant_key="PY10b:legacy",
            )
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        self.symbols.append(node.name)
        nodes = lexical_nodes(node.body)
        bindings = self.module_bindings.copy()
        for param in parameters(node):
            bindings.pop(param.arg, None)
        for child in nodes:
            self.bind_raw_text(child, bindings)
            expressions = []
            if isinstance(child, ast.Assert):
                expressions = [child.test]
            elif isinstance(child, ast.Call) and name_path(child.func).startswith(
                "self.assert"
            ):
                expressions = child.args
                if (
                    terminal_name(child.func) in {"assertIn", "assertNotIn"}
                    and len(child.args) >= 2
                ):
                    self.raw_assertion(child, child.args[1], bindings)
            for expression in expressions:
                for check in local_walk(expression):
                    if isinstance(check, ast.Compare):
                        for op, right in zip(check.ops, check.comparators, strict=True):
                            if isinstance(op, (ast.In, ast.NotIn)):
                                self.raw_assertion(check, right, bindings)
                    elif (
                        isinstance(check, ast.Call)
                        and isinstance(check.func, ast.Attribute)
                        and check.func.attr in {"find", "startswith"}
                    ):
                        self.raw_assertion(check, check.func.value, bindings)
        self.getter_mutations(nodes)
        calls = [child for child in nodes if isinstance(child, ast.Call)]
        windows = patch_windows(node, nodes)
        for window in windows:
            symbol = patch_symbol(window.patch)
            invoked = next(
                (
                    call
                    for call in calls
                    if terminal_name(call.func) == symbol
                    and window.start <= (call.lineno, call.col_offset) < window.end
                ),
                None,
            )
            if invoked:
                site = window.activation or window.patch
                related = (invoked,)
                if window.patch not in ast.walk(site):
                    related = (window.patch, invoked)
                self.smell(
                    site,
                    evidence_text("PY10b_self_patch", symbol=symbol),
                    related,
                    variant_key="PY10b:patch",
                )
        self.generic_visit(node)
        self.symbols.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def raw_assertion(self, node, operand, bindings):
        origin = raw_text_origin(operand, bindings)
        if origin and origin[0] == "text":
            self.smell(
                node,
                evidence_text("PY10b_raw_text"),
                tuple(dict.fromkeys(origin[1])),
                variant_key="PY10b:raw-text",
            )

    def getter_mutations(self, nodes):
        getters = {}
        for node in nodes:
            assignment = simple_assignment(node)
            if assignment:
                name, value = assignment
                for child in local_walk(value):
                    if isinstance(child, ast.Call):
                        self.getter_recall(child, getters)
                getters.pop(name, None)
                if isinstance(value, ast.Call) and isinstance(
                    value.func, ast.Attribute
                ):
                    getters[name] = GetterReturn(name_path(value.func), node, value)
            for target in write_targets(node):
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and target.value.id in getters
                ):
                    getters[target.value.id].mutations.append(node)
            if not isinstance(node, ast.Call):
                continue
            if (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in getters
                and node.func.attr in {"append", "extend", "pop", "clear"}
            ):
                getters[node.func.value.id].mutations.append(node)
            self.getter_recall(node, getters)

    def getter_recall(self, node, getters):
        for name, getter in getters.items():
            if (
                node is not getter.initial
                and getter.mutations
                and name_path(node.func) == getter.method
            ):
                self.smell(
                    getter.binding,
                    evidence_text(
                        "PY10b_getter_mutation", method=getter.method, name=name
                    ),
                    (*getter.mutations, node),
                    variant_key="PY10b:mutation",
                )
                getter.mutations.clear()
