"""Scope-bounded AST, binding, and control-flow helpers."""

import ast
import builtins
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import cached_property, lru_cache
import gc
import io
from itertools import pairwise
import re
import threading
import tokenize
import weakref

from .registry import PY09_IO_CALLS, PY09_LOCK_TOKENS, evidence_text


CLASS_DEFINITION = re.compile(rb"^[ \t]*class[ \t]+(\w+)", re.MULTILINE)
SCOPE_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
PREFIX_BOUNDARY_TYPES = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.Try,
    ast.TryStar,
    ast.With,
    ast.AsyncWith,
    ast.Match,
    ast.Return,
    ast.Raise,
)
LEAF_TYPES = frozenset(
    {
        ast.Name,
        ast.Constant,
        ast.alias,
        ast.Global,
        ast.Nonlocal,
        ast.Pass,
        ast.Break,
        ast.Continue,
    }
)
BINDING_TYPES = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.ExceptHandler,
    ast.MatchAs,
    ast.MatchStar,
)
MODULE_SCOPE = object()
PROBE_TYPES = frozenset(
    {
        ast.Compare,
        ast.BoolOp,
        ast.ClassDef,
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.ListComp,
        ast.Call,
        ast.With,
        ast.AsyncWith,
        ast.Try,
        ast.TryStar,
    }
)
STATE_TYPES = frozenset({ast.Assign, ast.AnnAssign, ast.Dict, ast.Try, ast.TryStar})
_PARALLEL_ACTIVE = 0


@contextmanager
def allocation_batch():
    """Retained ASTs are acyclic; collect young temporaries once per scan batch."""
    enabled = (
        threading.current_thread() is threading.main_thread()
        and _PARALLEL_ACTIVE == 0
        and gc.isenabled()
    )
    if enabled:
        gc.disable()
    try:
        yield
    finally:
        if enabled:
            gc.collect(0)
            gc.enable()


def parallel_map(fn, items, jobs):
    """Map in input order; only the orchestration thread owns pool/GC state."""
    # GC is process-global; the strict lint profile runs without PLW enabled.
    global _PARALLEL_ACTIVE  # noqa: PLW0603, RUF100
    if jobs <= 1 or len(items) <= 1:
        return [fn(item) for item in items]
    enabled = gc.isenabled()
    _PARALLEL_ACTIVE += 1
    if not enabled:
        gc.enable()
    try:
        with ThreadPoolExecutor(max_workers=min(jobs, len(items))) as executor:
            return list(executor.map(fn, items))
    finally:
        _PARALLEL_ACTIVE -= 1
        if not enabled:
            gc.disable()


def ast_field_parts():
    ignored = {
        "ctx",
        "op",
        "ops",
        "id",
        "attr",
        "name",
        "arg",
        "asname",
        "module",
        "level",
        "is_async",
        "simple",
        "conversion",
        "kind",
        "type_comment",
        "rest",
        "kwd_attrs",
        "type_ignores",
        "str",
    }
    lists = {
        "body",
        "orelse",
        "finalbody",
        "handlers",
        "decorator_list",
        "bases",
        "keywords",
        "targets",
        "items",
        "cases",
        "values",
        "elts",
        "generators",
        "comparators",
        "args",
        "posonlyargs",
        "kwonlyargs",
        "defaults",
        "names",
        "ifs",
        "patterns",
        "kwd_patterns",
        "keys",
        "kw_defaults",
        "type_params",
        "argtypes",
    }
    parts = {}
    pending = [ast.AST]
    while pending:
        cls = pending.pop()
        pending.extend(cls.__subclasses__())
        fields = []
        if cls not in LEAF_TYPES and cls is not ast.MatchSingleton:
            for name in cls._fields:
                if name in ignored and not (
                    name == "name" and cls.__name__ == "TypeAlias"
                ):
                    continue
                scalar = (name == "args" and cls in SCOPE_TYPES) or (
                    name in {"body", "orelse"} and cls in {ast.IfExp, ast.Lambda}
                )
                mode = (
                    "scalar"
                    if scalar or name not in lists
                    else "nullable"
                    if (cls is ast.Dict and name == "keys")
                    or (cls is ast.arguments and name == "kw_defaults")
                    else "body"
                    if name in {"body", "orelse", "finalbody"}
                    else "list"
                )
                fields.append((name, mode))
        parts[cls] = tuple(fields)
    return parts


TYPE_PARTS = ast_field_parts()


def unparse(node):
    """Cache formatting on immutable parsed nodes for the lifetime of their tree."""
    cache = vars(node)
    if "_adc_unparse" not in cache:
        cache["_adc_unparse"] = ast.unparse(node)
    return cache["_adc_unparse"]


def node_parts(node):
    """Use the AST schema without allocating permanent per-node child caches."""
    children, bodies = [], []
    fields = vars(node)
    for name, mode in TYPE_PARTS[type(node)]:
        value = fields.get(name)
        if mode == "scalar":
            if value is not None:
                children.append(value)
        elif mode == "nullable":
            children.extend(child for child in value if child is not None)
        else:
            children.extend(value)
            if mode == "body" and value:
                bodies.append(value)
    return children, bodies


