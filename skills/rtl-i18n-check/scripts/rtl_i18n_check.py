#!/usr/bin/env python3
"""rtl-i18n-check — deterministic checker for RTL (fa/ar/ur/he) locale files.

Agents translate whole locale files in one pass, the build passes, the
JSON is valid — and the shipped strings render backwards, lose numbers,
or read as machine Arabic. Generic i18n checkers (StringLane, Shipi18n,
Locale Guard, KAERIS) check missing keys, placeholders and plural
counts, but none of them checks the RTL layer. This tool is that layer.

Zero-dependency (Python standard library only).

Checks (all deterministic, no model, no network):
  RTL-01  bidi-isolation  (error) LTR island (Latin name, URL, email,
          placeholder, currency+number) inside RTL text without
          FSI/PDI isolation or <bdi>
  RTL-02  zwnj            (error) fa/ur: "می شود" / "کتاب ها" written
          with a space instead of ZWNJ
  RTL-03  letter-variants (error) Arabic ي/ك/ة inside a Persian file
          (or Persian ی/ک/گ/چ/پ/ژ inside an Arabic file, etc.)
  RTL-04  digit-mixing    (error) two digit scripts in one string;
          (warning) a string using the file's minority digit script
  RTL-05  plurals         (error) missing CLDR plural forms (Arabic
          needs six); Arabic zero/one/two forms containing a count
          placeholder — the number lives in the word itself
  RTL-06  bidi-controls   (error) override characters (RLO/LRO/...) or
          unpaired FSI/LRI/RLI/PDI isolates
  RTL-07  placeholders    (error) placeholder names that are not ASCII
          identifiers (a translator translated "{name}"); with
          --source, placeholder sets that differ from the source
          locale for the same key

Formats: JSON (incl. nested i18next), ARB (Flutter), Android
strings.xml (incl. <plurals>), gettext PO (incl. plural forms),
YAML (flat/nested mappings subset).

Usage:
  python3 rtl_i18n_check.py locales/fa.json [locales/ar.json ...]
      [--lang fa] [--source locales/en.json]
      [--digits fa|ar|latn|auto] [--strict] [--fix] [--json]

Exit codes: 0 = no errors (warnings allowed unless --strict),
1 = errors found, 2 = usage error or an unreadable/unparseable file.

--fix writes "<name>.fixed.<ext>" next to each JSON/ARB input (never
overwrites the input) applying the safely mechanical fixes — RTL-03
codepoints, RTL-02 ZWNJ joins, RTL-01 FSI/PDI wrapping, RTL-04 digit
normalisation — then re-checks and reports what still needs a human.
RTL-05/06/07 are never auto-fixed.
"""

import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

VERSION = "0.1.0"

ZWNJ = "\u200c"
FSI = "\u2068"
LRI = "\u2066"
RLI = "\u2067"
PDI = "\u2069"
OVERRIDES = set("\u202a\u202b\u202c\u202d\u202e")  # LRE RLE PDF LRO RLO
ISOLATE_OPEN = {FSI, LRI, RLI}
ISOLATE_CLOSE = PDI

ARABIC_BLOCK = "[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff\ufb50-\ufdff\ufe70-\ufeff]"
HEBREW_BLOCK = "[\u0590-\u05ff]"
RTL_STRONG = re.compile(ARABIC_BLOCK + "|" + HEBREW_BLOCK)
ARABIC_CHARS = re.compile(ARABIC_BLOCK)
PERSIAN_MARKERS = set("گچپژ")

DIGITS = {
    "latn": set("0123456789"),
    "fa": set("\u06f0\u06f1\u06f2\u06f3\u06f4\u06f5\u06f6\u06f7\u06f8\u06f9"),
    "ar": set("\u0660\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669"),
}
DIGIT_RE = {
    name: re.compile("[" + "".join(sorted(chars)) + "]")
    for name, chars in DIGITS.items()
}
ALL_DIGITS = re.compile(r"[0-9\u0660-\u0669\u06f0-\u06f9]")

# Letter variants that are wrong in each locale language.
# fa: Arabic yeh/kaf/teh-marbuta/alef-maksura must be the Persian forms.
# ar: Persian yeh/kaf and the Persian-only letters must be Arabic forms.
# ur: Arabic yeh/kaf/teh-marbuta must be the Urdu (Persian-form) letters;
#     Urdu keeps گ چ پ ژ, so they are NOT in its bad set.
LETTER_VARIANTS = {
    "fa": {"\u064a": "\u06cc", "\u0643": "\u06a9",
           "\u0629": "\u0647", "\u0649": "\u06cc"},
    "ar": {"\u06cc": "\u064a", "\u06a9": "\u0643",
           "\u06af": None, "\u0686": None, "\u067e": None,
           "\u0698": None, "\u06d2": None},
    "ur": {"\u064a": "\u06cc", "\u0643": "\u06a9",
           "\u0629": "\u06c1"},
    "he": {},
}

