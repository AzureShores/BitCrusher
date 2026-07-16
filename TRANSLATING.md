# Translating BitCrusher

You don't need to know Python to help. A translation is one JSON file.

## How it works

Every language pack lives in `i18n/` — one file per language, e.g. `i18n/fr.json`.
Each file maps a key to the text shown in the app:

```json
{
  "__code__": "fr",
  "__name__": "French",
  "__note__": "Translate values, keep keys unchanged.",
  "menu.file": "Fichier",
  "btn.estimate": "Estimate"
}
```

**Translate the values on the right. Never change the keys on the left.**

Any value still in English just hasn't been translated yet — the app falls back
to English for those, so a partial translation is fine and useful. Ship what you
have; someone else can finish it.

## Contributing a translation

1. Open `i18n/<code>.json` for your language (e.g. `i18n/de.json` for German).
   If your language has no file, copy `i18n/es.json`, rename it, and set
   `__code__` / `__name__`.
2. Translate the values. Leave anything you're unsure about in English.
3. Send a pull request.

To preview your work: put the same file in `user_settings/i18n/` and restart the
app — user packs override bundled ones, so you can test without touching the
repo copy. Pick your language from the **Language** menu; it shows a coverage
percentage per language. **Language Manager...** in that menu lists every pack
with its coverage and lets you reload without restarting.

## Rules

- Keep the keys exactly as they are.
- Keep placeholders and punctuation that carry meaning (`{}`, `%`, `:`, `...`).
- Keep it short. UI text sits in buttons and labels — a translation twice as long
  as the English may not fit.
- Plain text only, no emoji.
- Don't machine-translate a whole file and send it untouched. A small, correct,
  human-checked translation beats a full machine-translated one.

## For maintainers

After adding new UI strings, refresh every template so translators see the new
keys (existing translations are preserved):

```python
from ui.i18n import _export_lang_templates, _bundled_i18n_dir, LANG_CODES
_export_lang_templates([c for c, _ in LANG_CODES if c != "en"],
                       outdir=_bundled_i18n_dir())
```

English (`LANG_BUILTIN["en"]` in `ui/i18n.py`) is the source of truth for keys.
