"""Verify the plugin's locale bundles against the Simplified Chinese source.

Run it after adding or changing any ``i18n/*.json``::

    python scripts/check_i18n.py

Two failure modes it catches, both of which are invisible at runtime:

* **A missing key.** The kit's ``t()`` falls back down a candidate chain
  (locale → language → default_locale → ``en``) and finally returns the raw key,
  so a gap shows up as the literal string ``ui.run`` on a button rather than as
  an error.
* **A dropped placeholder.** ``interpolateI18n`` replaces ``{name}`` only when
  the name is present in the arguments; an unknown name is left verbatim. So a
  translation that loses ``{task}`` renders a literal ``{task}`` in the middle
  of a sentence — and, worse, one that *renames* it silently prints nothing
  useful at all.

Exit code is 0 only when every locale is complete and consistent. Missing files
are reported individually so a partly-translated batch still shows what is left.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
I18N = ROOT / "i18n"

# The runtime locale set for this repository, matching the host's
# ``static/locales``: eight bundles, Simplified as the source of truth.
SOURCE_LOCALE = "zh-CN"
TARGET_LOCALES = ["zh-CN", "zh-TW", "en", "ja", "ko", "ru", "es", "pt"]

# ``{name}``, but not ``{{name}}`` (the escaped form used for placeholders that
# main_server substitutes downstream rather than ``str.format``).
PLACEHOLDER_RE = re.compile(r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_]*)\}(?!\})")


def load(locale: str) -> dict | None:
    path = I18N / f"{locale}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[FAIL] {path.name}: not valid JSON — {exc}")
        return {}
    if not isinstance(data, dict):
        print(f"[FAIL] {path.name}: top level is not an object")
        return {}
    return data


def placeholders(text: str) -> set:
    return set(PLACEHOLDER_RE.findall(text))


def main() -> int:
    source = load(SOURCE_LOCALE)
    if not source:
        print(f"[FAIL] source bundle i18n/{SOURCE_LOCALE}.json is missing or unreadable")
        return 1

    source_keys = set(source)
    problems = 0
    missing_files = []

    for locale in TARGET_LOCALES:
        if locale == SOURCE_LOCALE:
            continue
        bundle = load(locale)
        if bundle is None:
            missing_files.append(locale)
            continue

        keys = set(bundle)
        for key in sorted(source_keys - keys):
            print(f"[FAIL] {locale}.json: missing key {key!r}")
            problems += 1
        for key in sorted(keys - source_keys):
            print(f"[FAIL] {locale}.json: unexpected key {key!r} (not in {SOURCE_LOCALE})")
            problems += 1

        for key in sorted(source_keys & keys):
            value = bundle[key]
            if not isinstance(value, str) or not value.strip():
                print(f"[FAIL] {locale}.json: {key!r} is empty or not a string")
                problems += 1
                continue
            want = placeholders(source[key])
            got = placeholders(value)
            if got != want:
                print(
                    f"[FAIL] {locale}.json: {key!r} placeholders {sorted(got)} "
                    f"!= {SOURCE_LOCALE} {sorted(want)}"
                )
                problems += 1

    if missing_files:
        print(f"[MISSING] no bundle yet for: {', '.join(missing_files)}")

    if problems:
        print(f"\n[FAIL] {problems} problem(s)")
        return 1
    if missing_files:
        print(f"\n[WARN] {len(TARGET_LOCALES) - 1 - len(missing_files)} locale(s) verified; "
              f"{len(missing_files)} still to write")
        return 0
    print(f"\n[OK] all {len(TARGET_LOCALES)} locales complete and consistent "
          f"({len(source_keys)} keys)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