# CLDR plural categories each language needs (ICU / i18next / Android / PO).
PLURAL_REQUIRED = {
    "ar": {"zero", "one", "two", "few", "many", "other"},
    "fa": {"one", "other"},
    "ur": {"one", "other"},
    "he": {"one", "two", "many", "other"},
}
# Arabic zero/one/two forms must NOT carry a count placeholder: the
# number lives in the word itself ("صفر رسائل", "رسالة واحدة").
AR_NO_COUNT_FORMS = {"zero", "one", "two"}

# gettext Plural-Forms index -> CLDR category, per language.
PO_PLURAL_MAP = {
    "ar": ["zero", "one", "two", "few", "many", "other"],
    "fa": ["one", "other"],
    "ur": ["one", "other"],
    "he": ["one", "two", "many", "other"],
}

I18NEXT_SUFFIXES = ("_zero", "_one", "_two", "_few", "_many", "_other")

# --- findings ---------------------------------------------------------------

def finding(filename, key, code, severity, message, detail=""):
    return {"file": str(filename), "key": key or "", "code": code,
            "severity": severity, "message": message, "detail": detail}


# --- locale loading ---------------------------------------------------------

def flatten(prefix, value, out):
    """Flatten nested JSON/YAML mappings to dotted keys; string leaves only."""
    if isinstance(value, dict):
        for k, v in value.items():
            flatten(f"{prefix}.{k}" if prefix else str(k), v, out)
    elif isinstance(value, str):
        out[prefix] = value
    # Numbers/bools/nulls are not translatable strings; ignore silently.


def load_json_like(path, text):
    """JSON and ARB. Returns (entries, plurals, error)."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return {}, {}, f"{path}: invalid JSON: {exc}"
    if not isinstance(data, dict):
        return {}, {}, f"{path}: top level is not an object"
    entries = {}
    for key, value in data.items():
        if key.startswith("@"):
            continue  # ARB metadata (@key, @@locale)
        if isinstance(value, dict) and "message" in value:
            value = value["message"]
        flatten(key, value, entries)
    return entries, {}, None


def _xml_unescape(value):
    return (value.replace("\\'", "'").replace('\\"', '"')
            .replace("\\n", "\n").replace("\\t", "\t")
            .replace("\\@", "@"))


def load_android_xml(path, text):
    """Android strings.xml, including <plurals>."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        return {}, {}, f"{path}: invalid XML: {exc}"
    entries, plurals = {}, {}
    for elem in root:
        if elem.tag == "string":
            name = elem.get("name")
            if name:
                entries[name] = _xml_unescape("".join(elem.itertext()))
        elif elem.tag == "plurals":
            name = elem.get("name")
            if not name:
                continue
            group = {}
            for item in elem.findall("item"):
                qty = item.get("quantity")
                if qty:
                    group[qty] = _xml_unescape("".join(item.itertext()))
            if group:
                plurals[name] = group
    return entries, plurals, None


def _po_unquote(token):
    if len(token) >= 2 and token.startswith('"') and token.endswith('"'):
        try:
            return json.loads(token)
        except json.JSONDecodeError:
            return token[1:-1]
    return token


def load_po(path, text):
    """Gettext PO: msgid/msgstr pairs, msgid_plural + msgstr[N]."""
    entries, plurals = {}, {}
    header = {}
    # Split into entries: a new entry starts at a "msgid" line once the
    # current chunk already holds one (msgid_plural/msgstr lines and
    # continued "..." lines stay in the same chunk).
    chunks, current = [], []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if (re.match(r'msgid\s', stripped)
                or stripped.startswith('msgid"')) \
                and any(l.strip().startswith("msgid") for l in current):
            chunks.append(current)
            current = []
        current.append(line)
    if current:
        chunks.append(current)

    def parse_chunk(lines):
        msgid, msgid_plural, msgstrs = "", None, {}
        field, buf = None, ""
        for line in lines:
            s = line.strip()
            m = re.match(r'(msgid_plural|msgid|msgstr\[(\d+)\]|msgstr)\s+(.*)$', s)
            if m:
                if field == "msgid":
                    msgid = buf
                elif field == "msgid_plural":
                    msgid_plural = buf
                elif field and field.startswith("msgstr"):
                    idx = int(field[7:-1]) if "[" in field else 0
                    msgstrs[idx] = buf
                field = m.group(1)
                buf = _po_unquote(m.group(3).strip())
            elif s.startswith('"'):
                buf += _po_unquote(s)
        if field == "msgid":
            msgid = buf
        elif field == "msgid_plural":
            msgid_plural = buf
        elif field and field.startswith("msgstr"):
            idx = int(field[7:-1]) if "[" in field else 0
            msgstrs[idx] = buf
        return msgid, msgid_plural, msgstrs

    for chunk in chunks:
        msgid, msgid_plural, msgstrs = parse_chunk(chunk)
        if msgid == "" and msgid_plural is None and msgstrs:
            header = dict(
                kv.split(":", 1) for kv in msgstrs.get(0, "").splitlines()
                if ":" in kv)
            continue
        if msgid_plural is not None:
            plurals[msgid] = msgstrs  # indexed; mapped to categories later
        elif msgid:
            entries[msgid] = msgstrs.get(0, "")
    return entries, plurals, None