def walk(node):
    pending = deque([node])
    while pending:
        current = pending.popleft()
        yield current
        pending.extend(node_parts(current)[0])


class IndexedVisitor(ast.NodeVisitor):
    def __init__(self):
        self.visitors = {}

    def visit(self, node):
        kind = type(node)
        if kind not in self.visitors:
            self.visitors[kind] = getattr(
                type(self), "visit_" + kind.__name__, type(self).generic_visit
            )
        return self.visitors[kind](self, node)

    def generic_visit(self, node):
        fields = vars(node)
        for name, mode in TYPE_PARTS[type(node)]:
            value = fields.get(name)
            if mode == "scalar":
                if value is not None:
                    self.visit(value)
            elif mode == "nullable":
                for child in value:
                    if child is not None:
                        self.visit(child)
            else:
                for child in value:
                    self.visit(child)


def assignments(node):
    for child in local_walk(node):
        if isinstance(child, ast.AnnAssign) and child.value is not None:
            yield child.target
        elif isinstance(child, ast.Assign):
            yield from child.targets


def bound_targets(target):
    if isinstance(target, (ast.Tuple, ast.List)):
        for element in target.elts:
            yield from bound_targets(element)
    elif isinstance(target, ast.Starred):
        yield from bound_targets(target.value)
    else:
        yield target


def absent_self_attribute(test):
    if not isinstance(test, ast.UnaryOp) or not isinstance(test.op, ast.Not):
        return None
    call = test.operand
    if (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "hasattr"
        and len(call.args) == 2
        and isinstance(call.args[0], ast.Name)
        and call.args[0].id == "self"
        and isinstance(call.args[1], ast.Constant)
        and isinstance(call.args[1].value, str)
    ):
        return call.args[1].value
    return None


def guarded_self_writes(node):
    if not isinstance(node, ast.If):
        return ()
    cache = vars(node)
    if "_adc_guarded_writes" in cache:
        return cache["_adc_guarded_writes"]
    attr = absent_self_attribute(node.test)
    writes = []
    if attr is not None:
        for statement in node.body:
            for write in local_walk(statement):
                if (
                    not isinstance(write, (ast.Assign, ast.AnnAssign))
                    or write.value is None
                ):
                    continue
                for target in write_targets(write):
                    writes.extend(
                        (attribute, write.value)
                        for attribute in bound_targets(target)
                        if self_attribute(attribute) and attribute.attr == attr
                    )
    cache["_adc_guarded_writes"] = writes
    return writes


