import json

import yaml
from yaml.events import AliasEvent
from yaml.nodes import MappingNode, ScalarNode, SequenceNode


def anchor(kind, name, start, end):
    return dict(
        kind=kind,
        qualified_name=name[:500],
        signature=name[:240],
        start_line=start,
        end_line=max(start, end),
    )


def structured_analysis(body, language):
    symbols = []
    if language in {"yaml", "json"}:
        try:
            if language == "json":
                json.loads(body)
            depth = 0
            for count, event in enumerate(yaml.parse(body, Loader=yaml.BaseLoader)):
                if isinstance(event, AliasEvent) or getattr(event, "tag", None):
                    return "unsupported_structure", [], [], []
                depth += int(
                    event.__class__.__name__ in {"MappingStartEvent", "SequenceStartEvent"}
                )
                depth -= int(event.__class__.__name__ in {"MappingEndEvent", "SequenceEndEvent"})
                if depth > 64 or count > 20000:
                    return "ast_output_limit", [], [], []
            for doc in yaml.compose_all(body, Loader=yaml.BaseLoader):
                stack = [(doc, "$")]
                while stack:
                    node, path = stack.pop()
                    if isinstance(node, MappingNode):
                        for key, value in node.value:
                            if not isinstance(key, ScalarNode) or len(key.value) > 120:
                                return "unsupported_structure", [], [], []
                            field = path + "/" + key.value.replace("~", "~0").replace("/", "~1")
                            symbols.append(
                                anchor(
                                    "config_key",
                                    field,
                                    key.start_mark.line + 1,
                                    value.end_mark.line + bool(value.end_mark.column),
                                )
                            )
                            stack.append((value, field))
                    elif isinstance(node, SequenceNode):
                        stack.extend((v, f"{path}/{i}") for i, v in enumerate(node.value))
        except (ValueError, RecursionError, yaml.YAMLError):
            return "syntax_error", [], [], []
        return "parsed", symbols, [], []
    if language not in {
        "markdown",
        "text",
        "shell",
        "lua",
        "sql",
        "protobuf",
        "config",
        "dockerfile",
        "makefile",
        "ini",
        "xml",
        "toml",
    }:
        return "unsupported", [], [], []
    lines = body.splitlines()
    for i, line in enumerate(lines, 1):
        stripped = line.lstrip("#")
        heading = (
            language == "markdown"
            and 1 <= len(line) - len(stripped) <= 6
            and stripped.startswith(" ")
        )
        if not symbols or heading or i - symbols[-1]["start_line"] >= 64:
            symbols.append(
                anchor(
                    "heading" if heading else "text_chunk",
                    stripped.strip() if heading else f"lines:{i}",
                    i,
                    i,
                )
            )
        symbols[-1]["end_line"] = i
        if len(symbols) > 4096:
            return "ast_output_limit", [], [], []
    return "text_chunked", symbols, [], []
