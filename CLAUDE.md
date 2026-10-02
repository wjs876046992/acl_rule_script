# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

`ios_rule_script` (a.k.a. "Rules And Scripts") is a **content/data repository**, not an application. It publishes proxy-tool rules, rewrite rules, and automation scripts, all consumed over HTTP via `raw.githubusercontent.com` links (plus CDN/GHProxy mirrors documented in every generated README). All output files are generated elsewhere (the external "RULE GENERATOR 规则生成器", not present here) and committed directly; there is no build system, package manager, or test suite.

This working copy is a **fork** (`wjs876046992/acl_rule_script`) of upstream `blackmatrix7/ios_rule_script`. It is kept in sync entirely by GitHub Actions — nothing local is required:

- `.github/workflows/sync-upstream.yml` runs daily at `0 19 * * *` UTC (**03:00 Beijing time, UTC+8**) and on manual dispatch. It hard-resets the fork's `master` to upstream's and force-pushes, so local history and upstream history are the only two lineages that matter.
- The workflow preserves files that upstream does not have, listed in the `PRESERVE` env var (`CLAUDE.md`, the workflow itself, `.github/scripts`, `.github/custom-rules`). **Any commit made locally to `master` is discarded by the next run unless the file is added to `PRESERVE`** — this is a mirror, not a place for local commits.
- **Local additions to generated rule files must go through `.github/custom-rules/`.** Editing `rule/...` directly does not survive the daily reset, because upstream regenerates those files and does not contain the additions. Instead, put domains in `.github/custom-rules/<Ruleset>.domains` (one per line, `#` comments allowed); `apply_custom_rules.py` re-applies them to every platform copy (`Surge`, `Loon`, `Shadowrocket`, `QuantumultX`, `Clash`) after each sync, rewriting header counts and keeping each type group sorted. It is idempotent and skips names already covered by an existing `DOMAIN-SUFFIX`. Run it locally with `python3 .github/scripts/apply_custom_rules.py` after adding domains.
- Because of the force-push, a local checkout will diverge after each run. Resync with `git fetch origin && git reset --hard origin/master` rather than merging. Set `git config pull.ff only` expectations accordingly; never `git pull` into a diverged local `master`.
- The fork is currently a 1-commit shallow clone; the workflow uses `fetch-depth: 1` on both sides and does not need history.

Because consumers hotlink the files, **paths and filenames are a public API**: moving or renaming a committed file breaks other people's configs.

## Layout and architecture

```
source/     hand-maintained inputs (domain lists, rewrite sources, JS bodies) — never referenced directly by users
rule/       generated rule sets, one directory per tool: Surge, Clash, Loon, QuantumultX, Shadowrocket, AdGuard
rewrite/    generated rewrite/URL-rewrite sets, per tool: Surge, Loon, QuantumultX, Shadowrocket, Stash
script/     MagicJS-based automation scripts (check-in, cookie capture, ad removal) + subscriptions
external/   mirrored third-party resources (TikTok unlock, TestFlight) kept per tool
icon/       one-way backup of upstream icon sets to keep hotlinked image URLs alive
blank/      empty placeholder files used as remote Mock-module targets in Surge
```

`source/` → `rule/` + `rewrite/` is the core data flow. Both generated trees carry the same logical rule-set names (e.g. `115`, `Advertising`, `Twitter`), so the per-tool directories act as parallel projections of the same source data:

- `rule/<Tool>/<Name>/<Name>.list|.yaml|.txt` plus a generated `README.md` with rule counts, update timestamp, and the list of raw/CDN/GHProxy URLs.
- Clash gets both `<Name>.yaml` (with `_No_Resolve.yaml` variant); AdGuard is `.txt` (adblock syntax); the rest are `.list`.
- Surge/Clash/Loon/QuantumultX/Shadowrocket rule sets share content but differ in syntax — regenerate all affected tools rather than hand-editing one. Entry counts are not identical across tools (Surge, Clash, Loon, QuantumultX, Shadowrocket each differ slightly in which rule sets they carry).

