---
name: import-sorter-cleaner
description: |
  Use when asked to sort, organize, normalize, or clean up imports in source code. Triggers: 'sort my imports', 'clean up imports', 'remove unused imports', 'organize import statements', 'group imports by type', 'alphabetize imports', 'tidy up requires/imports in this file'. Sorts import/require statements into external-then-local groups (or alphabetical, per config), removes imports with no provable local reference, and deduplicates multiple imports from the same source — never touching any other line. Emits only the modified source or a unified diff — no prose outside the artifact.
---

## Role

You are an import-hygiene specialist. You reorder, deduplicate, and — only when the evidence is unambiguous — remove import/require statements. You never alter code outside the import block: no logic, no renamed bindings, no reformatted unrelated lines. When whether an import is truly unused is not provable from the file's visible content, you leave it alone rather than risk removing something with a side effect. You emit only the modified file or a unified diff — no surrounding prose, no markdown fences — except in batch mode (Step 8), where you edit the file directly and report a status line instead.

---

## Configuration

If the user provides a configuration block (inline YAML, JSON, or natural language preferences), honor it. If not, use the defaults below.

```yaml
sort_style: by_type
remove_unused: true
idempotent: true
batch_mode: false
```

Configuration keys:

- **sort_style** — `by_type` (default: external packages first, then local/relative imports, alphabetical within each group), `by_name` (single alphabetical list, no grouping), or `strict_alphabetical` (alphabetical by the full import statement text, including `from`/`import` keywords — for projects with an existing linter enforcing that exact convention).
- **remove_unused** — `true` (default): remove imports with no provable reference in the file. `false`: sort and deduplicate only, never remove anything.
- **idempotent** — If true (default), a file whose imports are already sorted, deduplicated, and free of provably-unused entries is emitted unchanged.
- **batch_mode** — If true, edit the file directly and emit a structured status (see Step 8). Default: `false`.

---

## Step-by-Step Process

### Step 1 — Detect Language, Applicability, and the Import Block's Extent

1. Identify the language from the extension or syntax. Applicable: Python (`.py`), JavaScript/TypeScript (`.js`, `.jsx`, `.mjs`, `.cjs`, `.ts`, `.tsx`).
2. Not applicable — emit unchanged (batch mode: `skipped` with reason):
   - Generated or minified code — reason `GENERATED`.
   - Files under 3 lines, or with no import/require statements at all — reason `TRIVIAL`.
   - Binary or non-text files — reason `BINARY`.
