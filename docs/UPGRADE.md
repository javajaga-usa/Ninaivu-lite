# Moving from Ninaivu Lite to Ninaivu

This is **optional**. Ninaivu Lite is complete on its own; move up only if you want what the
full [Ninaivu](https://github.com/javajaga-usa/Ninaivu) adds (faces, search by description,
cloud and phone backup, editing).

Lite never changes your photos, so both can read the same folders. What moves is only what
the household *made* in Lite: people, who sees what, favourites, albums and share links.

## 1. Write the export file

Close Ninaivu Lite, then in its folder:

```bash
python -m ninaivu_lite --export lite-export.json
```

(Use `--data DIR` too if you started Lite with a different data folder.)
The file holds no photos and no previews; it is usually a few hundred kilobytes.
It **does** hold password and PIN hashes, so keep it private and delete it after the move.

## 2. Give it to Ninaivu

Install Ninaivu, add the **same photo folders**, and let it finish its first scan, so it knows
every file. Then import `lite-export.json` there. Photos are matched by folder and the path
inside it, so a file moved or renamed in between is reported, not guessed.

## 3. The format (for Ninaivu's importer)

One UTF-8 JSON object. `format` is `"ninaivu-lite-export"`; `format_version` is `1`.
Within a version, fields are only ever added, never changed in meaning. A photo is named by
a **reference**: `{"folder": "<library folder, absolute>", "path": "<path inside it, / separated>"}`.

| Field | Meaning |
| --- | --- |
| `app_version`, `exported_at` | Lite's version; Unix time of the export |
| `folders`, `default_folder` | library folders (absolute paths), and the one shown first |
| `default_language` | `"en"` or `"ta"` |
| `house_name`, `open_browsing` | the home's name; whether visitors may browse public photos without signing in |
| `people[]` | `username`, `name`, `role` (`admin`/`family`/`guest`), `password` + `password_scheme`, `pin` + `pin_scheme`, `library` (the one folder this person sees, absolute, or `null` for all), `color`, `language`, `home_label`, `active`, `must_change`, `created_at`, `last_login`, `created_by` (username) |
| `folder_rules[]` | `folder`, `path` (inside it; `""` is the whole folder), `level`, `created_at` — a rule covers that folder and everything below it, files added later included |
| `visibility[]` | reference + `level` + `source` (`"rule"` or `"item"`), only where different from the default (Family) |
| `favourites[]` | reference + `user` + `added_at` |
| `albums[]` | `id`, `name`, `owner`, `created_at`, `cover` (reference or `null`), `items[]` (reference + `added_at`) |
| `shares[]` | `token`, `kind` (`asset`/`album`), `owner`, `password` (hash or `null`), `expires_at`, `created_at`, `views`; plus a reference for a photo or `album_id` for an album |

**Levels:** `0` Public, `1` Family, `2` Hidden — the same numbers Ninaivu uses.

**Password and PIN hashes:** `scrypt$<n>$<r>$<p>$<salt>$<hash>`, the format Ninaivu verifies,
so nobody needs a new password. On a Python without scrypt, Lite falls back to
`pbkdf2_sha256$…`. The `*_scheme` field says which. Ask anyone with a scheme your Ninaivu does
not know to set a new password after the move.

**Share tokens** are kept, so a link already sent keeps working if Ninaivu answers on the same
address and port.