Generated files start with a metadata header (`# NAME:`, `# AUTHOR: blackmatrix7`, `# REPO:`, `# UPDATED:`, `# DOMAIN:`/`# DOMAIN-SUFFIX:`/`# TOTAL:` counts). Keep that format; the generator parses it. Chinese per-tool READMEs are also generated and should not be hand-edited.

### scripts (`script/`)

Scripts are written against the **MagicJS 2/3** framework (3 is current, 2 is unmaintained; both are documented in `script/README.md`). They run inside iOS proxy clients or NodeJS. A single JS body (e.g. `luka_signin.js`) is distributed through one small wrapper file per client, all pointing at the same `raw.githubusercontent.com` URL:

- `*.sgmodule` (Surge / Shadowrocket), `*.lnplugin` / `*.lnscript` (Loon), `*.qxrewrite` (QuantumultX), `*.stoverride` (Stash), `*.snippet`.

Wrapper files generally contain absolute raw URLs, cron expressions, and MITM hostnames, and are named `*_signin`/`*_checkin` for cron scripts and `*_cookie` in tags. `script/gallery.json` (QuantumultX Gallery) and `script/boxjs.json` (BoxJS app/keys/settings subscription) index these scripts; adding a script means updating whichever of those lists applies. `script/archive/` holds retired scripts kept online for existing users; `script/startup/` is the cached-splash-ad remover.

The index files are hand-maintained and lag behind file moves: `script/gallery.json` still points at pre-archive paths such as `script/dingdong/`, `script/famijia/`, `script/manmanbuy/`, `script/smzdm/` (actual files are under `script/archive/`) and at a `script/master/` directory that does not exist in the tree. After moving or renaming a script, grep both JSON files for stale paths rather than trusting them.

MagicJS conventions when editing scripts:
- Persistence goes through the MagicJS storage pool on iOS, or a `magic.json` file next to the script on NodeJS/青龙面板 (see `script/README.md`). Multi-account values are JSON objects tagged with `"magic_session": true` so the framework can distinguish session storage from a plain object.
- Shared keys: `magic_bark_url` (push) and `magic_loglevel` (default `INFO`; `DEBUG` for troubleshooting). Script-specific keys are listed in each script's README variable table; those tables are the contract with BoxJS `keys`/`settings`.
- Scripts must stay readable and unencrypted on purpose — `script/README.md` explicitly forbids obfuscated/minified scripts for security reasons. Minified variants (e.g. `*_min.js`) exist only for the few historical distributions and gist-hosted copies.

### external/, icon/, blank/

`external/` is a curated backup of other projects' configs, organized `<Tool>/<Topic>/<Variant>/` with the topic's own README crediting upstream. `icon/` is an append-only mirror of upstream icon repos; `icon/icon.json` is the generated icon gallery index. `blank/` files back Surge mock modules. Do not delete existing files from these trees — their URLs are embedded in third-party configs.

## Working in this repo

- Regeneration is done by an external tool; `.gitignore` names its artifacts (`replaceurl.js`, `to_blackmatrix7.js`, `magic.json`, `debug/`, `script/magicjs/server/`), so those local files are expected to be present but untracked.
- Rule-set edits belong in `source/rule/<Name>/<Name>.list` (or `source/rewrite/<Name>/` for rewrites), not in the generated trees.
- Verify syntax per target tool when touching generated files: `.list` for Surge-family, `payload:` YAML for Clash, adblock syntax for AdGuard, `[URL Rewrite]`/regex `- reject` entries for Surge `[General]` rewrite modules.
- Validate JS syntax before committing scripts, e.g. `node --check script/<name>/<file>.js`; there are no other automated checks.
- Commit messages in this repo are plain dates/timestamps; PRs and issues use the templates in `.github/ISSUE_TEMPLATE/`.