def _yaml_scalar(raw):
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        try:
            return json.loads(raw) if raw[0] == '"' else raw[1:-1]
        except json.JSONDecodeError:
            return raw[1:-1]
    return raw


def load_yaml(path, text):
    """Minimal YAML subset for locale files: nested mappings, quoted
    and plain scalars, full-line and trailing comments. Lists and
    block scalars are out of scope for v1 (reported as an error)."""
    entries = {}
    stack = []  # (indent, key-prefix)
    for lineno, raw_line in enumerate(text.splitlines(), 1):
        if not raw_line.strip() or raw_line.strip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()
        if line.startswith("- "):
            return {}, {}, (f"{path}:{lineno}: YAML lists are not "
                           "supported in v1")
        if ":" not in line:
            return {}, {}, f"{path}:{lineno}: cannot parse line: {line!r}"
        key, _, rest = line.partition(":")
        key = key.strip().strip("\"'")
        rest = rest.strip()
        # Strip a trailing comment on an unquoted scalar.
        if rest and rest[0] not in "\"'" and " #" in rest:
            rest = rest.split(" #", 1)[0].rstrip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        prefix = stack[-1][1] if stack else ""
        full = f"{prefix}.{key}" if prefix else key
        if rest == "":
            stack.append((indent, full))
        else:
            entries[full] = _yaml_scalar(rest)
    return entries, {}, None


LOADERS = {
    ".json": load_json_like,
    ".arb": load_json_like,
    ".xml": load_android_xml,
    ".po": load_po,
    ".yaml": load_yaml,
    ".yml": load_yaml,
}


def load_locale_file(path):
    """Returns (entries, plurals, error). plurals: base key ->
    {category: value} for Android/PO, or {index: value} for PO."""
    path = Path(path)
    loader = LOADERS.get(path.suffix.lower())
    if loader is None:
        return {}, {}, f"{path}: unsupported format {path.suffix!r} " \
            "(supported: .json .arb .xml .po .yaml .yml)"
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return {}, {}, f"{path}: cannot read: {exc}"
    return loader(path, text)


# --- language detection -----------------------------------------------------

def detect_lang(filename, entries, forced=None):
    if forced:
        return forced
    # The language usually lives in the file name (fa.json, ar.yaml)
    # or, for Android, in the parent directory (values-fa/strings.xml).
    hay = " ".join(p.lower() for p in Path(filename).parts)
    for lang in ("fa", "ar", "ur", "he"):
        if re.search(rf"(^|[^a-z]){lang}([^a-z]|$)", hay):
            return lang
    sample = "\n".join(list(entries.values())[:50])
    if any(c in sample for c in PERSIAN_MARKERS) or ZWNJ in sample:
        return "fa"
    if ARABIC_CHARS.search(sample):
        return "ar"
    if re.search(HEBREW_BLOCK, sample):
        return "he"
    return None

# --- per-string checks ------------------------------------------------------

# Chars skipped when looking for a group's effective neighbours.
_NEUTRAL = set(" \t.,:;!؟،«»\"'()[]")
_ISLAND_RE = re.compile(r"""
    https?://[^\s<>"']+ | www\.[^\s<>"']+ |
    [\w.+-]+@[\w-]+\.[\w.]+ |
    \{\{[^{}]*\}\} | \{[^{}]*\} |
    %[0-9]*\$?[sdf] |
    \$t\([^)]*\) |
    [A-Za-z]{2,}[A-Za-z0-9._-]* |
    [$€£¥]?\s?[0-9\u0660-\u0669\u06f0-\u06f9]+(?:[.,:][0-9\u0660-\u0669\u06f0-\u06f9]+)*
""", re.VERBOSE)


def _is_rtl_char(ch):
    return bool(RTL_STRONG.match(ch)) if ch else False


def _mask_spans(text, spans):
    """Replace every char inside spans with a space (offsets preserved)."""
    chars = list(text)
    for start, end in spans:
        for i in range(start, end):
            chars[i] = " "
    return "".join(chars)


