
claude-opus-4-7: 5 cell(s) superseded and re-run from scratch. Claude Code continues a call that stops on max_tokens with an injected user turn ('Output token limit hit. Resume directly ...') and calls the model again; found 2026-11-12. The adapter now stops at the cap and scores what was written (bca/liveeval/subject.py, ClaudeSubject). Every cell with a continued attempt was superseded: the continuation changed that attempt and the repair feedback after it.
Cells: json_lint.hex.aarch64.s1, json_lint.hex.x86_64.s1, rpn.hex.aarch64.s1, rpn.hex.x86_64.s1, sort_lines.hex.x86_64.s1