def class_declarations(classes):
    attributes = {}
    for cls in classes:
        nodes = lexical_nodes(cls.body)
        for statement in nodes:
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                targets = (
                    [statement.target]
                    if isinstance(statement, ast.AnnAssign)
                    else statement.targets
                )
                for target in targets:
                    for name in bound_targets(target):
                        if isinstance(name, ast.Name):
                            attributes.setdefault((cls, name.id), set()).add(
                                statement.lineno
                            )
        methods = [
            method
            for method in nodes
            if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        helpers = {
            node.func.attr
            for initializer in methods
            if initializer.name == "__init__"
            for node in scope_index(initializer).nodes
            if isinstance(node, ast.Call) and self_attribute(node.func)
        }
        for method in methods:
            if method.name not in {"__init__", *helpers}:
                continue
            # Lazy presence-guarded writes do not establish a construction invariant.
            lazy_targets = {
                attribute
                for node in scope_index(method).nodes
                for attribute, _ in guarded_self_writes(node)
            }
            for statement in scope_index(method).assignments:
                for target in write_targets(statement):
                    for attribute in bound_targets(target):
                        if self_attribute(attribute) and attribute not in lazy_targets:
                            attributes.setdefault((cls, attribute.attr), set()).add(
                                attribute.lineno
                            )
    return attributes


def named_class(node, classes):
    name = node.id if isinstance(node, ast.Name) else None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        name = node.value
    matches = classes.get(name, [])
    return matches[0] if len(matches) == 1 else None


def undeclared_attribute(cls, attr, classes, attributes):
    pending = [cls]
    seen = set()
    while pending:
        owner = pending.pop()
        if owner in seen:
            continue
        seen.add(owner)
        if (owner, attr) in attributes:
            return False
        for base in owner.bases:
            if isinstance(base, ast.Name) and base.id == "object":
                continue
            parent = named_class(base, classes)
            if parent is None:
                return False
            pending.append(parent)
    return True


def zero_arg_constructor(node, classes):
    if not isinstance(node, ast.Call) or node.args or node.keywords:
        return False
    name = terminal_name(node.func)
    return name is not None and (
        name[:1].isupper()
        or (
            isinstance(node.func, ast.Name)
            and isinstance(vars(builtins).get(name), type)
        )
        or name in {"cls", "__new__", "__class__"}
        or named_class(node.func, classes) is not None
    )


def local_walk(node):
    pending = [node]
    while pending:
        current = pending.pop()
        yield current
        if not isinstance(current, SCOPE_TYPES):
            pending.extend(reversed(node_parts(current)[0]))


def lexical_nodes(body):
    """Located statement and expression nodes under body, in source order."""
    nodes = (child for statement in body for child in local_walk(statement))
    return sorted(
        (child for child in nodes if isinstance(child, (ast.stmt, ast.expr))),
        key=lambda child: (child.lineno, child.col_offset),
    )


class ScopeIndex:
    """Scope-local traversal, source-ordered writes, and binding ownership."""

    def __init__(self, scope):
        self.body = [] if isinstance(scope, ast.Lambda) else scope.body
        self.args = (
            scope.args
            if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
            else None
        )
        cache = vars(scope)
        if "_adc_bindings" in cache:
            self.bindings = cache["_adc_bindings"]

    @cached_property
    def nodes(self):
        return [child for statement in self.body for child in local_walk(statement)]

    @cached_property
    def bindings(self):
        bindings = {}
        for child in self.nodes:
            for name in binding_names(child):
                bindings[name] = bindings.get(name, 0) + 1
        return bindings

    @cached_property
    def assignments(self):
        return [
            child
            for child in self.nodes
            if isinstance(child, (ast.Assign, ast.AnnAssign))
        ]

    @cached_property
    def parameters(self):
        if self.args is None:
            return {}
        return {
            param.arg: param
            for param in (
                *self.args.posonlyargs,
                *self.args.args,
                *self.args.kwonlyargs,
                self.args.vararg,
                self.args.kwarg,
            )
            if param is not None
        }

    @cached_property
    def lexical(self):
        return sorted(
            (child for child in self.nodes if isinstance(child, (ast.stmt, ast.expr))),
            key=lambda child: (child.lineno, child.col_offset),
        )

    @cached_property
    def writes(self):
        writes = {}
        for statement in self.lexical:
            targets = (
                [statement.target]
                if isinstance(statement, ast.AnnAssign)
                else write_targets(statement)
            )
            names = {
                name.id
                for target in targets
                for name in bound_targets(target)
                if isinstance(name, ast.Name)
            }
            for name in names:
                writes.setdefault(name, []).append(statement)
        return writes

    @cached_property
    def locals(self):
        return scope_bindings(self.parameters, self.nodes)

    @cached_property
    def reads(self):
        reads = {}
        for child in self.nodes:
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
                reads.setdefault(child.id, []).append(child)
            elif isinstance(child, SCOPE_TYPES):
                for loaded in load_names(child):
                    reads.setdefault(loaded.id, []).append(loaded)
        return reads


def scope_index(node):
    cache = vars(node)
    if "_adc_scope_index" not in cache:
        cache["_adc_scope_index"] = ScopeIndex(node)
    return cache["_adc_scope_index"]


@dataclass
class ReferenceCounts:
    calls: list[ast.Call] = field(default_factory=list)
    non_calls: int = 0


class TreeIndex:
    """One acyclic index shared by local probes and snapshot reachability."""

    def __init__(self, tree):
        self.root = weakref.ref(tree)
        self.parents = {}
        self.by_type = {}
        self.probe_children = {}
        self.following = {}
        self.owners = {}
        self.local_nodes = {}
        self.discards = {}
        self.json_hooks = set()
        self.call_funcs = {}
        self.scopes = [MODULE_SCOPE]
        self.scope_parents = {}
        self.scope_bindings = {MODULE_SCOPE: {}}
        self.scope_imports = {}
        self.methods = {}
        self.definitions = []
        self.function_parents = {}
        self.recursive_calls = {}
        self.delegations = set()
        self.name_references = {}
        self.attribute_references = {}
        self.name_uses = {}
        self.module_definitions = {}
        self.module_imports = {}
        self.module_exports = set()
        export_groups = {}
        alias_names = set()
        context = (MODULE_SCOPE, None, ())
        pending = [(tree, MODULE_SCOPE, None, MODULE_SCOPE, context, None)]
        while pending:
            node, parent, local, owner, context, top = pending.pop()
            kind = type(node)
            is_module = isinstance(node, ast.Module)
            if not is_module:
                self.parents[node] = parent
                if kind not in LEAF_TYPES:
                    self.by_type.setdefault(kind, []).append(node)
            if kind in STATE_TYPES:
                self.local_nodes.setdefault(local, []).append(node)
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(
                node.value, ast.Name
            ):
                assignment = simple_assignment(node)
                if assignment is not None and assignment[0] == "_":
                    self.discards.setdefault(local, []).append(node)
            if local is not None:
                bindings = self.scope_bindings[local]
                for name in binding_names(node):
                    bindings[name] = bindings.get(name, 0) + 1
                if isinstance(node, ast.ImportFrom):
                    self.scope_imports.setdefault(local, []).append(node)
                    alias_names.update(
                        alias.asname or alias.name for alias in node.names
                    )
            if local is MODULE_SCOPE:
                if isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                ):
                    self.module_definitions.setdefault(node.name, []).append(node)
                elif isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        if alias.name != "*":
                            self.module_imports.setdefault(
                                alias.asname or alias.name, []
                            ).append((node, alias.name))
                if isinstance(node, ast.Name) and node.id == "__all__":
                    export_groups.setdefault(top, [False, set()])[0] = True
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    export_groups.setdefault(top, [False, set()])[1].add(node.value)
            if isinstance(node, ast.Call):
                self.call_funcs[node.func] = node
                if (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "loads"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "json"
                    and any(
                        keyword.arg == "object_pairs_hook" for keyword in node.keywords
                    )
                ):
                    self.json_hooks.add(node)
            if context is not None and (
                isinstance(node, ast.Attribute)
                or (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load))
            ):
                self.record_reference(node, context)
            children, bodies = node_parts(node)
            for body in bodies:
                self.following.update(pairwise(body))
            if not is_module and (kind in PROBE_TYPES or bodies):
                self.owners[node] = local
                self.probe_children.setdefault(owner, []).append(node)
                owner = node
            if is_module:
                pending.extend(
                    (child, MODULE_SCOPE, MODULE_SCOPE, owner, context, child)
                    for child in reversed(children)
                )
            elif isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                scope, enclosing, symbols = context
                symbols = (*symbols, node.name)
                self.scopes.append(node)
                self.scope_parents[node] = scope
                self.scope_bindings[node] = {}
                if isinstance(node, ast.ClassDef):
                    self.methods.update(
                        (method, node)
                        for method in node.body
                        if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
                    )
                else:
                    self.definitions.append((node, ".".join(symbols)))
                    self.function_parents[node] = enclosing
                    enclosing = node
                nested = (node, enclosing, symbols)
                body = set(node.body)
                # CrossReferences historically skips PEP 695 type-parameter fields.
                type_params = set(vars(node).get("type_params", ()))
                pending.extend(
                    (
                        child,
                        node,
                        node if child in body else None,
                        owner,
                        nested
                        if child in body
                        else None
                        if child in type_params
                        else context,
                        None,
                    )
                    for child in reversed(children)
                )
            else:
                if isinstance(node, ast.Lambda):
                    local, top = None, None
                pending.extend(
                    (child, node, local, owner, context, top)
                    for child in reversed(children)
                )
        self.name_uses = {
            name: uses for name, uses in self.name_uses.items() if name in alias_names
        }
        for exported, strings in export_groups.values():
            if exported:
                self.module_exports.update(strings)
        for scope, bindings in self.scope_bindings.items():
            cache = vars(tree if scope is MODULE_SCOPE else scope)
            cache["_adc_bindings"] = bindings

    def parent(self, node):
        if isinstance(node, ast.Module):
            return None
        parent = self.parents[node]
        return self.root() if parent is MODULE_SCOPE else parent

    def ancestors(self, node):
        parent = self.parent(node)
        while parent is not None:
            yield parent
            parent = self.parent(parent)

    def record_reference(self, node, context):
        is_name = isinstance(node, ast.Name)
        name = node.id if is_name else node.attr
        owner = context[1]
        while owner is not None and owner.name != name:
            owner = self.function_parents[owner]
        call = self.call_funcs.get(node)
        receiver = None if is_name else node.value
        recursive = owner is not None and (
            is_name
            or (isinstance(receiver, ast.Name) and receiver.id in {"self", "cls"})
        )
        if recursive:
            if call is not None and (not is_name) == (owner in self.methods):
                self.recursive_calls.setdefault(owner, []).append(call)
            return
        if (
            owner is not None
            and call is not None
            and isinstance(receiver, ast.Call)
            and isinstance(receiver.func, ast.Name)
            and receiver.func.id == "super"
        ):
            self.delegations.add(call)
        references = self.name_references if is_name else self.attribute_references
        if name not in references:
            references[name] = ReferenceCounts()
        refs = references[name]
        if call is None:
            refs.non_calls += 1
        else:
            refs.calls.append(call)
        if is_name:
            self.name_uses.setdefault(name, []).append((node, context[0]))


