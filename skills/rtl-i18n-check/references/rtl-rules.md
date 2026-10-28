# RTL locale rules — why each check exists

The evidence behind `rtl-i18n-check`, and the exact rule each check
enforces. All of it is deterministic: codepoint tables and the
Unicode bidi algorithm, no model, no judgement calls hidden in the
tool.

## The pain, with sources

- "The Arabic version of the app was rendering gibberish. Greetings
  backwards. Numbers in wrong positions. The entire right-to-left
  layout, silently broken." — Akash Cubers, Medium, March 2026,
  "I Watched a $2.4 Million Bug Ship Because Nobody on the Team
  Reads Arabic". "CI was green. Staging looked fine. The English QA
  team signed off. Everything was perfect. if you only speak
  English."
- "Every {variable}, every number, every price. left-to-right
  content embedded in right-to-left Arabic without bidi isolation
  markers. … No translation engine adds these invisible Unicode
  markers. That's the gap." — same article (its "ScriptGuard"
  section). No shipped ScriptGuard repo could be found as of
  2026-10-28 — the article describes the linter, it does not ship
  one.
- "Arabic gets all six categories — and its _zero/_one/_two forms
  deliberately drop the {{count}} placeholder, because in Arabic
  the number lives in the word itself … A translation pipeline that
  blindly demands {{count}} in every plural form produces Arabic
  that reads like a machine." — dev.to, "The app speaks 19
  languages", ~September 2026.
- "A word indexed with one variant is not found by a query that
  uses another." — pkp/pkp-lib issue #13393, ~September 2026, on
  Persian/Arabic yeh/kaf variants and ZWNJ splitting words.
- Real-world placeholder bugs the generic checkers caught and this
  skill now catches at the RTL layer: Dashy translated `{theme}`
  to `{थीम}`; listmonk lost `{name}` in five Arabic messages;
  Open WebUI had placeholder names translated in 10 locales
  (Locale Guard write-up, ~October 2026).

## RTL-01 — isolate every LTR island

Rule: any Latin run (≥2 letters), URL, email, placeholder,
percent-format or currency+number inside an RTL-dominant string
must be wrapped in FSI…PDI (U+2068…U+2069) or `<bdi>…</bdi>`, or
the string is flagged.

Why the whole island, not just the broken-looking ones: the visible
symptom — punctuation and numbers jumping to the wrong side of the
island — appears only when a neutral character sits next to it, but
whether that happens depends on the string's neighbours at render
time, which the checker cannot see. The Unicode and W3C guidance is
to isolate every inline directional run; the checker enforces the
guidance, not the symptom.

Why pure digit runs are exempt: per the Unicode bidi algorithm, a
digit run between RTL words orders correctly (European numbers are
"weak" and take the paragraph direction for placement). Digits are
flagged only when they touch Latin text (`v2.5`, `ABC123`) or a
currency symbol (`$5`) — the cases that actually misrender. HTML
tags (`<b>…</b>`) are markup, not islands, and are masked before
scanning. Single Latin letters (grades, initials) are not flagged.

## RTL-02 — ZWNJ, not space

Persian and Urdu join the verb prefixes `می‌`/`نمی‌` and the plural
suffixes `ها`/`های`/`هایی` with a zero-width non-joiner (U+200C),
not a space. `می شود` and `کتاب ها` are two words to search, shaping
and line-breaking; `می‌شود` and `کتاب‌ها` are one. The check covers
exactly the two patterns agents produce most; it does not guess at
other compounds. Arabic does not use ZWNJ this way, so the check is
scoped to fa/ur.

## RTL-03 — one script, one codepoint per letter

Persian writes ی (U+06CC) and ک (U+06A9); Arabic writes ي (U+064A)
and ك (U+0643). They look identical and are different codepoints,
so a word indexed with one variant is not found by a query using
the other, and a Persian spell-checker flags the Arabic forms as
misspellings. The per-language tables:

| locale | flagged as wrong | correct form |
|---|---|---|
| fa | ي U+064A, ك U+0643, ة U+0629, ى U+0649 | ی U+06CC, ک U+06A9, ه U+0647, ی U+06CC |
| ar | ی U+06CC, ک U+06A9, گ U+06AF, چ U+0686, پ U+067E, ژ U+0698, ے U+06D2 | ي U+064A, ك U+0643 (Persian-only letters have no Arabic form) |
| ur | ي U+064A, ك U+0643, ة U+0629 | ی U+06CC, ک U+06A9, ہ U+06C1 |

