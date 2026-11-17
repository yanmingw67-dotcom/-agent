from functools import lru_cache
from pathlib import PurePosixPath

import tree_sitter_go
import tree_sitter_python
from tree_sitter import Language, Parser


@lru_cache
def parser(language):
    grammar = {"python": tree_sitter_python, "go": tree_sitter_go}[language]
    return Parser(Language(grammar.language()))


def language_for(path):
    return {
        ".py": "python",
        ".go": "go",
        ".js": "javascript",
        ".ts": "typescript",
        ".java": "java",
        ".rs": "rust",
        ".md": "markdown",
        ".txt": "text",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".json": "json",
    }.get(PurePosixPath(path).suffix, "unknown")


def analyze(text: str, language: str):
    if language not in {"python", "go"}:
        return "unsupported", [], [], []
    data = text.encode("utf-8")
    tree = parser(language).parse(data)
    symbols, calls, entries = [], [], []
    # 显式栈避免深层语法导致 Python 递归栈溢出；限定单文件内 AST 输出量。
    stack = [(tree.root_node, "", None)]
    while stack:
        node, prefix, owner = stack.pop()
        name_node = node.child_by_field_name("name")
        definition = node.type in {
            "function_definition",
            "class_definition",
            "function_declaration",
            "method_declaration",
            "type_spec",
            "method_elem",
        }
        next_prefix, next_owner = prefix, owner
        if definition and name_node:
            name = data[name_node.start_byte : name_node.end_byte].decode()
            kind = "function"
            if node.type == "class_definition":
                kind = "class"
            elif node.type in {"method_declaration", "method_elem"} or (
                prefix and language == "python"
            ):
                kind = "method"
            elif node.type == "type_spec":
                type_node = node.child_by_field_name("type")
                kind = "interface" if type_node and type_node.type == "interface_type" else "type"
            receiver = node.child_by_field_name("receiver")
            qualifier = prefix
            if receiver:
                qualifier = (
                    data[receiver.start_byte : receiver.end_byte]
                    .decode()
                    .strip("()")
                    .split()[-1]
                    .lstrip("*")
                )
            qualified = ".".join(filter(None, [qualifier, name]))
            start, end = (
                node.start_point.row + 1,
                node.end_point.row + (1 if node.end_point.column else 0),
            )
            signature = (
                data[node.start_byte : min(node.end_byte, node.start_byte + 500)]
                .decode(errors="replace")
                .splitlines()[0]
            )
            index = len(symbols)
            symbols.append(
                dict(
                    kind=kind,
                    qualified_name=qualified[:500],
                    signature=signature,
                    start_line=start,
                    end_line=max(start, end),
                )
            )
            next_prefix, next_owner = qualified, index
            if name == "main":
                entries.append(dict(type="process", line=start, basis="AST main declaration"))
            if name.startswith(("handle_", "on_")):
                entries.append(
                    dict(type="event", line=start, basis="name heuristic; wiring unverified")
                )
        if node.type in {"call", "call_expression"}:
            fn = node.child_by_field_name("function")
            if fn:
                name = data[fn.start_byte : fn.end_byte].decode()
                calls.append(
                    dict(owner=owner, target_name=name[:500], line=node.start_point.row + 1)
                )
                if name.endswith((".route", ".get", ".post", ".HandleFunc", ".Handle")):
                    entries.append(
                        dict(
                            type="api",
                            line=node.start_point.row + 1,
                            basis="AST registration call; runtime wiring unverified",
                        )
                    )
        if len(symbols) + len(calls) + len(entries) > 20000:
            return "ast_output_limit", [], [], []
        for child in reversed(node.named_children):
            stack.append((child, next_prefix, next_owner))
    return ("syntax_error" if tree.root_node.has_error else "parsed"), symbols, calls, entries