def tree_index(tree):
    cache = vars(tree)
    if "_adc_tree_index" not in cache:
        cache["_adc_tree_index"] = TreeIndex(tree)
    return cache["_adc_tree_index"]


@lru_cache(maxsize=16)
def source_lines(source):
    return [*source.encode().splitlines(keepends=True), b""]


def terminal_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        return terminal_name(node.value)
    return None


def terminal_names(node):
    return {
        terminal_name(item)
        for item in (node.elts if isinstance(node, ast.Tuple) else [node])
    }


def irrefutable(pattern):
    if isinstance(pattern, ast.MatchAs):
        return pattern.pattern is None or irrefutable(pattern.pattern)
    if isinstance(pattern, ast.MatchOr):
        return any(irrefutable(child) for child in pattern.patterns)
    return False


def exits(body):
    """Explicit control-flow exits; only fallthrough reaches the next statement."""
    result = {"next"}
    for statement in body:
        if "next" not in result:
            break
        result = (result - {"next"}) | statement_exits(statement)
    return result


def statement_exits(node):
    if isinstance(node, ast.Raise):
        return {"bare" if node.exc is None else "raise"}
    if isinstance(node, (ast.Return, ast.Break, ast.Continue)):
        return {type(node).__name__.lower()}
    if isinstance(node, ast.If):
        return exits(node.body) | exits(node.orelse)
    if isinstance(node, (ast.Try, ast.TryStar)):
        pending = exits(node.body)
        if "next" in pending:
            pending = (pending - {"next"}) | exits(node.orelse)
        for handler in node.handlers:
            pending |= exits(handler.body)
        final = exits(node.finalbody)
        return (final - {"next"}) | (pending if "next" in final else set())
    if isinstance(node, (ast.With, ast.AsyncWith)):
        result = exits(node.body)
        # An unknown context manager can suppress a raise via __exit__.
        if result & {"bare", "raise"}:
            result.add("next")
        return result
    if isinstance(node, ast.Match):
        result = set()
        for case in node.cases:
            result |= exits(case.body)
            if case.guard is None and irrefutable(case.pattern):
                break
        else:
            result.add("next")
        return result
    if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
        # Loops do not prove a raise; preserve possible early returns as well.
        return {"next"} | (
            (exits(node.body) | exits(node.orelse)) - {"break", "continue"}
        )
    return {"next"}


