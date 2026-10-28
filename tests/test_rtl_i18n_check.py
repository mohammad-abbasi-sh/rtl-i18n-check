#!/usr/bin/env python3
"""Tests for skills/rtl-i18n-check/scripts/rtl_i18n_check.py.

Standard library only (unittest). No network: every check runs on
in-memory strings or on the fixture locale files in tests/fixtures/.
The fixtures encode real bugs from the research: the March 2026
Medium story (Arabic greetings backwards, unisolated {userName}),
listmonk losing {name} in five Arabic messages, Dashy translating
{theme} to {थीम}, the PKP yeh/kaf search bug, and the Arabic plural
rule that zero/one/two forms must not repeat {{count}}.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                       / "skills" / "rtl-i18n-check" / "scripts"))

import rtl_i18n_check as ric  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "skills" / "rtl-i18n-check" / "scripts" / "rtl_i18n_check.py"
FIX = ROOT / "tests" / "fixtures"

FSI, PDI, ZWNJ = "\u2068", "\u2069", "\u200c"


def codes(findings):
    return sorted((f["code"], f["key"]) for f in findings)


def by_code(findings, code):
    return [f for f in findings if f["code"] == code]


class TestIsolation(unittest.TestCase):
    def check(self, value, lang="fa"):
        return ric.check_string(value, lang, "t.json", "k")

    def test_latin_name_between_rtl(self):
        found = self.check("سلام John، چطوری؟")
        self.assertEqual([f["code"] for f in found], ["RTL-01"])
        self.assertIn("latin", found[0]["message"])

    def test_url_between_rtl(self):
        found = self.check("به https://example.com مراجعه کنید")
        self.assertEqual(len(by_code(found, "RTL-01")), 1)
        self.assertIn("url", found[0]["message"])

    def test_email_between_rtl(self):
        found = self.check("با ما در info@example.com تماس بگیرید")
        self.assertEqual(len(by_code(found, "RTL-01")), 1)

    def test_placeholder_between_rtl(self):
        found = self.check("سلام {userName}، خوش آمدید")
        self.assertEqual(len(by_code(found, "RTL-01")), 1)

    def test_currency_number(self):
        found = self.check("قیمت $5 است")
        self.assertEqual(len(by_code(found, "RTL-01")), 1)
        self.assertIn("currency-number", found[0]["message"])

    def test_pure_digits_between_rtl_are_fine(self):
        # Per the Unicode bidi algorithm, a digit run between RTL words
        # orders correctly; only digits touching Latin text or a
        # currency symbol misrender.
        self.assertEqual(self.check("شما 3 پیام جدید دارید"), [])
        self.assertEqual(self.check("قیمت 250 تومان است"), [])

    def test_already_isolated_is_fine(self):
        self.assertEqual(
            self.check(f"سلام {FSI}{{userName}}{PDI}، خوش آمدید"), [])
        self.assertEqual(
            self.check("سلام <bdi>{userName}</bdi>، خوش آمدید"), [])

    def test_html_tags_are_not_islands(self):
        self.assertEqual(self.check("سلام <b>متن</b> خداحافظ"), [])

    def test_latin_only_string_is_fine(self):
        self.assertEqual(self.check("Hello World"), [])

    def test_icu_syntax_does_not_false_positive(self):
        self.assertEqual(self.check(
            "{count, plural, one {یک پیام} other {# پیام}}"), [])
        self.assertEqual(self.check(
            "{count, plural, zero {لا رسائل} one {رسالة واحدة} "
            "two {رسالتان} few {# رسائل} many {# رسالة} "
            "other {# رسائل}}", lang="ar"), [])

    def test_merged_latin_run(self):
        found = self.check("سلام Hello World خداحافظ")
        self.assertEqual(len(by_code(found, "RTL-01")), 1)


class TestZwnjAndVariants(unittest.TestCase):
    def check(self, value, lang="fa"):
        return ric.check_string(value, lang, "t.json", "k")

    def test_mi_space(self):
        found = self.check("من می خواهم کتاب بخوانم")
        self.assertEqual([f["code"] for f in found], ["RTL-02"])

    def test_plural_space(self):
        found = self.check("کتاب ها روی میز است")
        self.assertEqual([f["code"] for f in found], ["RTL-02"])

    def test_correct_zwnj(self):
        self.assertEqual(self.check(f"من می{ZWNJ}خواهم کتاب بخوانم"), [])
        self.assertEqual(self.check(f"کتاب{ZWNJ}ها روی میز است"), [])

    def test_arabic_yeh_kaf_in_persian(self):
        found = self.check("اين كتاب جديد است")
        self.assertEqual(len(by_code(found, "RTL-03")), 1)
        self.assertIn("U+064A", found[0]["message"])
        self.assertIn("U+0643", found[0]["message"])

    def test_persian_yeh_kaf_in_arabic(self):
        found = self.check("مرحبا، این کتاب برای شما است", lang="ar")
        self.assertEqual(len(by_code(found, "RTL-03")), 1)

    def test_urdu_keeps_persian_letters(self):
        # Urdu uses گ چ پ ژ and Persian-form yeh/kaf; only Arabic ي/ك
        # are wrong there.
        self.assertEqual(self.check("یہ کتاب اچھی ہے", lang="ur"), [])
        found = self.check("اين كتاب اچھا ہے", lang="ur")
        self.assertEqual(len(by_code(found, "RTL-03")), 1)

    def test_no_zwnj_check_for_arabic(self):
        self.assertEqual(
            self.check("أريد أن أذهب إلى المدرسة", lang="ar"), [])


class TestDigitsControlsPlaceholders(unittest.TestCase):
    def check(self, value, lang="fa"):
        return ric.check_string(value, lang, "t.json", "k")

    def test_mixed_digit_scripts(self):
        found = self.check("شما ۵ امتیاز و 3 ستاره دارید")
        self.assertEqual([f["code"] for f in found], ["RTL-04"])

    def test_single_digit_script(self):
        self.assertEqual(self.check("شما ۵ امتیاز دارید"), [])
        self.assertEqual(self.check("You have 5 points"), [])

    def test_rlo_override(self):
        found = self.check("متن \u202eپنهان")
        self.assertEqual([f["code"] for f in found], ["RTL-06"])

    def test_unclosed_isolate(self):
        found = self.check(f"متن {FSI}بدون بستن")
        self.assertEqual([f["code"] for f in found], ["RTL-06"])

    def test_unmatched_pdi(self):
        found = self.check(f"متن {PDI}بدون بازکننده")
        self.assertEqual([f["code"] for f in found], ["RTL-06"])

    def test_balanced_isolates(self):
        self.assertEqual(
            self.check(f"متن {FSI}John{PDI} ادامه"), [])

    def test_translated_placeholder_name(self):
        # Dashy translated {theme} to {थीम}; same bug class.
        found = self.check("سلام {نام}، چطوری؟")
        self.assertEqual([f["code"] for f in found], ["RTL-07"])

    def test_valid_placeholder_names(self):
        # Isolated placeholders (FSI…PDI) with valid ASCII names.
        self.assertEqual(self.check(
            f"سلام {FSI}{{name}}{PDI}، چطوری؟"), [])
        self.assertEqual(self.check(
            f"مورد {FSI}{{0}}{PDI} از {FSI}{{1}}{PDI}"), [])
        self.assertEqual(self.check(
            f"مورد {FSI}%s{PDI} است"), [])

    def test_placeholder_vs_source(self):
        found = ric.check_placeholders_vs_source(
            "سلام {نام}، چطوری؟", "Hello {name}, how are you?",
            "t.json", "k")
        self.assertEqual(len(found), 1)
        self.assertIn("name", found[0]["detail"])
        self.assertEqual(ric.check_placeholders_vs_source(
            "سلام {name}، چطوری؟", "Hello {name}, how are you?",
            "t.json", "k"), [])


class TestPlurals(unittest.TestCase):
    def check(self, value, lang="fa"):
        return ric.check_string(value, lang, "t.json", "k")

    def test_fa_icu_complete(self):
        self.assertEqual(self.check(
            "{count, plural, one {یک مورد} other {# مورد}}"), [])

    def test_fa_icu_missing_one(self):
        found = self.check("{count, plural, other {# مورد}}")
        self.assertEqual([f["code"] for f in found], ["RTL-05"])
        self.assertIn("one", found[0]["message"])

    def test_ar_icu_needs_six(self):
        found = self.check(
            "{count, plural, one {رسالة واحدة} other {# رسائل}}",
            lang="ar")
        self.assertEqual([f["code"] for f in found], ["RTL-05"])
        for missing in ("zero", "two", "few", "many"):
            self.assertIn(missing, found[0]["message"])

    def test_ar_zero_must_not_repeat_count(self):
        found = self.check(
            "{count, plural, zero {# رسائل} one {رسالة واحدة} "
            "two {رسالتان} few {# رسائل} many {# رسالة} "
            "other {# رسائل}}", lang="ar")
        self.assertEqual([f["code"] for f in found], ["RTL-05"])
        self.assertIn("zero", found[0]["message"])

    def test_ar_icu_complete(self):
        self.assertEqual(self.check(
            "{count, plural, zero {لا رسائل} one {رسالة واحدة} "
            "two {رسالتان} few {# رسائل} many {# رسالة} "
            "other {# رسائل}}", lang="ar"), [])

    def test_i18next_group_missing_other(self):
        entries = {"alerts_one": "یک هشدار"}
        found = ric.check_entries(entries, {}, "fa", "t.json")
        self.assertEqual(codes(found), [("RTL-05", "alerts")])

    def test_i18next_group_complete(self):
        entries = {"items_one": "یک پیام",
                   "items_other": "{count} پیام"}
        found = ric.check_entries(entries, {}, "fa", "t.json")
        self.assertEqual([f for f in found if f["code"] == "RTL-05"], [])

    def test_icu_base_is_not_an_i18next_group(self):
        # "items" (ICU) + "items_few" share a prefix but are two
        # different plural systems; no spurious i18next finding.
        entries = {
            "items": "{count, plural, one {یک مورد} other {# مورد}}",
            "items_few": "{count, plural, other {# مورد}}",
        }
        found = ric.check_entries(entries, {}, "fa", "t.json")
        self.assertEqual(codes(found), [("RTL-05", "items_few")])


class TestLoaders(unittest.TestCase):
    def test_json_nested_and_arb(self):
        entries, plurals, err = ric.load_locale_file(FIX / "fa-broken.json")
        self.assertIsNone(err)
        self.assertEqual(entries["greeting"],
                         "سلام {userName}، به برنامه خوش آمدید")
        entries, _, err = ric.load_locale_file(FIX / "fa.arb")
        self.assertIsNone(err)
        self.assertNotIn("@greeting", entries)
        self.assertNotIn("@@locale", entries)
        self.assertEqual(entries["nested.deep.key"], "متن تو در تو")

    def test_android_xml(self):
        entries, plurals, err = ric.load_locale_file(
            FIX / "values-fa" / "strings.xml")
        self.assertIsNone(err)
        self.assertEqual(entries["greeting"],
                         "سلام {userName}، به برنامه خوش آمدید")
        self.assertEqual(plurals["items"],
                         {"one": "یک مورد", "other": "%d مورد"})

    def test_po(self):
        entries, plurals, err = ric.load_locale_file(FIX / "fa.po")
        self.assertIsNone(err)
        self.assertEqual(entries["greeting"],
                         "سلام {userName}، به برنامه خوش آمدید")
        self.assertEqual(plurals["one item"],
                         {0: "یک مورد", 1: "%d مورد"})

    def test_yaml(self):
        entries, _, err = ric.load_locale_file(FIX / "fa.yml")
        self.assertIsNone(err)
        self.assertEqual(entries["fa.nested.deep_key"], "متن تو در تو")
        self.assertEqual(entries["fa.greeting"],
                         "سلام {userName}، به برنامه خوش آمدید")

    def test_unsupported_format(self):
        with tempfile.NamedTemporaryFile(
                suffix=".txt", mode="w", delete=False) as tmp:
            tmp.write("hello")
            name = tmp.name
        _, _, err = ric.load_locale_file(name)
        self.assertIn("unsupported format", err)

    def test_detect_lang_from_android_dir(self):
        entries, _, _ = ric.load_locale_file(
            FIX / "values-fa" / "strings.xml")
        self.assertEqual(ric.detect_lang(
            FIX / "values-fa" / "strings.xml", entries), "fa")


class TestFixturesEndToEnd(unittest.TestCase):
    def load(self, name):
        return ric.load_locale_file(FIX / name)

    def test_fa_broken_with_source(self):
        entries, plurals, _ = self.load("fa-broken.json")
        src, src_pl, _ = self.load("en.json")
        found = ric.check_entries(entries, plurals, "fa",
                                  FIX / "fa-broken.json", src, src_pl)
        errors = [f for f in found if f["severity"] == "error"]
        warns = [f for f in found if f["severity"] == "warning"]
        self.assertEqual(len(errors), 15)
        self.assertEqual(len(warns), 1)
        self.assertEqual(len(by_code(found, "RTL-01")), 5)
        self.assertEqual(len(by_code(found, "RTL-02")), 2)
        self.assertEqual(len(by_code(found, "RTL-03")), 1)
        self.assertEqual(len(by_code(found, "RTL-04")), 2)  # error + warn
        self.assertEqual(len(by_code(found, "RTL-05")), 2)
        self.assertEqual(len(by_code(found, "RTL-06")), 2)
        self.assertEqual(len(by_code(found, "RTL-07")), 2)

    def test_fa_clean_with_source(self):
        entries, plurals, _ = self.load("fa-clean.json")
        src, src_pl, _ = self.load("en.json")
        found = ric.check_entries(entries, plurals, "fa",
                                  FIX / "fa-clean.json", src, src_pl)
        self.assertEqual(found, [])

    def test_ar_broken(self):
        entries, plurals, _ = self.load("ar-broken.json")
        found = ric.check_entries(entries, plurals, "ar",
                                  FIX / "ar-broken.json")
        self.assertEqual(len([f for f in found
                              if f["severity"] == "error"]), 5)
        self.assertEqual(len(by_code(found, "RTL-05")), 2)

    def test_digits_policy_pinned(self):
        entries, plurals, _ = self.load("fa-broken.json")
        found = ric.check_entries(entries, plurals, "fa",
                                  FIX / "fa-broken.json",
                                  digits_policy="fa")
        # File-level findings only (message "Digit script is ..."):
        # inbox + price + price_usd use Latin digits against a fa policy.
        pinned = [f for f in by_code(found, "RTL-04")
                  if f["message"].startswith("Digit script is")]
        self.assertEqual({f["key"] for f in pinned},
                         {"inbox", "price", "price_usd"})


class TestFix(unittest.TestCase):
    def test_fix_string(self):
        fixed, notes = ric.fix_string(
            "اين كتاب ها می شود", "fa", digits_target="fa")
        self.assertEqual(fixed,
                         f"این کتاب{ZWNJ}ها می{ZWNJ}شود")
        self.assertTrue(notes)

    def test_fix_string_wraps_islands(self):
        fixed, notes = ric.fix_string("سلام {userName}، خوش آمدید", "fa")
        self.assertEqual(fixed,
                         f"سلام {FSI}{{userName}}{PDI}، خوش آمدید")

    def test_fix_string_digits(self):
        fixed, _ = ric.fix_string("۵ و 3", "fa", digits_target="latn")
        self.assertEqual(fixed, "5 و 3")

    def test_fix_file_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "fa.json"
            src.write_text((FIX / "fa-broken.json").read_text(
                encoding="utf-8"), encoding="utf-8")
            fixed_path, notes, remaining = ric.fix_json_file(src, "fa")
            self.assertIsNotNone(fixed_path)
            self.assertTrue(notes)
            # Original untouched: still has the Arabic yeh.
            self.assertIn("\u064a", src.read_text(encoding="utf-8"))
            # Only the human-judgement checks remain.
            self.assertEqual(
                {f["code"] for f in remaining},
                {"RTL-05", "RTL-06", "RTL-07"})
            # And the fixed file itself re-checks to exactly that set.
            entries, plurals, _ = ric.load_locale_file(fixed_path)
            again = ric.check_entries(entries, plurals, "fa", fixed_path)
            self.assertEqual({f["code"] for f in again},
                             {"RTL-05", "RTL-06", "RTL-07"})


class TestCli(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args],
                              capture_output=True, text=True, timeout=60)

    def test_broken_exits_1(self):
        proc = self.run_cli(str(FIX / "fa-broken.json"),
                        "--source", str(FIX / "en.json"))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("15 error(s), 1 warning(s)", proc.stdout)

    def test_clean_exits_0(self):
        proc = self.run_cli(str(FIX / "fa-clean.json"),
                        "--source", str(FIX / "en.json"))
        self.assertEqual(proc.returncode, 0)
        self.assertIn("0 error(s), 0 warning(s)", proc.stdout)

    def test_json_output(self):
        proc = self.run_cli(str(FIX / "fa-broken.json"), "--json")
        data = json.loads(proc.stdout)
        self.assertEqual(data["counts"]["error"], 14)  # no --source
        self.assertEqual(data["tool"], "rtl-i18n-check")
        self.assertTrue(all("code" in f for f in data["findings"]))

    def test_strict_fails_on_warning(self):
        proc = self.run_cli(str(FIX / "fa-clean.json"), "--strict")
        self.assertEqual(proc.returncode, 0)
        proc = self.run_cli(str(FIX / "fa-broken.json"), "--strict")
        self.assertEqual(proc.returncode, 1)

    def test_unreadable_file_exits_2(self):
        proc = self.run_cli(str(FIX / "missing.json"))
        self.assertEqual(proc.returncode, 2)
        proc = self.run_cli(str(FIX / "fa.po"), "--lang", "xx")
        self.assertEqual(proc.returncode, 2)

    def test_fix_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "fa.json"
            src.write_text((FIX / "fa-broken.json").read_text(
                encoding="utf-8"), encoding="utf-8")
            proc = self.run_cli(str(src), "--fix",
                            "--source", str(FIX / "en.json"))
            self.assertEqual(proc.returncode, 1)  # errors remain
            fixed = Path(tmp) / "fa.fixed.json"
            self.assertTrue(fixed.exists())
            self.assertIn("6 error(s) remain", proc.stderr)
            # stdout stays clean text output (fix notes went to stderr).
            self.assertIn("15 error(s)", proc.stdout)


if __name__ == "__main__":
    unittest.main()