def _isolated_spans(text):
    """Spans already isolated: FSI/LRI/RLI...PDI pairs and <bdi>...</bdi>."""
    spans = []
    stack = []
    for i, ch in enumerate(text):
        if ch in ISOLATE_OPEN:
            stack.append(i)
        elif ch == PDI and stack:
            spans.append((stack.pop(), i + 1))
    for m in re.finditer(r"<bdi\b[^>]*>.*?</bdi\s*>", text,
                         re.IGNORECASE | re.DOTALL):
        spans.append(m.span())
    return spans


def _effective_neighbour(masked, spans, pos, direction):
    """Nearest char at/after pos moving in direction, skipping spaces,
    neutral punctuation and any char inside an island span. Returns the
    char, or None at the string edge."""
    i = pos
    while 0 <= i < len(masked):
        if any(s <= i < e for s, e in spans):
            i += direction
            continue
        ch = masked[i]
        if ch in _NEUTRAL:
            i += direction
            continue
        return ch
    return None


def find_unisolated_groups(text):
    """LTR island groups inside RTL text that lack bidi isolation.

    Returns a list of (start, end, kind, snippet). A group is flagged
    when RTL-strong text stands on both sides, or on one side with the
    string edge on the other. Pure digit runs are NOT flagged on their
    own — per the Unicode bidi algorithm they order correctly between
    RTL words; they are flagged only when they touch Latin text or a
    currency symbol (the cases that actually misrender).
    """
    if not RTL_STRONG.search(text):
        return []
    masked = _mask_spans(text, _isolated_spans(text))
    # Mask HTML tags: "<b>...</b>" is markup, not an LTR island.
    masked = re.sub(r"<[^>]*>", lambda m: " " * len(m.group(0)), masked)
    matches = list(_ISLAND_RE.finditer(masked))
    if not matches:
        return []
    # Trim trailing sentence punctuation off URL/email matches
    # ("see https://x.com." — the "." is not part of the URL).
    trimmed = []
    for m in matches:
        start, end = m.span()
        text_m = m.group(0)
        if text_m.startswith(("http://", "https://", "www.")) \
                or re.search(r"\w@\w", text_m):
            while end > start and masked[end - 1] in ".,;:!?)]}'":
                end -= 1
        if end > start:
            trimmed.append((start, end))
    matches = trimmed
    if not matches:
        return []
    spans = matches
    # Merge matches whose gap holds no RTL-strong char ("Hello World").
    groups = []
    start, end = matches[0]
    for s, e in matches[1:]:
        if RTL_STRONG.search(masked[end:s]):
            groups.append((start, end))
            start, end = s, e
        else:
            end = e
    groups.append((start, end))

    flagged = []
    for start, end in groups:
        snippet = masked[start:end].strip()
        has_latin = bool(re.search(r"[A-Za-z]", snippet))
        has_currency = bool(re.search(r"[$€£¥]", snippet))
        is_pure_digits = bool(re.fullmatch(
            r"[0-9\u0660-\u0669\u06f0-\u06f9]+(?:[.,:][0-9\u0660-\u0669"
            r"\u06f0-\u06f9]+)*", snippet))
        if is_pure_digits and not has_currency:
            continue
        if snippet.startswith("{") and not re.search(r"[A-Za-z0-9]", snippet):
            # A placeholder with no Latin/ASCII content (e.g. "{نام}")
            # is not an LTR island — RTL-07 reports the translated name.
            continue
        left = _effective_neighbour(masked, spans, start - 1, -1)
        right = _effective_neighbour(masked, spans, end, +1)
        left_rtl, right_rtl = _is_rtl_char(left), _is_rtl_char(right)
        if left_rtl and right_rtl:
            pass
        elif left_rtl != right_rtl:
            if is_pure_digits:
                continue
        else:
            continue
        if snippet.startswith(("http://", "https://", "www.")):
            kind = "url"
        elif "@" in snippet and re.search(r"\w@\w", snippet):
            kind = "email"
        elif snippet.startswith(("{", "%", "$t")):
            kind = "placeholder"
        elif has_currency:
            kind = "currency-number"
        else:
            kind = "latin"
        flagged.append((start, end, kind, snippet))
    return flagged


MI_SPACE = re.compile(r"(می|نمی)[ ]+(?=" + ARABIC_BLOCK + ")")
PLURAL_SPACE = re.compile(
    r"(?<=" + ARABIC_BLOCK + r")[ ]+(ها|های|هایی)(?=[\s\.,،؛:!؟]|$)")