def handler_stratum(handler):
    statements = [
        stmt
        for stmt in handler.body
        if not (
            isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Constant)
            and isinstance(stmt.value.value, str)
        )
    ]
    if not statements or all(
        isinstance(stmt, (ast.Pass, ast.Continue, ast.Break))
        or (
            isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Constant)
            and stmt.value.value is Ellipsis
        )
        for stmt in statements
    ):
        return evidence_text("PY02_silent_pass")
    returns = [
        node
        for stmt in handler.body
        for node in local_walk(stmt)
        if isinstance(node, ast.Return)
    ]
    if returns:
        values = list(
            dict.fromkeys(
                unparse(ret.value) if ret.value is not None else "None"
                for ret in returns
            )
        )
        value = ", ".join(values)
        if len(value) > 80:
            value = value[:77] + "..."
        return evidence_text("PY02_returns_fallback", value=value)
    for stmt in handler.body:
        for node in local_walk(stmt):
            if isinstance(node, ast.Call):
                exc_info_kw = next(
                    (kw.value for kw in node.keywords if kw.arg == "exc_info"),
                    None,
                )
                exc_info_disabled = isinstance(
                    exc_info_kw, ast.Constant
                ) and exc_info_kw.value in (False, None, 0)
                func = unparse(node.func)
                if not exc_info_disabled and (
                    func.endswith(
                        (
                            ".exception",
                            "print_exc",
                            "format_exc",
                            "print_exception",
                            "format_exception",
                        )
                    )
                    or exc_info_kw is not None
                ):
                    return evidence_text("PY02_fallthrough_exc_info")
    return evidence_text("PY02_fallthrough_no_tb")


def is_none(node):
    return isinstance(node, ast.Constant) and node.value is None


def none_check(node):
    if (
        isinstance(node, ast.Compare)
        and isinstance(node.left, ast.Name)
        and len(node.ops) == 1
        and isinstance(node.ops[0], (ast.Is, ast.IsNot))
        and is_none(node.comparators[0])
    ):
        return node.left.id
    return None


def literal_truth(node):
    if isinstance(node, ast.Constant) and node.value is not None:
        return bool(node.value)
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)) and not any(
        isinstance(item, ast.Starred) for item in node.elts
    ):
        return bool(node.elts)
    if isinstance(node, ast.Dict) and all(key is not None for key in node.keys):
        return bool(node.keys)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"dict", "list", "set"}
        and not node.args
        and not node.keywords
    ):
        return False
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, (ast.UAdd, ast.USub))
        and isinstance(node.operand, ast.Constant)
        and isinstance(node.operand.value, (int, float, complex))
    ):
        return bool(node.operand.value)
    return None


def simple_assignment(node):
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        target = node.targets[0]
    elif isinstance(node, ast.AnnAssign) and node.value is not None:
        target = node.target
    else:
        return None
    if isinstance(target, ast.Name):
        return target.id, node.value
    return None


def statement_neighbors(node, parent):
    body = next(
        value
        for _, value in ast.iter_fields(parent)
        if isinstance(value, list) and node in value
    )
    index = body.index(node)
    return body[:index], body[index + 1 :]


