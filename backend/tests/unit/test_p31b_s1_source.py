import pytest

from app.ingestion.language_adapters import analyze, language_for


@pytest.mark.parametrize(
    ("path", "body", "kind"),
    [
        ("a.go", "package a\nfunc Hello() {}", "function"),
        ("a.py", "def Hello(): pass", "function"),
        ("a.ts", "export interface Policy { enabled: boolean }", "interface"),
        ("a.tsx", "export const View = () => <div/>;", "function"),
        ("a.mjs", "export class Handler {}", "class"),
        ("a.yaml", "policy:\n  enabled: false", "config_key"),
        ("a.json", '{"policy": {"enabled": false}}', "config_key"),
        ("a.md", "# Planned\nNot implemented", "heading"),
        ("launch", "#!/bin/sh\nexit 0", "text_chunk"),
    ],
)
def test_locator(path, body, kind):
    status, syms, _, _ = analyze(body, language_for(path, body))
    assert status in {"parsed", "text_chunked"}
    assert any(
        s["kind"] == kind and 1 <= s["start_line"] <= s["end_line"] <= len(body.splitlines())
        for s in syms
    )


def test_limits_and_relations():
    assert analyze("a: !!python/object:x {}", "yaml")[0] == "unsupported_structure"
    assert analyze("a: &x [*x]", "yaml")[0] == "unsupported_structure"
    assert analyze("[" * 70 + "]" * 70, "json")[0] == "ast_output_limit"
    assert analyze("{", "json")[0] == "syntax_error"
    _, _, calls, entries = analyze(
        "import r from 'r';\nexport function boot(){r.get('/x', f)}", "typescript"
    )
    assert {e["type"] for e in entries} >= {"import", "export", "api"}
    assert calls[0]["owner"] is not None and calls[0]["line"] == 2
    blocks = analyze("line\n" * 130, "text")[1]
    assert [(b["start_line"], b["end_line"]) for b in blocks] == [(1, 64), (65, 128), (129, 130)]