def check_string(value, lang, filename, key):
    """Per-string checks: RTL-01/02/03, RTL-04 in-string, RTL-06,
    RTL-07 placeholder names, RTL-05 ICU plurals."""
    out = []
    if not value:
        return out

    for start, end, kind, snippet in find_unisolated_groups(value):
        out.append(finding(
            filename, key, "RTL-01", "error",
            f"Unisolated {kind} {snippet!r} inside RTL text — wrap it in "
            "FSI…PDI (U+2068…U+2069) or <bdi>, or it renders in the wrong "
            "place.",
            f"position {start}–{end}"))

    if lang in ("fa", "ur"):
        for m in MI_SPACE.finditer(value):
            out.append(finding(
                filename, key, "RTL-02", "error",
                f"«{m.group(1)} …» uses a space; the Persian verb prefix "
                f"joins with ZWNJ: «{m.group(1)}‌» + verb.",
                value.strip()[:100]))
        for m in PLURAL_SPACE.finditer(value):
            out.append(finding(
                filename, key, "RTL-02", "error",
                f"Plural suffix «{m.group(1)}» split off with a space; "
                "join it with ZWNJ.",
                value.strip()[:100]))

    variants = LETTER_VARIANTS.get(lang, {})
    if variants:
        bad = sorted({c for c in value if c in variants})
        if bad:
            parts = []
            for c in bad:
                repl = variants[c]
                parts.append(f"U+{ord(c):04X} ({c!r}) → "
                             f"{repl!r}" if repl else
                             f"U+{ord(c):04X} ({c!r}) is not an "
                             f"{lang} letter")
            out.append(finding(
                filename, key, "RTL-03", "error",
                "Wrong-script letter variant(s): " + "; ".join(parts) +
                ". A word indexed with one variant is not found by a "
                "query using the other.",
                value.strip()[:100]))

    scripts = {name for name, rx in DIGIT_RE.items() if rx.search(value)}
    if len(scripts) > 1:
        out.append(finding(
            filename, key, "RTL-04", "error",
            "Digit scripts mixed in one string: " +
            ", ".join(sorted(scripts)) + ". Pick one script per locale.",
            value.strip()[:100]))

    for c in sorted({c for c in value if c in OVERRIDES}):
        out.append(finding(
            filename, key, "RTL-06", "error",
            f"Bidi override U+{ord(c):04X} present — overrides reorder "
            "rendered text and are a known spoofing vector. Use "
            "isolates (FSI…PDI) instead, never overrides.",
            value.strip()[:100]))
    depth = 0
    for ch in value:
        if ch in ISOLATE_OPEN:
            depth += 1
        elif ch == PDI:
            if depth == 0:
                out.append(finding(
                    filename, key, "RTL-06", "error",
                    "Unmatched PDI (U+2069) — an isolate close with no "
                    "matching FSI/LRI/RLI.", value.strip()[:100]))
                break
            depth -= 1
    else:
        if depth:
            out.append(finding(
                filename, key, "RTL-06", "error",
                f"{depth} unclosed isolate(s) (FSI/LRI/RLI with no "
                "matching PDI).", value.strip()[:100]))

    for name in sorted(extract_placeholders(value)):
        if not re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_.\-]*|\d+|%[sdf]|\$t:.+", name):
            out.append(finding(
                filename, key, "RTL-07", "error",
                f"Placeholder {name!r} is not an ASCII identifier — a "
                "translator translated the placeholder name itself. "
                "Placeholder names must stay exactly as in the source.",
                value.strip()[:100]))

    out.extend(check_icu_plurals(value, lang, filename, key))
    return out


# --- placeholders -----------------------------------------------------------

ICU_BLOCK_RE = re.compile(
    r"\{\w+\s*,\s*(?:plural|select)\s*,(?:[^{}]|\{[^{}]*\})*\}")
PLACEHOLDER_RE = re.compile(
    r"\{\{([^{}]*)\}\}|\{([^{}]*)\}|%([0-9]*\$?[sdf])|\$t\(([^)]*)\)")


def extract_placeholders(value):
    """Placeholder names in a string, ICU plural/select blocks removed.
    %s/%d become "%s"/"%d"; $t(key) becomes "$t:key"."""
    clean = ICU_BLOCK_RE.sub("", value)
    names = set()
    for m in PLACEHOLDER_RE.finditer(clean):
        if m.group(1) is not None:
            names.add(m.group(1).strip())
        elif m.group(2) is not None:
            names.add(m.group(2).strip())
        elif m.group(3) is not None:
            names.add("%" + m.group(3)[-1])
        elif m.group(4) is not None:
            names.add("$t:" + m.group(4).strip())
    return names