def saved_exception(handler):
    assignment = simple_assignment(handler.body[-1])
    if (
        assignment is not None
        and isinstance(assignment[1], ast.Name)
        and assignment[1].id == handler.name
    ):
        return assignment[0]
    return None


def exception_save_targets(handlers, slot):
    return {
        target
        for handler in handlers
        if saved_exception(handler) == slot
        for target in write_targets(handler.body[-1])
    }


def raises_name(node, name):
    return (
        isinstance(node, ast.Raise)
        and isinstance(node.exc, ast.Name)
        and node.exc.id == name
    )


def saved_retry(node, handler, parents):
    slot = saved_exception(handler)
    if slot is None:
        return False
    loop = parents[-1]
    if (
        not isinstance(loop, (ast.For, ast.While))
        or loop.orelse
        or (isinstance(loop, ast.While) and literal_truth(loop.test))
        or exits(handler.body) & {"return", "break", "continue"}
        or any(isinstance(child, ast.Break) for child in local_walk(loop))
    ):
        return False
    before, after = statement_neighbors(node, loop)
    if any("return" in statement_exits(statement) for statement in before + after):
        return False
    saves = exception_save_targets(node.handlers, slot)
    if any(
        slot in binding_names(child) and child not in saves
        for child in local_walk(loop)
    ):
        return False
    _, following = statement_neighbors(loop, parents[-2])
    if following and isinstance(following[0], ast.Assert):
        if any(slot in binding_names(child) for child in local_walk(following[0])):
            return False
        following = following[1:]
    return bool(following) and raises_name(following[0], slot)


def final_attempt(test, name, count):
    if not (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == name
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Eq)
    ):
        return False
    value = test.comparators[0]
    return (isinstance(value, ast.Constant) and value.value == count - 1) or (
        isinstance(value, ast.BinOp)
        and isinstance(value.op, ast.Sub)
        and isinstance(value.left, ast.Constant)
        and value.left.value == count
        and isinstance(value.right, ast.Constant)
        and value.right.value == 1
    )


def bounded_retry(node, handler, parent):
    if not (
        isinstance(parent, ast.For)
        and parent.body == [node]
        and isinstance(parent.target, ast.Name)
        and isinstance(parent.iter, ast.Call)
        and isinstance(parent.iter.func, ast.Name)
        and parent.iter.func.id == "range"
        and len(parent.iter.args) == 1
        and not parent.iter.keywords
    ):
        return False
    count = parent.iter.args[0]
    if not (
        isinstance(count, ast.Constant) and type(count.value) is int and count.value > 0
    ):
        return False
    success = exits(node.body)
    if "return" not in success or not success <= {"return", "bare", "raise"}:
        return False
    if any(parent.target.id in binding_names(child) for child in local_walk(node)):
        return False
    guard = handler.body[-1]
    return (
        isinstance(guard, ast.If)
        and final_attempt(guard.test, parent.target.id, count.value)
        and exits(guard.body) == {"bare"}
        and not guard.orelse
        and not exits(handler.body) & {"return", "break", "continue"}
    )


def stored_primary_cleanup(node, handler, parents):
    guard = handler.body[-1]
    if not (
        isinstance(guard, ast.If)
        and none_check(guard.test) is not None
        and isinstance(guard.test.ops[0], ast.Is)
        and exits(guard.body) == {"bare"}
        and not guard.orelse
        and not exits(handler.body) & {"return", "break", "continue"}
    ):
        return False
    slot = none_check(guard.test)
    primary = parents[-1]
    if not (
        isinstance(primary, ast.Try)
        and node in primary.finalbody
        and not statement_exits(primary) & {"return", "break", "continue"}
    ):
        return False
    before, after = statement_neighbors(primary, parents[-2])
    if not before or not after:
        return False
    initial = simple_assignment(before[-1])
    propagate = after[0]
    if not (
        initial is not None
        and initial[0] == slot
        and is_none(initial[1])
        and isinstance(propagate, ast.If)
        and none_check(propagate.test) == slot
        and isinstance(propagate.test.ops[0], ast.IsNot)
        and len(propagate.body) == 1
        and raises_name(propagate.body[0], slot)
        and not propagate.orelse
    ):
        return False
    saves = exception_save_targets(primary.handlers, slot)
    return bool(saves) and all(
        child in saves for child in local_walk(primary) if slot in binding_names(child)
    )


def last_noreturn_call(body, helpers):
    last = body[-1]
    if not isinstance(last, ast.Expr) or "next" not in exits(body[:-1]):
        return None
    call = last.value.value if isinstance(last.value, ast.Await) else last.value
    if not isinstance(call, ast.Call) or terminal_name(call.func) not in helpers:
        return None
    if isinstance(call.func, ast.Name) or (
        isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id in {"self", "cls"}
    ):
        return unparse(call.func)
    return None


def get_call(node):
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
    )


def parameters(node):
    return node.args.posonlyargs + node.args.args + node.args.kwonlyargs


def load_names(node):
    return [
        child
        for child in walk(node)
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
    ]