Urdu keeps گ چ پ ژ, so they are not flagged there. Hebrew has no
variant table.

## RTL-04 — one digit script per locale

Persian digits (۰–۹, U+06F0–U+06F9), Arabic-Indic digits (٠–٩,
U+0660–U+0669) and Latin digits (0–9) are three scripts. Two scripts
in one string is always an error. Across a file, the default policy
is the file's majority script; strings using the minority script are
warnings. Pin the policy with `--digits fa|ar|latn` when the product
has a house rule — then a mismatch is an error. A file with fewer
than two digit-bearing strings has no majority and skips the
file-level check.

## RTL-05 — CLDR plural forms, and the Arabic count rule

| locale | required forms |
|---|---|
| ar | zero, one, two, few, many, other |
| fa, ur | one, other |
| he | one, two, many, other |

Checked in ICU MessageFormat (`{count, plural, …}`), i18next suffix
keys (`key_zero` … `key_other`, `key` + `key_plural`), Android
`<plurals>` (`quantity="…"`) and gettext PO (`msgid_plural` +
`msgstr[N]`, mapped by the language's standard plural order). A base
key whose own value is an ICU plural is not treated as an i18next
group — `items` (ICU) and `items_few` are two plural systems sharing
a prefix, not one group.

Arabic zero/one/two forms must not contain `#`, `{count}` or
`{{count}}`: in Arabic the number lives in the word itself ("صفر
رسائل", "رسالة واحدة"), so repeating it reads like a machine.

## RTL-06 — isolates, never overrides

U+202A–U+202E (LRE/RLE/PDF/LRO/RLO) reorder rendered text and are a
known spoofing vector (Trojan Source class); they are always an
error. FSI/LRI/RLI isolates must pair with PDI — an unmatched PDI
or an unclosed isolate is an error. Balanced isolates are the fix
for RTL-01 and are never flagged. LRM/RLM (U+200E/U+200F) are
legitimate marks and are not flagged.

## RTL-07 — placeholders are code, not text

Placeholder names must be ASCII identifiers (`{name}`, `{count}`,
`%s`, `%1$s`, `$t(key)`, `{0}`). A name containing non-ASCII
characters means a translator translated the placeholder itself.
With `--source`, the placeholder set of each key must match the
source locale exactly — a dropped `{name}` breaks formatting at
runtime even though the string still "reads fine".

## What this skill deliberately does not check

- Missing keys, untranslated English, ICU syntax errors — the
  generic checkers own those (StringLane, Shipi18n, Locale Guard,
  KAERIS). Pair this skill with one of them; the README names the
  pairing.
- RTL layout — CSS logical properties, mirrored icons, `dir=rtl`
  on containers. That is a layout tool's job (see rtlify for the
  agent-side rules); locale files cannot express it.
- Translation quality — whether the Persian is good Persian. That
  needs a reader of the language; no deterministic checker can do
  it, and this one does not pretend to.

## Nearest existing projects — why they do not cover this

- **StringLane** (`npx @stringlane/cli check`, Oct 2026): missing
  keys, placeholder mismatch, plural-form counts, ICU syntax,
  MCP. No bidi, ZWNJ, letter-variant or digit check.
- **Shipi18n** (2025–2026): missing keys, dropped placeholders,
  collapsed plurals, glossary, .po↔JED sync, MCP, GitHub Action.
  No RTL layer anywhere on the page.
- **Locale Guard** (~Oct 2026): GitHub Action, per-PR comment,
  missing keys / placeholders / ICU args / plural forms via
  `Intl.PluralRules`. Strong bug evidence (Dashy, Open WebUI,
  listmonk) — all structural/generic, none RTL-specific.
- **KAERIS**: CLI + MCP + GitHub Action, back-translation QA,
  "i18n-firewall". Generic; no RTL layer described.
- **globalize.now**: SaaS translation pipeline that *adds* RTL
  markers while translating (paid). It is not a free deterministic
  checker for locale files however produced, and it does not check
  ZWNJ, letter variants or digits.
- **rtl-text-tools** (JS library, ~May 2026): `fixRTL`,
  `wrapLTR`, digit conversion. A library for processing text, not
  a checker/skill/CI gate; no locale-file scanning, no plural or
  digit rules.
- **rtlify** (Mar 2026): teaches agents RTL *layout* rules
  (logical CSS). Adjacent, not overlapping — it never touches
  locale files.