def check_placeholders_vs_source(value, source_value, filename, key):
    out = []
    if source_value is None:
        return out
    src = extract_placeholders(source_value)
    tgt = extract_placeholders(value)
    missing, extra = src - tgt, tgt - src
    if missing or extra:
        detail = []
        if missing:
            detail.append("missing from translation: " +
                          ", ".join(sorted(missing)))
        if extra:
            detail.append("not in source: " + ", ".join(sorted(extra)))
        out.append(finding(
            filename, key, "RTL-07", "error",
            "Placeholder set differs from the source locale for this "
            "key — a dropped or renamed placeholder breaks formatting "
            "at runtime.",
            "; ".join(detail)))
    return out


# --- plurals ----------------------------------------------------------------

ICU_PLURAL_RE = re.compile(
    r"\{(\w+)\s*,\s*plural\s*,((?:[^{}]|\{[^{}]*\})*)\}")
ICU_FORM_RE = re.compile(r"(\w+)\s*\{([^{}]*)\}")


def check_icu_plurals(value, lang, filename, key):
    out = []
    for m in ICU_PLURAL_RE.finditer(value):
        forms = dict(ICU_FORM_RE.findall(m.group(2)))
        out.extend(_check_plural_forms(
            forms, lang, filename, key, where="ICU plural"))
    return out


def _check_plural_forms(forms, lang, filename, key, where):
    out = []
    required = PLURAL_REQUIRED.get(lang)
    if not required:
        return out
    missing = required - set(forms)
    if missing:
        out.append(finding(
            filename, key, "RTL-05", "error",
            f"{where} for {lang} is missing plural form(s): " +
            ", ".join(sorted(missing)) + f" (needs {len(required)}: " +
            ", ".join(sorted(required)) + ").",
            "present: " + ", ".join(sorted(forms)) if forms else "no forms"))
    if lang == "ar":
        for form in AR_NO_COUNT_FORMS & set(forms):
            text = forms[form]
            if "#" in text or "{count}" in text or "{{count}}" in text:
                out.append(finding(
                    filename, key, "RTL-05", "error",
                    f"Arabic {form} form carries a count placeholder "
                    "(#, {{count}}) — in Arabic the number lives in the "
                    "word itself, so the form must not repeat it.",
                    text.strip()[:100]))
    return out


def check_plural_group(base, forms, lang, filename,
                       source_forms=None, where="plural group"):
    """forms: {category: value}. source_forms: same, from --source."""
    out = _check_plural_forms(forms, lang, filename, base, where)
    if source_forms is not None:
        for cat in sorted(set(forms) & set(source_forms)):
            out.extend(check_placeholders_vs_source(
                forms[cat], source_forms[cat], filename, f"{base} [{cat}]"))
    return out


def group_i18next_suffixes(entries):
    """{base: {category: value}} for key_zero/_one/... keys. A bare base
    key counts as the 'other' form (i18next fallback)."""
    groups = {}
    for key, value in entries.items():
        for suffix in I18NEXT_SUFFIXES:
            if key.endswith(suffix):
                groups.setdefault(key[:-len(suffix)], {})[suffix[1:]] = value
                break
    for base, group in groups.items():
        if "other" not in group and base in entries:
            group["other"] = entries[base]
    # Old-style key + key_plural (one/other only).
    for key, value in entries.items():
        if key.endswith("_plural"):
            base = key[:-7]
            if base in entries and base not in groups:
                groups[base] = {"one": entries[base], "other": value}
    # A base whose own value is an ICU plural is not an i18next group:
    # "items" (ICU) + "items_few" are two different plural systems
    # sharing a prefix, not one i18next group.
    return {base: group for base, group in groups.items()
            if not ICU_PLURAL_RE.search(entries.get(base, ""))}


# --- file-level check -------------------------------------------------------

def _dominant_digit_script(value):
    counts = {name: len(rx.findall(value))
              for name, rx in DIGIT_RE.items()}
    best = max(counts.values())
    if best == 0:
        return None
    winners = [n for n, c in counts.items() if c == best]
    return winners[0] if len(winners) == 1 else None