def parameter_defaults(node):
    positional = node.args.posonlyargs + node.args.args
    values = [None] * (len(positional) - len(node.args.defaults)) + node.args.defaults
    return {
        param.arg: value
        for param, value in zip(
            parameters(node), values + node.args.kw_defaults, strict=True
        )
    }


def prefix_boundary(node):
    return isinstance(node, PREFIX_BOUNDARY_TYPES) or any(
        isinstance(child, (ast.Yield, ast.YieldFrom)) for child in local_walk(node)
    )


def binding_names(node):
    if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
        return (node.id,)
    if isinstance(node, BINDING_TYPES):
        return (node.name,) if node.name is not None else ()
    if isinstance(node, ast.MatchMapping):
        return (node.rest,) if node.rest is not None else ()
    if isinstance(node, ast.alias):
        return (node.asname or node.name.split(".")[0],)
    return ()


def import_bindings(nodes):
    return {
        name
        for node in nodes
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
        for name in binding_names(alias)
    }


def identifier_tokens(text):
    separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", text)
    return [token.lower() for token in re.split(r"[\W_]+", separated) if token]


def name_path(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{name_path(node.value)}.{node.attr}"
    return ""


def write_targets(node):
    if isinstance(node, ast.Assign):
        return node.targets
    if (
        isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr))
        and node.value is not None
    ):
        return [node.target]
    return []


def row_factory_facts(function):
    """`<x>.row_factory = <expr>` facts among function's own statements."""
    return [
        evidence_text(
            "PY08_row_factory",
            target=unparse(target),
            value=unparse(node.value),
            line=node.lineno,
        )
        for node in scope_index(function).lexical
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in write_targets(node)
        if isinstance(target, ast.Attribute) and target.attr == "row_factory"
    ]


def self_state_writes(body, *, reset):
    writes = {}
    for statement in body:
        for child in local_walk(statement):
            if not isinstance(child, (ast.Assign, ast.AnnAssign)):
                continue
            if child.value is None or is_none(child.value) != reset:
                continue
            for target in write_targets(child):
                if self_attribute(target):
                    writes.setdefault(target.attr, []).append(child)
    return writes


def self_attribute(node):
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    )


def scope_bindings(params, nodes):
    """Names local to function: parameters and stores among its same-scope nodes."""
    # Comprehension targets bind in the comprehension's own scope.
    comprehension_names = {
        name
        for node in nodes
        if isinstance(node, ast.comprehension)
        for name in walk(node.target)
    }
    external = {
        name
        for node in nodes
        if isinstance(node, (ast.Global, ast.Nonlocal))
        for name in node.names
    }
    return (
        set(params).union(
            *(binding_names(node) for node in nodes if node not in comprehension_names)
        )
        - external
    )


def same_class_stashes(classes, following_statements, local_nodes):
    """Map `self._a = n` directly followed by `return n` to sibling-method readers."""
    stashes = {}
    for cls in classes:
        methods = [
            node
            for node in cls.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        writers = []
        for method in methods:
            for statement in local_nodes.get(method, ()):
                following = following_statements.get(statement)
                if not (
                    isinstance(statement, (ast.Assign, ast.AnnAssign))
                    and isinstance(statement.value, ast.Name)
                    and isinstance(following, ast.Return)
                    and isinstance(following.value, ast.Name)
                    and following.value.id == statement.value.id
                    and statement.value.id in scope_index(method).locals
                ):
                    continue
                writers.extend(
                    (method, statement, following, target.attr)
                    for target in write_targets(statement)
                    if self_attribute(target) and target.attr.startswith("_")
                )
        if not writers:
            continue
        attributes = {attr for _, _, _, attr in writers}
        loads = {}
        for method in methods:
            for node in scope_index(method).nodes:
                if (
                    self_attribute(node)
                    and node.attr in attributes
                    and isinstance(node.ctx, ast.Load)
                ):
                    loads.setdefault(node.attr, []).append((method, node))
        for method, statement, following, attr in writers:
            readers = sorted(
                (
                    (other.name, load)
                    for other, load in loads.get(attr, ())
                    if other is not method
                ),
                key=lambda reader: (reader[1].lineno, reader[1].col_offset),
            )
            if readers:
                stashes[statement] = (attr, following, readers)
    return stashes


def context_tokens(node):
    while isinstance(node, ast.Subscript):
        node = node.value
    return set(
        identifier_tokens(name_path(node.func if isinstance(node, ast.Call) else node))
    )


def lock_context_parts(node):
    if not isinstance(node, (ast.With, ast.AsyncWith)):
        return
    for index, item in enumerate(node.items):
        if context_tokens(item.context_expr) & PY09_LOCK_TOKENS:
            yield (
                item.context_expr,
                [
                    *node.body,
                    *(later.context_expr for later in node.items[index + 1 :]),
                ],
            )


def io_or_hash_call(node):
    path = name_path(node.func)
    return (
        terminal_name(node.func) in PY09_IO_CALLS["terminal_names"]
        or path.startswith(PY09_IO_CALLS["prefixes"])
        or path.endswith(PY09_IO_CALLS["suffixes"])
        or path in PY09_IO_CALLS["paths"]
    )


def iteration_parts(node):
    if isinstance(node, (ast.For, ast.AsyncFor)):
        return [node.target, *node.body], [node.target, node.iter]
    if isinstance(node, ast.While):
        return [node.test, *node.body], [node.test]
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        repeated = (
            [node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]
        )
        headers = []
        for index, generator in enumerate(node.generators):
            repeated.extend([generator.target, *generator.ifs])
            if index:
                repeated.append(generator.iter)
            headers.extend([generator.target, generator.iter])
        return repeated, headers
    return [], []


def predicate_tests(node):
    if isinstance(node, (ast.If, ast.While, ast.IfExp, ast.Assert)):
        return [node.test]
    if isinstance(node, ast.comprehension):
        return node.ifs
    if isinstance(node, ast.match_case):
        return [node.guard] if node.guard is not None else []
    if (
        isinstance(node, (ast.Compare, ast.BoolOp))
        or (isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not))
        or (isinstance(node, ast.Call) and name_path(node.func) == "bool")
    ):
        return [node]
    return []


