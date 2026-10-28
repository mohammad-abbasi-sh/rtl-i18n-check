# rtl-i18n-check

[🇮🇷 توضیحات فارسی](README-fa.md)

**The RTL layer your i18n checker is missing.** An Agent Skill +
deterministic checker for fa/ar/ur/he locale files that catches the
rendering bugs generic i18n checkers cannot see — bidi isolation,
ZWNJ loss, Arabic/Persian letter-variant mixing, mixed digit
scripts, missing CLDR plural forms, bidi control characters, and
translated or dropped placeholders.

## The problem

Agents translate whole locale files in one pass, the build passes,
the JSON is valid — and the shipped strings render backwards, lose
numbers, or read as machine Arabic. In March 2026 one team shipped
a $2.4M bug this way: "The Arabic version of the app was rendering
gibberish. Greetings backwards. Numbers in wrong positions. CI was
green. Staging looked fine. The English QA team signed off."

The gap, in the same article's words: "Every {variable}, every
number, every price. left-to-right content embedded in right-to-left
Arabic without bidi isolation markers. … No translation engine adds
these invisible Unicode markers. That's the gap."

## Quick start

```bash
# Check a Persian locale file (language detected from the file name)
python3 skills/rtl-i18n-check/scripts/rtl_i18n_check.py locales/fa.json

# Check against the source locale — placeholder sets must match per key
python3 skills/rtl-i18n-check/scripts/rtl_i18n_check.py locales/fa.json --source locales/en.json

# Fix mechanically (writes locales/fa.fixed.json, never overwrites)
python3 skills/rtl-i18n-check/scripts/rtl_i18n_check.py locales/fa.json --fix --source locales/en.json

# Re-check the fixed file until it exits 0
python3 skills/rtl-i18n-check/scripts/rtl_i18n_check.py locales/fa.fixed.json --source locales/en.json
```

Example output (against the test fixture in `tests/fixtures/`):

```
[FAIL] RTL-01 tests/fixtures/fa-broken.json :: greeting: Unisolated placeholder '{userName}' inside RTL text — wrap it in FSI…PDI (U+2068…U+2069) or <bdi>, or it renders in the wrong place.
       position 5–15
[FAIL] RTL-02 tests/fixtures/fa-broken.json :: verb: «می …» uses a space; the Persian verb prefix joins with ZWNJ: «می‌» + verb.
       من می خواهم کتاب بخوانم
[FAIL] RTL-03 tests/fixtures/fa-broken.json :: variant: Wrong-script letter variant(s): U+0643 ('ك') → 'ک'; U+064A ('ي') → 'ی'. A word indexed with one variant is not found by a query using the other.
       اين كتاب جديد است
[FAIL] RTL-05 tests/fixtures/fa-broken.json :: items_few: ICU plural for fa is missing plural form(s): one (needs 2: one, other).
       present: other
[FAIL] RTL-07 tests/fixtures/fa-broken.json :: placeholder_name: Placeholder 'نام' is not an ASCII identifier — a translator translated the placeholder name itself. Placeholder names must stay exactly as in the source.
       سلام {نام}، چطوری؟

1 file(s), 19 key(s): 15 error(s), 1 warning(s).
```

Exit codes: `0` = no errors · `1` = errors found · `2` = usage
error or an unreadable/unparseable file. Warnings do not fail the
check unless `--strict` is passed.

## What it checks

| Check | What fails |
|---|---|
| RTL-01 bidi isolation | LTR island (Latin name, URL, email, `{placeholder}`, `$5`) inside RTL text without FSI…PDI or `<bdi>` |
| RTL-02 ZWNJ | fa/ur: `می شود` / `کتاب ها` with a space instead of ZWNJ |
| RTL-03 letter variants | Arabic ي/ك/ة in a Persian file (or Persian ی/ک/گ/چ/پ/ژ in an Arabic file) |
| RTL-04 digit scripts | Two digit scripts in one string; a string using the file's minority script (warning, or error with `--digits`) |
| RTL-05 plurals | Missing CLDR forms (Arabic needs six, Persian/Urdu two); Arabic zero/one/two forms carrying a count placeholder |
| RTL-06 bidi controls | Override characters (RLO/LRO/…) or unpaired FSI/LRI/RLI/PDI isolates |
| RTL-07 placeholders | Placeholder names that are not ASCII identifiers; with `--source`, placeholder sets differing from the source locale |

