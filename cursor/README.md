# Cursor — rtl-i18n-check rule

`cursor/rtl-i18n-check.mdc` is the Cursor version of this skill. It
carries the same contract in condensed form: check first with the
tool, fix mechanically into a `.fixed` file and review it, prove the
fix with a re-check until exit 0, and state the limits (RTL text
layer only — missing keys and untranslated English belong to the
generic checkers, RTL layout belongs to a layout tool).

## Install

Copy `cursor/rtl-i18n-check.mdc` into your project's
`.cursor/rules/` directory (create it if needed). Cursor applies it
automatically. The rule points at the checker script — copy the whole
`skills/rtl-i18n-check/` directory into your project, or install the
checker where the rule's commands can find it.

## What it covers

- The check-first contract (RTL-01 isolation, RTL-02 ZWNJ, RTL-03
  letter variants, RTL-04 digit scripts, RTL-05 plurals, RTL-06
  bidi controls, RTL-07 placeholders).
- The fix rules (mechanical fixes only, never overwrite the input,
  RTL-05/06/07 need a human).
- The proof rule (re-check the fixed file until exit 0).
- The limit (RTL text layer only).

The full skill with references and the MCP server lives in
`skills/rtl-i18n-check/` and `mcp-server/`.