def lookup_signature(values):
    keys = []
    for value in values:
        if isinstance(value, ast.Attribute):
            keys.append(repr(value.attr))
        elif isinstance(value, ast.Subscript) and isinstance(value.slice, ast.Constant):
            keys.append(repr(value.slice.value))
        elif get_call(value) and value.args and isinstance(value.args[0], ast.Constant):
            keys.append(repr(value.args[0].value))
    return tuple(keys) or None


class CandidateRecords(IndexedVisitor):
    def __init__(self, source):
        super().__init__()
        self.source = source
        self.source_lines = source_lines(source)
        self.symbols = []
        self.candidates = []
        self.function_depth = 0
        self.defenses = []

    def visit_Module(self, node):
        with allocation_batch():
            self.generic_visit(node)

    def source_segment(self, node):
        start = node.lineno, node.col_offset
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0]
            return self.source_slice(start, (first.lineno, first.col_offset)).rstrip()
        segment = self.source_slice(start, (node.end_lineno, node.end_col_offset))
        if isinstance(node, ast.ExceptHandler) and len(" ".join(segment.split())) > 200:
            first = node.body[0]
            header = self.source_slice(start, (first.lineno, first.col_offset)).rstrip()
            segment = header + " " + self.source_segment(first)
            if len(node.body) > 1:
                segment += "; ...; " + self.source_segment(node.body[-1])
        return segment

    def source_slice(self, start, end):
        start_line, start_col = start
        end_line, end_col = end
        if start_line == end_line:
            return self.source_lines[start_line - 1][start_col:end_col].decode()
        return b"".join(
            [
                self.source_lines[start_line - 1][start_col:],
                *self.source_lines[start_line : end_line - 1],
                self.source_lines[end_line - 1][:end_col],
            ]
        ).decode()

    def add(
        self,
        rule,
        node,
        evidence,
        verify,
        related=(),
        signature=None,
        scope=(),
        reflect=None,
    ):
        """`scope` line ranges make the candidate diff-eligible outside its `span`;
        `signature` is a tuple of display strings that `PY10a` pairs and prints.
        Code stays lossless for bundle evidence until final output truncation."""
        nodes = (node, *related)
        spans = [(item.lineno, item.end_lineno) for item in nodes]
        code = "; ".join(self.source_segment(item) for item in nodes)
        record = {
            "rule": rule,
            "span": [
                min(start for start, _ in spans),
                max(end for _, end in spans),
            ],
            "symbol": ".".join(self.symbols) or "<module>",
            "code": " ".join(code.split()),
            "evidence": evidence,
            "verify": verify,
            "_scope": [*spans, *scope],
        }
        if reflect is not None:
            record["_reflect"] = reflect
        self.candidates.append(record)
        if signature is not None and self.function_depth:
            self.defenses.append(((rule, signature), record))


def has_conflict(source):
    if "<<<<<<< " not in source:
        return False
    state = 0
    for line in source.splitlines():
        if state == 0 and line.startswith("<<<<<<< "):
            state = 1
        elif state == 1 and line == "=======":
            state = 2
        elif state == 2 and line.startswith(">>>>>>> "):
            return True
    return False


@allocation_batch()
def parse_source(raw, path, errors, *, base=False, pattern=None):
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
        source = raw.decode(encoding)
        if pattern is not None and not pattern.search(source):
            return None
        if has_conflict(source):
            raise SyntaxError("Unresolved merge conflict markers")
        tree = ast.parse(source, filename=f"base:{path}" if base else path)
        return source, tree
    except (SyntaxError, UnicodeError, LookupError) as error:
        errors.add((path, ("base: " if base else "") + " ".join(str(error).split())))
        return None
