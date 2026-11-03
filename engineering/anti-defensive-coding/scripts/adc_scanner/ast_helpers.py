"""Scope-bounded AST, binding, and control-flow helpers."""

import ast
import builtins
import io
from itertools import pairwise
import re
import tokenize

from .registry import PY09_IO_CALLS, PY09_LOCK_TOKENS, evidence_text


CLASS_DEFINITION = re.compile(rb"^[ \t]*class[ \t]+(\w+)", re.MULTILINE)


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
        return
    attr = absent_self_attribute(node.test)
    if attr is None:
        return
    for statement in node.body:
        for write in local_walk(statement):
            if (
                not isinstance(write, (ast.Assign, ast.AnnAssign))
                or write.value is None
            ):
                continue
            for target in write_targets(write):
                for attribute in bound_targets(target):
                    if self_attribute(attribute) and attribute.attr == attr:
                        yield attribute, write.value


def class_declarations(classes):
    attributes = {}
    for cls in classes:
        for statement in lexical_nodes(cls.body):
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
            for method in lexical_nodes(cls.body)
            if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        helpers = {
            node.func.attr
            for initializer in methods
            if initializer.name == "__init__"
            for node in lexical_nodes(initializer.body)
            if isinstance(node, ast.Call) and self_attribute(node.func)
        }
        for method in methods:
            if method.name not in {"__init__", *helpers}:
                continue
            # Lazy presence-guarded writes do not establish a construction invariant.
            lazy_targets = {
                attribute
                for node in lexical_nodes(method.body)
                for attribute, _ in guarded_self_writes(node)
            }
            for statement in method.body:
                for target in assignments(statement):
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
    yield node
    if not isinstance(
        node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
    ):
        for child in ast.iter_child_nodes(node):
            yield from local_walk(child)


def lexical_nodes(body):
    """Located statement and expression nodes under body, in source order."""
    return sorted(
        (
            child
            for statement in body
            for child in local_walk(statement)
            if isinstance(child, (ast.stmt, ast.expr))
        ),
        key=lambda child: (child.lineno, child.col_offset),
    )


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
        return ast.unparse(call.func)
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
        for child in ast.walk(node)
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
    return isinstance(
        node,
        (
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
        ),
    ) or any(
        isinstance(child, (ast.Yield, ast.YieldFrom)) for child in local_walk(node)
    )


def binding_names(node):
    if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
        return {node.id}
    if isinstance(
        node,
        (
            ast.FunctionDef,
            ast.AsyncFunctionDef,
            ast.ClassDef,
            ast.ExceptHandler,
            ast.MatchAs,
            ast.MatchStar,
        ),
    ):
        return {node.name} if node.name is not None else set()
    if isinstance(node, ast.MatchMapping):
        return {node.rest} if node.rest is not None else set()
    if isinstance(node, ast.alias):
        return {node.asname or node.name.split(".")[0]}
    return set()


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
            target=ast.unparse(target),
            value=ast.unparse(node.value),
            line=node.lineno,
        )
        for node in lexical_nodes(function.body)
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


def adjacent_statements(nodes):
    """Adjacent statement pairs of every statement list owned by same-scope nodes."""
    for node in nodes:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for _, body in ast.iter_fields(node):
            if (
                isinstance(body, list)
                and body
                and all(isinstance(item, ast.stmt) for item in body)
            ):
                yield from pairwise(body)


def scope_bindings(function, nodes):
    """Names local to function: parameters and stores among its same-scope nodes."""
    # Comprehension targets bind in the comprehension's own scope.
    comprehension_names = {
        name
        for node in nodes
        if isinstance(node, ast.comprehension)
        for name in ast.walk(node.target)
    }
    external = {
        name
        for node in nodes
        if isinstance(node, (ast.Global, ast.Nonlocal))
        for name in node.names
    }
    return {
        param.arg
        for param in [*parameters(function), function.args.vararg, function.args.kwarg]
        if param is not None
    }.union(
        *(binding_names(node) for node in nodes if node not in comprehension_names)
    ) - external


def same_class_stashes(tree):
    """Map `self._a = n` directly followed by `return n` to sibling-method readers."""
    stashes = {}
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        methods = [
            node
            for node in cls.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        scopes = {
            method: [
                child for statement in method.body for child in local_walk(statement)
            ]
            for method in methods
        }
        loads = {}
        for method, nodes in scopes.items():
            for node in nodes:
                if self_attribute(node) and isinstance(node.ctx, ast.Load):
                    loads.setdefault(node.attr, []).append((method, node))
        for method, nodes in scopes.items():
            local = scope_bindings(method, nodes)
            for statement, following in [
                *pairwise(method.body),
                *adjacent_statements(nodes),
            ]:
                if not (
                    isinstance(statement, (ast.Assign, ast.AnnAssign))
                    and isinstance(statement.value, ast.Name)
                    and statement.value.id in local
                    and isinstance(following, ast.Return)
                    and isinstance(following.value, ast.Name)
                    and following.value.id == statement.value.id
                ):
                    continue
                for target in write_targets(statement):
                    if not (self_attribute(target) and target.attr.startswith("_")):
                        continue
                    readers = sorted(
                        (
                            (other.name, load)
                            for other, load in loads.get(target.attr, [])
                            if other is not method
                        ),
                        key=lambda reader: (reader[1].lineno, reader[1].col_offset),
                    )
                    if readers:
                        stashes[statement] = (target.attr, following, readers)
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


class CandidateRecords(ast.NodeVisitor):
    def __init__(self, source):
        self.source = source
        self.symbols = []
        self.candidates = []
        self.function_depth = 0
        self.defenses = []

    def add(self, rule, node, evidence, verify, related=(), signature=None, scope=()):
        """`scope` line ranges make the candidate diff-eligible outside its `span`;
        `signature` is a tuple of display strings that `PY10a` pairs and prints.
        Code stays lossless for bundle evidence until final output truncation."""
        nodes = (node, *related)
        spans = [(item.lineno, item.end_lineno) for item in nodes]
        code = "; ".join(ast.get_source_segment(self.source, item) for item in nodes)
        self.candidates.append(
            {
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
        )
        if signature is not None and self.function_depth:
            self.defenses.append(((rule, signature), self.candidates[-1]))


def has_conflict(source):
    state = 0
    for line in source.splitlines():
        if state == 0 and line.startswith("<<<<<<< "):
            state = 1
        elif state == 1 and line == "=======":
            state = 2
        elif state == 2 and line.startswith(">>>>>>> "):
            return True
    return False


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
