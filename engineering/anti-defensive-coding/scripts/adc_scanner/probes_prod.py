"""Production candidate probes for PY01-PY09 and default hooks."""

import ast
from functools import cached_property

from .ast_helpers import (
    MODULE_SCOPE,
    PREFIX_BOUNDARY_TYPES,
    SCOPE_TYPES,
    CandidateRecords,
    absent_self_attribute,
    allocation_batch,
    assignments,
    binding_names,
    bound_targets,
    bounded_retry,
    class_declarations,
    context_tokens,
    exits,
    get_call,
    guarded_self_writes,
    handler_stratum,
    identifier_tokens,
    import_bindings,
    io_or_hash_call,
    is_none,
    iteration_parts,
    last_noreturn_call,
    literal_truth,
    load_names,
    local_walk,
    lock_context_parts,
    lookup_signature,
    name_path,
    named_class,
    node_parts,
    none_check,
    parameter_defaults,
    parameters,
    predicate_tests,
    prefix_boundary,
    row_factory_facts,
    same_class_stashes,
    saved_retry,
    scope_index,
    self_state_writes,
    simple_assignment,
    stored_primary_cleanup,
    terminal_name,
    terminal_names,
    tree_index,
    undeclared_attribute,
    unparse,
    walk,
    write_targets,
    zero_arg_constructor,
)
from .registry import (
    PY07_DEPENDENCY_SUFFIXES,
    PY08_COPY_CALLS,
    PY08_ROUNDTRIP_PAIRS,
    PY09_TX_TOKENS,
    evidence_text,
    verify_text,
)


