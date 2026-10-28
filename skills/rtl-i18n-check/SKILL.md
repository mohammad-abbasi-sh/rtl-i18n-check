---
name: rtl-i18n-check
description: Check fa/ar/ur/he locale files for the RTL bugs generic i18n checkers cannot see — unisolated LTR islands, ZWNJ loss, Arabic/Persian letter-variant mixing, mixed digit scripts, missing CLDR plural forms, bidi control characters, translated or dropped placeholders. Use when an agent translates locale files, when fa/ar/ur strings render backwards or lose numbers, when a PR touches .json/.arb/.xml/.po/.yaml locale files, or before shipping any Arabic/Persian/Urdu UI. Also use to verify a fix before reporting the locale as fixed.
---

# RTL locale-file QA — the layer your i18n checker is missing

Agents translate whole locale files in one pass, the build passes,
the JSON is valid — and the shipped strings render backwards, lose
numbers, or read as machine Arabic. In March 2026 one team shipped a
$2.4M bug this way: "The Arabic version of the app was rendering
gibberish. Greetings backwards. Numbers in wrong positions. CI was
green. Staging looked fine. The English QA team signed off."

Generic i18n checkers (StringLane, Shipi18n, Locale Guard, KAERIS)
check missing keys, placeholders and plural counts — none of them
checks bidi isolation, ZWNJ, letter variants or digit scripts. This
skill is that layer. It complements them; it does not replace them.

## The contract

1. **Check first, with the tool — never guess.** Run the bundled
   checker (zero-dependency, Python stdlib only):

   ```bash
   python3 scripts/rtl_i18n_check.py locales/fa.json [locales/ar.json ...]
   ```

   Exit codes: `0` = no errors · `1` = errors found ·
   `2` = usage error or an unreadable/unparseable file. Warnings do
   not fail the check unless `--strict` is passed.

   Useful flags: `--source locales/en.json` (compare placeholder
   sets against the source locale for the same key),
   `--lang fa|ar|ur|he` (force the language when the file name does
   not carry it — Android `strings.xml` lives in `values-fa/`,
   which is detected automatically), `--digits fa|ar|latn` (pin
   the expected digit script instead of the file majority),
   `--json` (machine-readable output for CI).

2. **Fix mechanically, then review.** `--fix` writes
   `<name>.fixed.<ext>` next to each JSON/ARB input — it never
   overwrites the input — applying only the safely mechanical
   fixes: letter variants (RTL-03), ZWNJ joins (RTL-02), FSI/PDI
   wrapping of unisolated islands (RTL-01), digit normalisation
   (RTL-04). It then re-checks and reports what still needs a
   human. RTL-05 (plurals), RTL-06 (bidi controls) and RTL-07
   (placeholders) are never auto-fixed: a missing Arabic plural
   form or a translated placeholder name is a judgement call, not
   a string replace.

   ```bash
   python3 scripts/rtl_i18n_check.py locales/fa.json --fix --source locales/en.json
   # review the .fixed file, then re-check it until exit 0
   python3 scripts/rtl_i18n_check.py locales/fa.fixed.json --source locales/en.json
   ```

3. **Prove the fix.** Do not report a locale as fixed after
   editing strings — re-run the check on the fixed file until it
   exits 0. Quote the final `0 errors` line in your report.

4. **Know the limits — and say them.** This skill checks the RTL
   *text* layer of locale files. It does not check missing keys,
   untranslated English, or ICU syntax — route those to
   StringLane / Locale Guard / Shipi18n. It does not check RTL
   *layout* (CSS logical properties, mirrored icons) — route that
   to rtlify. It does not translate. Say so in every report (see
   `references/rtl-rules.md`).

## What each check means

- **RTL-01 bidi isolation** — an LTR island (Latin name, URL,
  email, `{placeholder}`, `$5`) inside RTL text without FSI…PDI
  isolation or `<bdi>`. No translation engine adds these invisible
  markers — that is the gap. The visible symptom is punctuation
  and numbers jumping to the wrong side of the island. Pure digit
  runs between RTL words are *not* flagged: per the Unicode bidi
  algorithm they order correctly; only digits touching Latin text
  or a currency symbol misrender.
- **RTL-02 ZWNJ** — Persian/Urdu `می شود` and `کتاب ها` written
  with a space instead of ZWNJ (`می‌شود`, `کتاب‌ها`). A missing
  ZWNJ splits one word into two for search and shaping.
- **RTL-03 letter variants** — Arabic ي/ك/ة inside a Persian file
  (or Persian ی/ک/گ/چ/پ/ژ inside an Arabic file). A word indexed
  with one variant is not found by a query using the other.
- **RTL-04 digit scripts** — two digit scripts in one string
  (۵ and 3), or a string using the file's minority script. Pin
  the policy with `--digits fa|ar|latn` when the product has one.
- **RTL-05 plurals** — missing CLDR forms: Arabic needs six
  (zero, one, two, few, many, other), Persian/Urdu need two
  (one, other). Arabic zero/one/two forms must *not* carry a
  count placeholder (`#`, `{{count}}`) — in Arabic the number
  lives in the word itself.
- **RTL-06 bidi controls** — override characters (RLO/LRO/…)
  present, or FSI/LRI/RLI isolates without a matching PDI.
  Overrides reorder rendered text and are a known spoofing
  vector; use isolates, never overrides.
- **RTL-07 placeholders** — a placeholder name that is not an
  ASCII identifier (a translator translated `{name}` itself —
  the Dashy `{थीम}` bug class), or, with `--source`, a
  placeholder set that differs from the source locale for the
  same key (the listmonk Arabic `{name}` loss class).

## When NOT to use this skill

- The locale is not fa/ar/ur/he — the checks are defined for
  Arabic-script RTL locales (plus Hebrew for isolation/plurals
  only; ZWNJ and letter-variant checks do not apply to it).
- The problem is a missing key or untranslated English — that is
  the generic checkers' job; this skill says nothing about it.
- The problem is RTL layout (CSS, icon mirroring, `dir=rtl`) —
  that is a layout tool's job, not a locale-file checker's.