Formats: JSON (incl. nested i18next), ARB (Flutter), Android
`strings.xml` (incl. `<plurals>`), gettext PO (incl. plural forms),
YAML (flat/nested mappings subset). Pure digit runs between RTL
words are deliberately *not* flagged — per the Unicode bidi
algorithm they order correctly; only digits touching Latin text or
a currency symbol misrender. The full reasoning for every rule is
in `skills/rtl-i18n-check/references/rtl-rules.md`.

## The contract

1. **Check first, never guess.** The tool measures the RTL layer
   deterministically — codepoint tables and the bidi algorithm,
   no model, no network.
2. **Fix mechanically, then review.** `--fix` applies only the
   safely mechanical fixes (RTL-01/02/03/04) to a new `.fixed`
   file. RTL-05/06/07 need a human — a missing Arabic plural form
   or a translated placeholder name is a judgement call.
3. **Prove the fix.** Re-run the check on the fixed file until it
   exits 0. Quote the final `0 errors` line.
4. **Know the limit.** This skill checks the RTL *text* layer of
   locale files. It does not check missing keys, untranslated
   English, or ICU syntax — pair it with StringLane / Locale
   Guard / Shipi18n for those. It does not check RTL *layout*
   (CSS logical properties, mirrored icons).

## How this differs from existing i18n checkers

StringLane, Shipi18n, Locale Guard and KAERIS all check missing
keys, placeholders and plural counts — and none of them checks
bidi isolation, ZWNJ, letter variants or digit scripts (verified
on their own pages, October 2026). `rtl-text-tools` is a JS
library for processing RTL text, not a checker; `rtlify` teaches
RTL *layout* rules and never touches locale files; `globalize.now`
adds RTL markers while translating (paid SaaS), it is not a free
deterministic checker for files however produced. The March 2026
Medium story describes exactly this linter ("ScriptGuard") — no
shipped repo for it could be found as of 2026-10-28.

## Install

- **Claude Code / Claude.ai:** copy `skills/rtl-i18n-check/` into
  your skills directory, or use the zip in `dist/` (extract it
  there).
- **Cursor:** copy `cursor/rtl-i18n-check.mdc` into
  `.cursor/rules/` (see `cursor/README.md`).
- **MCP:** `mcp-server/rtl_i18n_check_mcp_server.py` is a
  zero-dependency stdio MCP server exposing one tool,
  `check_rtl_locales` (see `mcp-server/`).

## Tests

```bash
python3 -m unittest discover -s tests
```

59 tests — per-check unit tests (isolation, ZWNJ, variants,
digits, controls, placeholders, plurals), loader tests for all
five formats, end-to-end fixture runs (a broken Persian file with
15 errors + 1 warning, a clean file with 0/0, a broken Arabic
file with 5 errors), the `--fix` round-trip, CLI exit codes, and
4 MCP server end-to-end tests — all passing, no network.

## Layout

```
skills/rtl-i18n-check/
  SKILL.md                     the skill: the contract
  scripts/rtl_i18n_check.py    the checker (stdlib only)
  references/rtl-rules.md      why each check exists + nearest projects
cursor/rtl-i18n-check.mdc      Cursor rule (condensed contract)
mcp-server/rtl_i18n_check_mcp_server.py  MCP server (one tool: check_rtl_locales)
tests/                         checker + MCP tests, real-bug fixtures
dist/rtl-i18n-check-skill.zip  packaged skill
```

## Roadmap

- `--fix` for Android XML and PO (v1 covers JSON/ARB only).
- YAML list values and block scalars in the YAML loader.
- Hebrew letter-variant table (currently isolation/plurals only).
- GitHub Action wrapper around the checker for per-PR comments.

## License

MIT — see `LICENSE`.