class CandidateProbes(CandidateRecords):
    @allocation_batch()
    def __init__(self, source, tree):
        super().__init__(source)
        self.index = tree_index(tree)
        index = self.index
        self.class_nodes = index.by_type.get(ast.ClassDef, ())
        self.tree = tree
        self.imported = import_bindings(
            [
                *index.by_type.get(ast.Import, ()),
                *index.by_type.get(ast.ImportFrom, ()),
            ]
        )
        self.stashes = same_class_stashes(
            self.class_nodes, index.following, index.local_nodes
        )
        self.lock_contexts = {}
        self.transaction_contexts = set()
        self.transaction_functions = set()
        returns = {}
        for kind in (ast.FunctionDef, ast.AsyncFunctionDef):
            for node in index.by_type.get(kind, ()):
                returns.setdefault(node.name, []).append(terminal_name(node.returns))
        contexts = sorted(
            [
                *index.by_type.get(ast.With, ()),
                *index.by_type.get(ast.AsyncWith, ()),
            ],
            key=lambda node: (node.lineno, node.col_offset),
        )
        for node in contexts:
            self.transaction_contexts.update(
                item.context_expr
                for item in node.items
                if context_tokens(item.context_expr) & PY09_TX_TOKENS["entry"]
            )
            if any(
                item.context_expr in self.transaction_contexts for item in node.items
            ):
                self.transaction_functions.add(index.owners[node])
            for context, parts in lock_context_parts(node):
                for part in parts:
                    for child in local_walk(part):
                        if isinstance(child, ast.expr):
                            self.lock_contexts.setdefault(child, []).append(context)
        self.noreturn_helpers = {
            name
            for name, annotations in returns.items()
            if all(annotation in {"NoReturn", "Never"} for annotation in annotations)
        }
        self.functions = set()

    @cached_property
    def classes(self):
        classes = {}
        for declarations in self.index.module_definitions.values():
            for cls in declarations:
                if isinstance(cls, ast.ClassDef):
                    classes.setdefault(cls.name, []).append(cls)
        return classes

    @cached_property
    def attributes(self):
        return class_declarations(self.class_nodes)

    def generic_visit(self, node):
        for body in node_parts(node)[1]:
            self.default_shadows(body)
        key = MODULE_SCOPE if isinstance(node, ast.Module) else node
        for child in self.index.probe_children.get(key, ()):
            self.visit(child)

    def default_shadows(self, body):
        if len(body) < 2 or not any(isinstance(guard, ast.If) for guard in body[1:]):
            return
        for index, statement in enumerate(body):
            assignment = simple_assignment(statement)
            if assignment is None:
                continue
            name, value = assignment
            via_get = get_call(value) and len(value.args) == 2 and not value.keywords
            if via_get:
                fallback = value.args[1]
            elif isinstance(value, ast.BoolOp) and isinstance(value.op, ast.Or):
                fallback = value.values[-1]
            else:
                continue
            truth = literal_truth(fallback)
            if truth is None:
                continue
            for guard in body[index + 1 : index + 6]:
                if isinstance(guard, ast.If):
                    test = guard.test
                    matches = none_check(test) == name and isinstance(
                        test.ops[0], ast.Is
                    )
                    matches |= (
                        not via_get
                        and truth
                        and isinstance(test, ast.UnaryOp)
                        and isinstance(test.op, ast.Not)
                        and isinstance(test.operand, ast.Name)
                        and test.operand.id == name
                    )
                    if matches and exits(guard.body) & {"raise", "bare", "return"}:
                        self.add(
                            "PY03_default_shadows_check",
                            statement,
                            evidence_text(
                                "PY03_default_shadows_check",
                                name=name,
                                default_line=statement.lineno,
                                guard_line=guard.lineno,
                            ),
                            verify_text("PY03:get" if via_get else "PY03"),
                            related=(guard.test, *guard.body),
                        )
                if any(name in binding_names(child) for child in local_walk(guard)):
                    break

    def visit_Compare(self, node):
        if (
            len(node.ops) == 1
            and isinstance(node.ops[0], (ast.IsNot, ast.NotEq))
            and any(
                isinstance(operand, (ast.Name, ast.Attribute))
                and terminal_name(operand).startswith("_DEFAULT_")
                for operand in (node.left, node.comparators[0])
            )
        ):
            self.add(
                "PY10b_test_smell",
                node,
                evidence_text("PY10b_default_hook"),
                verify_text("PY10b:hook"),
            )
        self.generic_visit(node)

    def visit_BoolOp(self, node):
        fallback = node.values[-1]
        truth = literal_truth(fallback)
        if (
            isinstance(node.op, ast.Or)
            and truth is not None
            and not (
                truth and isinstance(fallback, (ast.List, ast.Tuple, ast.Set, ast.Dict))
            )
            and any(
                isinstance(value, (ast.Subscript, ast.Attribute)) or get_call(value)
                for value in node.values[:-1]
            )
        ):
            verify = verify_text("PY04:truthy" if truth else "PY04:falsy")
            self.add(
                "PY04_lossy_falsy_coalesce",
                node,
                evidence_text(
                    "PY04_lossy_falsy_coalesce",
                    fallback=unparse(fallback),
                ),
                verify,
                signature=lookup_signature(node.values[:-1]),
            )
            self.candidates[-1]["_coalesce"] = (
                node.col_offset,
                node.end_col_offset,
                unparse(fallback),
            )
        self.generic_visit(node)

    def visit_ClassDef(self, node):
        self.symbols.append(node.name)
        self.generic_visit(node)
        self.symbols.pop()

    def visit_FunctionDef(self, node):
        self.symbols.append(node.name)
        self.functions.add(".".join(self.symbols))
        self.function_depth += 1
        self.dead_parameters(node)
        self.state_smuggling(node)
        self.transaction_checks(node)
        self.generic_visit(node)
        self.function_depth -= 1
        self.symbols.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def transaction_checks(self, node):
        if node not in self.transaction_functions:
            return
        nodes = scope_index(node).nodes
        contexts = [child for child in nodes if child in self.transaction_contexts]
        if not contexts:
            return
        checks = []
        covered = set()
        for child in nodes:
            for test in predicate_tests(child):
                if test in covered:
                    continue
                parts = list(local_walk(test))
                covered.update(parts)
                if any(
                    isinstance(part, ast.Attribute)
                    and part.attr == "in_transaction"
                    and isinstance(part.ctx, ast.Load)
                    for part in parts
                ):
                    checks.append(test)
        if checks:
            self.add(
                "PY09_io_or_tx_in_lock",
                checks[0],
                evidence_text(
                    "PY09_tx_check",
                    checks=", ".join(
                        evidence_text("PY09_check_site", line=check.lineno)
                        for check in checks
                    ),
                    contexts=", ".join(
                        evidence_text(
                            "PY09_tx_context",
                            context=unparse(context),
                            line=context.lineno,
                        )
                        for context in contexts
                    ),
                ),
                verify_text("PY09:tx-check"),
                related=(*checks[1:], *contexts),
            )

    def state_smuggling(self, node):
        nodes = self.index.local_nodes.get(node, ())
        for child in nodes:
            if child in self.stashes:
                attr, returned, readers = self.stashes[child]
                name = returned.value.id
                self.add(
                    "PY07_state_smuggling",
                    child,
                    evidence_text(
                        "PY07_return_stash",
                        attribute=attr,
                        name=name,
                        line=child.lineno,
                        readers=", ".join(
                            evidence_text(
                                "PY07_stash_reader", method=method, line=load.lineno
                            )
                            for method, load in readers
                        ),
                    ),
                    verify_text("PY07:stash"),
                    related=(returned, *(load for _, load in readers)),
                )
            if isinstance(child, ast.Dict):
                for key, value in zip(child.keys, child.values, strict=True):
                    self.dict_dependency(key, value)
            if isinstance(child, (ast.Try, ast.TryStar)) and child.finalbody:
                active = self_state_writes(child.body, reset=False)
                resets = self_state_writes(child.finalbody, reset=True)
                for attr in sorted(active.keys() & resets.keys()):
                    first, *related = [*active[attr], *resets[attr]]
                    self.add(
                        "PY07_state_smuggling",
                        first,
                        evidence_text(
                            "PY07_state_smuggling",
                            attribute=attr,
                            write_line=first.lineno,
                            try_line=child.lineno,
                            reset_line=resets[attr][0].lineno,
                        ),
                        verify_text("PY07:try"),
                        related=related,
                    )
            for assignment in write_targets(child):
                for target in bound_targets(assignment):
                    if isinstance(child, (ast.Assign, ast.AnnAssign)) and isinstance(
                        target, ast.Subscript
                    ):
                        self.dict_dependency(target.slice, child.value, child)

    def dict_dependency(self, key, value, assignment=None):
        if not (
            isinstance(key, ast.Constant)
            and isinstance(key.value, str)
            and isinstance(value, (ast.Name, ast.Attribute))
        ):
            return
        if identifier_tokens(terminal_name(value))[-1:] == ["name"]:
            return
        tokens = identifier_tokens(key.value)
        if not tokens or tokens[-1] not in PY07_DEPENDENCY_SUFFIXES:
            return
        self.add(
            "PY07_state_smuggling",
            assignment if assignment is not None else key,
            evidence_text(
                "PY07_dict_dependency",
                key=key.value,
                value=unparse(value),
                line=key.lineno,
            ),
            verify_text("PY07:dict"),
            related=() if assignment is not None else (value,),
        )

    def dead_parameters(self, node):
        params = {
            param.arg
            for param in [*parameters(node), node.args.vararg, node.args.kwarg]
            if param is not None and param.arg not in {"self", "cls"}
        }
        if not params:
            return
        for child in self.index.discards.get(node, ()):
            value = child.value
            if value.id in params and scope_index(node).reads.get(value.id) == [value]:
                self.dead_param_candidate(
                    value.id,
                    child,
                    evidence_text("PY05_discard"),
                )
        relevant = False
        for statement in node.body:
            if isinstance(statement, PREFIX_BOUNDARY_TYPES):
                relevant = (
                    isinstance(statement, ast.If)
                    and none_check(statement.test) in params
                )
                break
            if isinstance(statement, (ast.Assign, ast.AnnAssign)) and any(
                isinstance(target, ast.Name) and target.id in params
                for assignment in write_targets(statement)
                for target in bound_targets(assignment)
            ):
                relevant = True
                break
        if not relevant:
            return
        unseen = set(params)
        for statement in node.body:
            # The first control-flow statement may itself be the fallback guard.
            if isinstance(statement, ast.If):
                defaults = parameter_defaults(node)
                new = none_check(statement.test)
                if (
                    new in params
                    and new in defaults
                    and isinstance(statement.test.ops[0], ast.Is)
                    and is_none(defaults[new])
                ):
                    for child in statement.body:
                        assignment = simple_assignment(child)
                        if assignment is None or assignment[0] != new:
                            continue
                        rhs_reads = load_names(assignment[1])
                        for old in sorted(params - {new}):
                            reads = scope_index(node).reads.get(old, ())
                            if reads and all(read in rhs_reads for read in reads):
                                self.dead_param_candidate(
                                    old,
                                    statement.test,
                                    evidence_text(
                                        "PY05_dual_parameter",
                                        name=new,
                                        default_line=defaults[new].lineno,
                                        overwrite_line=child.lineno,
                                    ),
                                    related=(child,),
                                )
            if prefix_boundary(statement):
                break
            mentioned = {
                child.id for child in walk(statement) if isinstance(child, ast.Name)
            }
            for child in local_walk(statement):
                mentioned.update(binding_names(child))
            if (
                isinstance(statement, (ast.Assign, ast.AnnAssign))
                and statement.value is not None
            ):
                bound = {
                    target.id
                    for assignment in assignments(statement)
                    for target in bound_targets(assignment)
                    if isinstance(target, ast.Name)
                }
                rhs = {
                    child.id
                    for child in walk(statement.value)
                    if isinstance(child, ast.Name)
                }
                for name in sorted((unseen & bound) - rhs):
                    self.dead_param_candidate(
                        name, statement, evidence_text("PY05_entry_overwrite")
                    )
            unseen -= mentioned

    def dead_param_candidate(self, name, node, fact, related=()):
        self.add(
            "PY05_dead_param_evasion",
            node,
            evidence_text(
                "PY05_dead_param_evasion", name=name, fact=fact, line=node.lineno
            ),
            verify_text("PY05"),
            related=related,
        )

    def visit_ListComp(self, node):
        element = node.elt
        if (
            isinstance(element, ast.Call)
            and name_path(element.func) == "dict"
            and len(element.args) == 1
            and not element.keywords
            and isinstance(element.args[0], ast.Name)
        ):
            for generator in node.generators:
                if (
                    isinstance(generator.target, ast.Name)
                    and generator.target.id == element.args[0].id
                ):
                    name = generator.target.id
                    function = next(
                        (
                            scope
                            for scope in self.index.ancestors(node)
                            if isinstance(
                                scope, (ast.FunctionDef, ast.AsyncFunctionDef)
                            )
                        ),
                        None,
                    )
                    factory_facts = row_factory_facts(function) if function else []
                    self.add(
                        "PY08_roundtrip_and_copy",
                        element,
                        "; ".join(
                            [
                                evidence_text(
                                    "PY08_comprehension",
                                    comprehension_line=node.lineno,
                                    name=name,
                                    call_line=element.lineno,
                                ),
                                *factory_facts,
                            ]
                        ),
                        verify_text("PY08:comp"),
                        related=(generator.target,),
                    )
                    break
        self.generic_visit(node)

    def roundtrip(self, node):
        if len(node.args) != 1 or not isinstance(node.args[0], ast.Call):
            return
        inner = node.args[0]
        pair = name_path(node.func), name_path(inner.func)
        matches = pair in PY08_ROUNDTRIP_PAIRS
        matches |= (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "fromisoformat"
            and isinstance(inner.func, ast.Attribute)
            and inner.func.attr == "isoformat"
        )
        matches |= (
            pair == ("set", "list")
            and len(inner.args) == 1
            and isinstance(inner.args[0], ast.Call)
            and name_path(inner.args[0].func) == "set"
        )
        if matches:
            self.add(
                "PY08_roundtrip_and_copy",
                node,
                evidence_text(
                    "PY08_roundtrip_copy",
                    outer=unparse(node.func),
                    inner=unparse(inner.func),
                    line=node.lineno,
                ),
                verify_text("PY08:roundtrip"),
            )

    def lock_candidate(self, node, fact):
        contexts = self.lock_contexts[node]
        variant_key = (
            "PY09:tx"
            if any(
                context_tokens(context) & PY09_TX_TOKENS["protected"]
                for context in contexts
            )
            else "PY09:lock"
        )
        self.add(
            "PY09_io_or_tx_in_lock",
            node,
            evidence_text(
                "PY09_lock",
                fact=fact,
                line=node.lineno,
                contexts=", ".join(
                    evidence_text(
                        "PY09_lock_context",
                        context=unparse(context),
                        line=context.lineno,
                    )
                    for context in contexts
                ),
            ),
            verify_text(variant_key),
            related=tuple(contexts),
        )

    def visit_With(self, node):
        for item in node.items:
            context = item.context_expr
            if context in self.transaction_contexts and context in self.lock_contexts:
                self.lock_candidate(context, evidence_text("PY09_nested_tx_fact"))
        self.generic_visit(node)

    visit_AsyncWith = visit_With

    def receiver_binding(self, receiver):
        root = receiver
        while isinstance(root, (ast.Attribute, ast.Subscript)):
            root = root.value
        if not isinstance(root, ast.Name):
            return [], None
        for scope in self.index.ancestors(receiver):
            if isinstance(
                scope, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
            ) and any(
                isinstance(name, ast.Name) and name.id == root.id
                for generator in scope.generators
                for name in bound_targets(generator.target)
            ):
                break
            if not isinstance(
                scope, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
            ):
                continue
            index = scope_index(scope)
            for statement in reversed(index.writes.get(root.id, ())):
                if (statement.end_lineno, statement.end_col_offset) <= (
                    receiver.lineno,
                    receiver.col_offset,
                ):
                    annotation = (
                        statement.annotation
                        if isinstance(statement, ast.AnnAssign)
                        else None
                    )
                    return [
                        evidence_text(
                            "PY01_local_binding",
                            binding=unparse(statement),
                            line=statement.lineno,
                        )
                    ], annotation if receiver is root else None
            if not isinstance(scope, ast.Module):
                param = index.parameters.get(root.id)
                if param is not None:
                    annotation = (
                        unparse(param.annotation)
                        if param.annotation is not None
                        else evidence_text("PY01_unannotated")
                    )
                    return [
                        evidence_text(
                            "PY01_parameter",
                            name=param.arg,
                            annotation=annotation,
                            line=param.lineno,
                        )
                    ], param.annotation if receiver is root else None
            if root.id in index.bindings:
                break
        return [], None

    def reflection_evidence(self, receiver, cls, attr):
        facts = [evidence_text("PY01_receiver", receiver=unparse(receiver))]
        if isinstance(receiver, ast.Name) and receiver.id == "self" and cls is not None:
            facts.append(
                evidence_text("PY01_enclosing_class", name=cls.name, line=cls.lineno)
            )
        else:
            binding, annotation = self.receiver_binding(receiver)
            facts.extend(binding)
            cls = named_class(annotation, self.classes)
        if cls is not None:
            bases = ", ".join(unparse(base) for base in cls.bases) or evidence_text(
                "PY01_implicit_base"
            )
            facts.append(
                evidence_text("PY01_receiver_class", name=cls.name, bases=bases)
            )
            facts.extend(
                evidence_text("PY01_declared_attribute", attribute=attr, line=line)
                for line in sorted(self.attributes.get((cls, attr), []))
            )
        return "; ".join(facts)

    def memo_guard(self, node, cls, attr):
        if cls is None:
            return False
        test = self.index.parent(node)
        guard = self.index.parent(test)
        if not (
            isinstance(guard, ast.If)
            and absent_self_attribute(test) == attr
            and undeclared_attribute(cls, attr, self.classes, self.attributes)
        ):
            return False
        return any(
            not is_none(value)
            and literal_truth(value) is None
            and not zero_arg_constructor(value, self.classes)
            for _, value in guarded_self_writes(guard)
        )

    def iteration_contexts(self, node):
        contexts = []
        descendants = {node}
        for parent in self.index.ancestors(node):
            if isinstance(parent, SCOPE_TYPES):
                break
            repeated, headers = iteration_parts(parent)
            if any(part in descendants for part in repeated):
                contexts.append((parent, headers))
            descendants.add(parent)
        return list(reversed(contexts))

    def visit_Call(self, node):
        self.roundtrip(node)
        if (
            node in self.lock_contexts
            and node not in self.transaction_contexts
            and io_or_hash_call(node)
        ):
            self.lock_candidate(
                node,
                evidence_text("PY09_lock_io_fact", callable=unparse(node.func)),
            )
        contexts = (
            self.iteration_contexts(node)
            if name_path(node.func) in PY08_COPY_CALLS
            and (terminal_name(node.func) != "asdict" or len(node.args) == 1)
            else ()
        )
        if contexts:
            self.add(
                "PY08_roundtrip_and_copy",
                node,
                evidence_text(
                    "PY08_iteration_copy",
                    callable=unparse(node.func),
                    line=node.lineno,
                    iterations=", ".join(
                        evidence_text(
                            "PY08_iteration", kind=type(loop).__name__, line=loop.lineno
                        )
                        for loop, _ in contexts
                    ),
                ),
                verify_text("PY08:loop"),
                related=tuple(header for _, headers in contexts for header in headers),
            )
        if (
            isinstance(node.func, ast.Name)
            and (
                (node.func.id == "getattr" and len(node.args) >= 3)
                or (node.func.id == "hasattr" and len(node.args) == 2)
            )
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
            and not (
                isinstance(node.args[0], ast.Name) and node.args[0].id in self.imported
            )
        ):
            attr = node.args[1].value
            cls = next(
                (
                    parent
                    for parent in self.index.ancestors(node)
                    if isinstance(parent, ast.ClassDef)
                ),
                None,
            )
            if self.memo_guard(node, cls, attr):
                self.generic_visit(node)
                return
            self.add(
                "PY01_reflect_default",
                node,
                self.reflection_evidence(node.args[0], cls, attr),
                verify_text("PY01"),
                signature=(repr(attr),),
                reflect=(node.col_offset, node.end_col_offset, unparse(node)),
            )
        self.generic_visit(node)

    def visit_Try(self, node):
        internal = {
            "KeyError",
            "TypeError",
            "AttributeError",
            "IndexError",
            "ValueError",
            "LookupError",
        }
        hooks = (
            [
                child
                for statement in node.body
                for child in local_walk(statement)
                if child in self.index.json_hooks
            ]
            if self.index.json_hooks
            else []
        )
        parent = self.index.parent(node)
        parents = (self.index.parent(parent), parent)
        for handler in node.handlers:
            caught = handler.type
            names = terminal_names(caught)
            broad = caught is None or bool(names & {"Exception", "BaseException"})
            if not broad and len(names & internal) < 2 and not hooks:
                continue
            outcomes = exits(handler.body)
            facts = []
            if (
                (broad or len(names & internal) >= 2)
                and not outcomes <= {"bare", "raise"}
                and not (
                    not exits(node.finalbody) & {"return", "break", "continue"}
                    and (
                        saved_retry(node, handler, parents)
                        or bounded_retry(node, handler, parent)
                        or stored_primary_cleanup(node, handler, parents)
                    )
                )
            ):
                helper = last_noreturn_call(handler.body, self.noreturn_helpers)
                effective = (outcomes - {"next"}) | {"raise"} if helper else outcomes
                if not effective <= {"bare", "raise"}:
                    facts.append(handler_stratum(handler))
                if helper:
                    facts.append(evidence_text("PY02_noreturn", helper=helper))
            washes_hook = (
                hooks and (broad or "ValueError" in names) and outcomes != {"bare"}
            )
            if washes_hook:
                facts.append(
                    evidence_text(
                        "PY02_hook_diagnostic",
                        hooks=", ".join(
                            evidence_text("PY02_hook_site", line=hook.lineno)
                            for hook in hooks
                        ),
                    )
                )
            if facts:
                label = (
                    unparse(caught)
                    if caught is not None
                    else evidence_text("PY02_bare_except")
                )
                verify = (
                    verify_text("PY02:hook") if washes_hook else verify_text("PY02")
                )
                self.add(
                    "PY02_error_washing",
                    handler,
                    evidence_text(
                        "PY02_error_washing",
                        caught=label,
                        line=handler.lineno,
                        facts="; ".join(facts),
                    ),
                    verify,
                    signature=tuple(sorted(names - {None}))
                    if caught is not None
                    else ("bare except",),
                    # A rewritten try body widens what an unchanged handler swallows.
                    scope=[(node.body[0].lineno, node.body[-1].end_lineno)],
                )
        self.generic_visit(node)

    visit_TryStar = visit_Try