def check_entries(entries, plurals, lang, filename,
                  source_entries=None, source_plurals=None,
                  digits_policy="auto"):
    """Run every check over one loaded locale file."""
    findings = []
    source_entries = source_entries or {}
    for key in sorted(entries):
        value = entries[key]
        findings.extend(check_string(value, lang, filename, key))
        findings.extend(check_placeholders_vs_source(
            value, source_entries.get(key), filename, key))

    # i18next suffix groups (key_zero ... key_other, key + key_plural).
    for base, group in sorted(group_i18next_suffixes(entries).items()):
        src_group = None
        if source_entries:
            src_group = group_i18next_suffixes(source_entries).get(base)
        findings.extend(check_plural_group(
            base, group, lang, filename, src_group, "i18next plurals"))

    # Android <plurals> and gettext PO plurals.
    for base, forms in sorted(plurals.items()):
        if forms and all(isinstance(k, int) for k in forms):
            mapping = PO_PLURAL_MAP.get(lang, [])
            forms = {mapping[i]: v for i, v in sorted(forms.items())
                     if i < len(mapping)}
        # Each form is a translatable string: same per-string checks.
        for cat, value in sorted(forms.items()):
            findings.extend(check_string(
                value, lang, filename, f"{base} [{cat}]"))
        src_forms = None
        if source_plurals and base in source_plurals:
            src_forms = source_plurals[base]
            if src_forms and all(isinstance(k, int) for k in src_forms):
                mapping = PO_PLURAL_MAP.get(lang, [])
                src_forms = {mapping[i]: v
                             for i, v in sorted(src_forms.items())
                             if i < len(mapping)}
        findings.extend(check_plural_group(
            base, forms, lang, filename, src_forms, "plural group"))

    # File-level digit policy.
    dominant = {}
    for key, value in entries.items():
        script = _dominant_digit_script(value)
        if script:
            dominant[key] = script
    if dominant:
        if digits_policy in DIGITS:
            expected = digits_policy
            for key, script in sorted(dominant.items()):
                if script != expected:
                    findings.append(finding(
                        filename, key, "RTL-04", "error",
                        f"Digit script is {script}, but this file's "
                        f"policy is {expected}.", entries[key].strip()[:100]))
        elif digits_policy == "auto" and len(dominant) >= 2:
            majority = max(set(dominant.values()),
                           key=lambda s: list(dominant.values()).count(s))
            counts = {s: list(dominant.values()).count(s)
                      for s in set(dominant.values())}
            if len(counts) > 1:
                for key, script in sorted(dominant.items()):
                    if script != majority:
                        findings.append(finding(
                            filename, key, "RTL-04", "warning",
                            f"Digit script is {script}, but most strings "
                            f"in this file use {majority}.",
                            entries[key].strip()[:100]))
    return findings

# --- auto-fix (JSON/ARB only, never overwrites the input) -------------------

def _digit_translation(target):
    """Map every digit char of the other scripts to the target script."""
    table = {}
    for name, chars in DIGITS.items():
        if name == target:
            continue
        for i, ch in enumerate(sorted(chars)):
            table[ch] = sorted(DIGITS[target])[i]
    return table


def fix_string(value, lang, digits_target=None):
    """Mechanical fixes only: RTL-03 codepoints, RTL-02 ZWNJ joins,
    RTL-01 FSI/PDI wrapping, RTL-04 digit normalisation.
    Returns (fixed_value, [notes])."""
    fixed, notes = value, []
    variants = LETTER_VARIANTS.get(lang, {})
    changed = sorted({c for c in fixed if c in variants and variants[c]})
    if changed:
        for c in changed:
            fixed = fixed.replace(c, variants[c])
        notes.append("letter variants → " +
                     ", ".join(f"U+{ord(c):04X}" for c in changed))
    if lang in ("fa", "ur"):
        new = MI_SPACE.sub(lambda m: m.group(1) + ZWNJ, fixed)
        new = PLURAL_SPACE.sub(lambda m: ZWNJ + m.group(1), new)
        if new != fixed:
            fixed = new
            notes.append("ZWNJ joins restored")
    for start, end, kind, snippet in reversed(find_unisolated_groups(fixed)):
        fixed = fixed[:start] + FSI + fixed[start:end] + PDI + fixed[end:]
        notes.append(f"wrapped {kind} {snippet!r} in FSI…PDI")
    if digits_target:
        table = _digit_translation(digits_target)
        new = fixed.translate(str.maketrans(table))
        if new != fixed:
            fixed = new
            notes.append(f"digits → {digits_target} script")
    return fixed, notes


def _fix_walk(node, lang, digits_target, prefix, notes):
    if isinstance(node, dict):
        return {k: (v if k.startswith("@") else
                    _fix_walk(v, lang, digits_target,
                              f"{prefix}.{k}" if prefix else str(k), notes))
                for k, v in node.items()}
    if isinstance(node, str):
        fixed, applied = fix_string(node, lang, digits_target)
        if applied:
            notes.append(f"{prefix}: " + "; ".join(applied))
        return fixed
    return node