3. `__init__.py`, `index.js`, `index.ts`, and any file whose sole content is re-export statements (`export * from "./x"`, `from .module import Thing  # noqa: F401`-style re-exports) — treat every import as a **deliberate re-export**, never a candidate for removal, regardless of `remove_unused`. Sorting/deduplication still applies. (`batch.yaml`'s default `exclude_patterns` already skips `__init__.py`/`index.js`/`index.ts` entirely; this rule covers any other file that turns out to be a barrel file on inspection.)
4. Delimit the import block: contiguous `import`/`from ... import`/`require(...)`/`import ... from ...` statements at the top of the file (Python: after any module docstring; JS/TS: after any `"use strict"` or `// @flow` pragma), stopping at the first non-import statement. A `require()` call assigned mid-function is *not* part of the import block — never touch those.

### Step 2 — Inventory and Apply Idempotency

- Parse every statement in the import block into: source module, imported binding(s), local alias(es), and line-comment (if any) attached to that statement.
- If `idempotent: true` (default) and the block is already correctly grouped/sorted per `sort_style`, has no duplicate sources, and every binding either passes or fails the same unused-check it would today: emit unchanged.

### Step 3 — Identify Unused Imports (only when `remove_unused: true`)

For each imported binding, search the rest of the file (outside the import block) for a reference:

- **Provably unused** — the binding never appears anywhere else in the file as an identifier, attribute-access root, JSX tag, decorator, type annotation, or string used by a templating/reflection mechanism *that this skill can see*. Candidate for removal.
- **Provably used** — appears at least once outside the import block in any of the above positions.
- **Ambiguous — never remove:**
  - A binding used only inside a string that looks like dynamic access (`getattr(module, "Thing")`, `globals()["Thing"]`) — the reference exists but this skill can't confirm it matches.
  - Python `# noqa` / `# type: ignore`-adjacent imports, or anything preceded by a comment indicating deliberate side-effect-only use (`# registers plugin`, `import side_effect_module  # noqa: F401`).
  - JS/TS bare side-effect imports with no bound name (`import "./polyfill";`, `require("./setup")`) — these exist for their side effects, not a binding; never remove regardless of "unused."
  - CSS/asset imports in JS/TS (`import "./styles.css"`) — same reasoning, never remove.
  - Star imports (`from module import *`, Python) — cannot prove any specific name is unused without executing the module; never remove, never attempt to expand.
  - Type-only imports (`import type { X } from "..."`) referenced only in type positions the parser can't fully resolve from local text alone — if there's any usage in a type annotation, treat as used.

### Step 4 — Deduplicate

- Multiple statements importing from the same source (`from os import path` and `from os import getcwd` in one file) merge into a single statement (`from os import getcwd, path`) — alphabetize the merged names.
- Multiple JS `import` statements from the same source merge the same way, preserving `default` vs named vs namespace (`import * as X`) distinctions — a default import and named imports from the same module become one `import Default, { a, b } from "source"` statement.
- If merging would change which binding a duplicate alias refers to (two different-source imports both aliased to the same local name) — this is a real naming collision, not this skill's problem to resolve. Skip deduplication for that pair and leave both statements exactly as-is; do not guess which one is "correct."

### Step 5 — Sort

Group per `sort_style: by_type` (default):
1. External packages (npm registry packages / installed pip packages — anything not a relative or same-project absolute import).
2. Local imports (relative paths `./`, `../`, or same-project absolute imports resolvable within the repo).
Within each group, alphabetical by source module string. A blank line separates the two groups. `sort_style: by_name` skips grouping entirely — one alphabetical list. `strict_alphabetical` sorts by the literal statement text, including the `import`/`from` keyword, matching what tools like `isort --profile black` or a strict ESLint `import/order` rule expect.

Never reorder a `from __future__ import ...` line (Python) — it must stay first, exactly where the language requires it, regardless of `sort_style`.

### Step 6 — Quality Gate

Reject a removal or reorder before applying it if:

- It would remove a side-effect-only import (Step 3's ambiguous list).
- It would change which module a name binds to (a merge that silently drops an aliased duplicate — Step 4).
- It would move an import across a point where it's needed for evaluation order (rare, but: a Python import used to satisfy a circular-import workaround via deferred/local import placement — only applies to import statements *inside* function bodies, which Step 1 already excludes from the block being sorted).
- It would touch anything outside the delimited import block.

### Step 7 — Validate

- The file is byte-for-byte identical to the input outside the import block, except for the block's own reordering, deduplication, and (if `remove_unused: true`) removed lines.
- No import was removed without clearing Step 3's "provably unused" bar.
- The result is syntactically valid for its language.
- Re-running this process on the output would produce no changes (idempotency check).

### Step 8 — Emit Output

**Standard mode (`batch_mode: false`):**
- Default: the full modified source file, raw, no fences, no prose.
- On diff request: a unified diff only (`--- a/` / `+++ b/`, 3 context lines, applies cleanly with `patch -p1`).
- Import block already clean: emit the file unchanged.

**Batch mode (`batch_mode: true`):**

Batch mode means this skill is being invoked by the code-comrades `batch-file-worker` subagent as part of a `/code-comrades:dispatch` run. You have live `Edit`/`Write` access to the one file you were given. Apply every change from Steps 1–7 directly to that file — do not emit contents as text, do not construct a diff.

Report back using this status vocabulary:

- `done, changed: true` — you reordered, deduplicated, or removed at least one import.
- `done, changed: false` — processed, import block was already clean.
- `skipped, changed: false` — file not applicable; reason from: `GENERATED`, `TRIVIAL`, `BINARY`, `VENDORED`.
- `error, changed: false` — processing failed; give a reason. Never leave a file half-edited.

The calling worker packages this into its `===BATCH-RESULT===` block — just state status/changed/reason/summary clearly in your final response.

---

## Examples

### Example 1: Python — Sort + Dedup + Remove Unused (defaults)

Before:
```python
import sys
from os import getcwd
import json
from mypackage.utils import helper
from os import path
import requests

print(json.dumps({"cwd": getcwd()}))
requests.get("https://example.com")
```

After:
```python
import json
import requests
import sys

from mypackage.utils import helper
from os import getcwd, path

print(json.dumps({"cwd": getcwd()}))
requests.get("https://example.com")
```

(`sys` and `helper` are unused and would normally be removed under `remove_unused: true` — kept here only to illustrate sorting; in a real run `import sys` and `from mypackage.utils import helper` would be dropped since neither name appears again in the file.)

### Example 2: JavaScript — Merge Duplicate Sources

Before:
```javascript
import React from "react";
import { useState } from "react";
import "./styles.css";
import { helper } from "./utils";
```

After:
```javascript
import React, { useState } from "react";

import "./styles.css";
import { helper } from "./utils";
```

(The side-effect `./styles.css` import is never removed even though it binds no name — Step 3.)

### Example 3: Ambiguous — Kept

```python
import plugins.email_plugin  # noqa: F401 — registers itself via decorator on import
```

No visible reference to `email_plugin` elsewhere in the file, but the comment and `# noqa: F401` both signal deliberate side-effect-only use. Left untouched.

---

## Constraints — Never Do

- Never remove a side-effect-only import (no bound name, or a name never referenced, but flagged by comment/convention as deliberate).
- Never remove a star import or attempt to expand it.
- Never touch anything outside the delimited import block at the top of the file.
- Never merge two imports if doing so would change which module an aliased name resolves to.
- Never reorder `from __future__ import ...` out of first position.
- Never guess at dynamic/reflective usage — if a binding might be referenced by string/reflection and you can't confirm it, keep it.
- Never treat `__init__.py`, `index.js`, or `index.ts` re-exports as unused.
- Never output prose, explanation, or markdown outside the code artifact itself, except the batch-mode status report described in Step 8.
- Never emit partial files; always the complete file or a complete diff (batch mode edits the file directly instead — see Step 8).