def fix_json_file(path, lang, digits_policy="auto",
                  source_entries=None, source_plurals=None):
    """Write <stem>.fixed<ext> next to the input; return
    (fixed_path, fix_notes, remaining_findings) or (None, [], error)."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, [], f"{path}: cannot fix: {exc}"
    entries, _, err = load_json_like(path, path.read_text(encoding="utf-8"))
    if err:
        return None, [], err
    target = None
    if digits_policy in DIGITS:
        target = digits_policy
    else:
        dominant = [_dominant_digit_script(v) for v in entries.values()]
        dominant = [d for d in dominant if d]
        if dominant:
            target = max(set(dominant), key=dominant.count)
    notes = []
    fixed_data = _fix_walk(data, lang, target, "", notes)
    fixed_path = path.with_name(f"{path.stem}.fixed{path.suffix}")
    fixed_path.write_text(
        json.dumps(fixed_data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    fixed_entries, fixed_plurals, err = load_locale_file(fixed_path)
    remaining = []
    if not err:
        remaining = check_entries(fixed_entries, fixed_plurals, lang,
                                  fixed_path, source_entries, source_plurals,
                                  digits_policy)
    return fixed_path, notes, remaining


# --- CLI --------------------------------------------------------------------

def _print_text(findings, n_files, n_keys):
    errors = [f for f in findings if f["severity"] == "error"]
    warns = [f for f in findings if f["severity"] == "warning"]
    for f in findings:
        tag = {"error": "FAIL", "warning": "WARN",
               "info": "INFO"}[f["severity"]]
        key = f" :: {f['key']}" if f["key"] else ""
        print(f"[{tag}] {f['code']} {f['file']}{key}: {f['message']}")
        if f["detail"]:
            print(f"       {f['detail']}")
    print(f"\n{n_files} file(s), {n_keys} key(s): "
          f"{len(errors)} error(s), {len(warns)} warning(s).")


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(
        prog="rtl_i18n_check",
        description="Check fa/ar/ur/he locale files for the RTL bugs "
                    "generic i18n checkers cannot see.")
    parser.add_argument("files", nargs="+", help="locale files to check")
    parser.add_argument("--lang", choices=["fa", "ar", "ur", "he"],
                        help="force the locale language for all files")
    parser.add_argument("--source", help="source-locale file (e.g. en) "
                        "to compare placeholder sets against")
    parser.add_argument("--digits", choices=["fa", "ar", "latn", "auto"],
                        default="auto",
                        help="expected digit script (default: file majority)")
    parser.add_argument("--strict", action="store_true",
                        help="warnings also fail the check")
    parser.add_argument("--fix", action="store_true",
                        help="write <name>.fixed.<ext> for JSON/ARB inputs")
    parser.add_argument("--json", action="store_true",
                        help="machine-readable JSON output")
    args = parser.parse_args(argv)

    source_entries, source_plurals = {}, {}
    if args.source:
        source_entries, source_plurals, err = load_locale_file(args.source)
        if err:
            print(err, file=sys.stderr)
            return 2

    all_findings, n_keys, load_errors = [], 0, []
    for fname in args.files:
        entries, plurals, err = load_locale_file(fname)
        if err:
            load_errors.append(err)
            continue
        lang = detect_lang(fname, entries, args.lang)
        if lang is None:
            load_errors.append(
                f"{fname}: cannot detect the locale language — pass "
                "--lang fa|ar|ur|he")
            continue
        n_keys += len(entries) + sum(len(g) for g in plurals.values())
        findings = check_entries(entries, plurals, lang, fname,
                                 source_entries, source_plurals,
                                 args.digits)
        all_findings.extend(findings)
        if args.fix and Path(fname).suffix.lower() in (".json", ".arb"):
            fixed_path, notes, remaining = fix_json_file(
                fname, lang, args.digits, source_entries, source_plurals)
            if fixed_path is None:
                print(notes[0] if notes else "fix failed", file=sys.stderr)
            else:
                rem_err = [f for f in remaining
                           if f["severity"] == "error"]
                # Fix notes go to stderr so --json stdout stays pure JSON.
                print(f"\n# Fix: {fname} → {fixed_path}", file=sys.stderr)
                for note in notes:
                    print(f"  - {note}", file=sys.stderr)
                print(f"  {len(rem_err)} error(s) remain after the fix "
                      "(RTL-05/06/07 need a human).", file=sys.stderr)
        elif args.fix:
            print(f"\n# Fix: {fname}: --fix supports JSON/ARB in v1; "
                  "apply the listed changes by hand.", file=sys.stderr)

    if load_errors:
        for err in load_errors:
            print(err, file=sys.stderr)
        return 2

    n_errors = sum(1 for f in all_findings if f["severity"] == "error")
    n_warns = sum(1 for f in all_findings if f["severity"] == "warning")
    if args.json:
        print(json.dumps({
            "tool": "rtl-i18n-check", "version": VERSION,
            "counts": {"error": n_errors, "warning": n_warns},
            "findings": all_findings}, ensure_ascii=False, indent=2))
    else:
        _print_text(all_findings, len(args.files), n_keys)
    if n_errors or (args.strict and n_warns):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
