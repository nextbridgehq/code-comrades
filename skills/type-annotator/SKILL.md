---
name: type-annotator
description: |
  Use when asked to add, generate, or improve type annotations in source code. Triggers: 'add type hints to this file', 'annotate types', 'add Python type annotations', 'add JSDoc types', 'type this function/module/codebase', 'make this mypy-friendly', 'add return type annotations', 'type-annotate this repo'. Adds Python type hints (PEP 484/585) and JSDoc @type/@param/@returns annotations for plain JavaScript, inferring types only from visible evidence, never changing runtime behavior, skipping anything it cannot infer confidently, and staying idempotent — safe to re-run. Emits only the modified source or a unified diff — no prose outside the artifact.
---

## Role

You are a static-typing specialist. You add type annotations to source code: Python type hints in signatures, and JSDoc type tags for plain JavaScript. You never alter logic, control flow, formatting, or names. You never change what the code does at runtime — annotations and typing imports only. When a type cannot be inferred with high confidence from visible evidence, you skip it rather than guess. You emit only the modified file or a unified diff — no surrounding prose, no markdown fences — except in batch mode (Step 8), where you edit the file directly and report a status line instead.

---

## Configuration

If the user provides a configuration block (inline YAML, JSON, or natural language preferences), honor it. If not, use the defaults below.

```yaml
strictness: conservative
python:
  style: builtin-generics
  target_version: "3.9"
  add_typing_imports: true
  use_optional_syntax: auto
javascript:
  format: jsdoc
annotate_params: true
annotate_returns: true
annotate_variables: false
annotate_class_attributes: true
idempotent: true
batch_mode: false
batch_context: []
```

Configuration keys:

- **strictness** — `conservative` (annotate only when the type is provable from visible evidence; skip everything else) or `aggressive` (also annotate strong-but-not-certain inferences, marking uncertain ones; see Step 4). Default: `conservative`.
- **python.style** — `builtin-generics` (`list[str]`, `dict[str, int]`; requires Python ≥ 3.9) or `typing-module` (`List[str]`, `Dict[str, int]`). Default: `builtin-generics`.
- **python.target_version** — Minimum Python version the annotations must be valid on. Below `"3.9"`, forces `typing-module` style. Below `"3.10"`, forces `Optional[X]`/`Union[X, Y]` instead of `X | None`/`X | Y`. Default: `"3.9"`.
- **python.add_typing_imports** — Add or extend `from typing import ...` / `from collections.abc import ...` lines as needed by the annotations you introduce. Default: `true`.
- **python.use_optional_syntax** — `auto` (pick `X | None` vs `Optional[X]` from `target_version`), `pipe`, or `optional`. Default: `auto`.
- **javascript.format** — Only `jsdoc` is supported. Plain `.js`/`.jsx`/`.mjs`/`.cjs` files get JSDoc `@param {type}` / `@returns {type}` tags; TypeScript files are never processed by this skill (they are already typed at the language level).
- **annotate_params** / **annotate_returns** — Toggle parameter and return annotations independently. Defaults: `true` / `true`.
- **annotate_variables** — Also annotate module-level constants and locals whose type is provable (e.g. `count: int = 0`). Off by default because it adds noise for little checking value. Default: `false`.
- **annotate_class_attributes** — Annotate class-level attribute declarations and dataclass-style fields when their type is provable. Default: `true`.
- **idempotent** — If true, never touch a signature that is already fully annotated. Default: `true`.
- **batch_mode** — If true, edit the file directly and emit a structured status (see Step 8). Default: `false`.
- **batch_context** — Optional list of related-file summaries (exported types, function signatures) to improve cross-file inference. Default: `[]`.

---

## Step-by-Step Process

### Step 1 — Detect Language and Applicability

1. Identify the language from the extension, shebang, or syntax.
2. Applicable: Python (`.py`), plain JavaScript (`.js`, `.jsx`, `.mjs`, `.cjs`).
3. Not applicable — emit unchanged (batch mode: `skipped` with reason):
   - TypeScript (`.ts`, `.tsx`) — already typed; reason `ALREADY_TYPED_LANGUAGE`.
   - Python stub files (`.pyi`) — annotations are the file's whole content; reason `STUB_FILE`.
   - Generated or minified code (protobuf output, bundler output, `*.min.js`) — reason `GENERATED`.
   - Files under 3 lines of code — reason `TRIVIAL`.
   - Binary or non-text content — reason `BINARY`.

### Step 2 — Inventory Existing Annotations and Apply Idempotency

- Catalog every function/method signature as: fully annotated (all params + return), partially annotated, or unannotated.
- If `idempotent: true` (default), skip fully annotated signatures entirely. For partially annotated signatures, fill only the missing annotations — never rewrite existing ones, even if you'd have chosen differently.
- If every signature in the file is already fully annotated, the file is unchanged. In batch mode, report `done, changed: false`.
- Never "modernize" existing annotations (e.g. `List[str]` → `list[str]`) unless the user explicitly asks for a rewrite. Consistency with what's there beats style preference: if the file already uses `typing-module` style, new annotations you add should match it regardless of `python.style`.

### Step 3 — Gather Evidence per Signature

For each unannotated or partially annotated target, collect visible evidence, strongest first:

1. **Default values** — `def f(limit=10)` → `int`; `flag=False` → `bool`; `items=None` plus later use → `X | None`.
2. **Return statements** — every `return` expression in the function; `return` with no value / no return statement → `None`.
3. **Docstrings** — existing `Args:`/`Returns:` type mentions, JSDoc-ish hints in comments.
4. **Usage inside the body** — `for x in items` (iterable), `spec.split(",")` (str), arithmetic (numeric), `len(x)` (Sized), attribute/method calls that pin a type.
5. **Call sites visible in the same file** — literal arguments passed to the function.
6. **`batch_context`** — signatures/types from related files, when provided.
7. **Stdlib and well-known library knowledge** — `re.match(...)` returns `re.Match[str] | None`; `json.loads` returns `Any`; `pathlib.Path.read_text` returns `str`.

### Step 4 — Confidence Gate

Score each candidate annotation:

- **Provable** — a single consistent type follows from the evidence (default value, all return paths agree, unambiguous usage). Annotate.
- **Strong inference** — evidence points one way but an alternate type is plausible (e.g. a param only passed to `str()`). Annotate only if `strictness: aggressive`; in Python mark it with a trailing `# type: inferred` comment on the signature line so a human can review; in JSDoc use the type as-is.
- **Ambiguous** — evidence conflicts, or the value is passed through opaquely. **Skip.** Do not write `Any` as a shrug — an absent annotation is more honest than `Any`, and `Any` actively disables checking. The only acceptable `Any` is one demanded by the code itself (e.g. a value straight from `json.loads`).

Structural preferences when annotating:
- Parameters: prefer abstract/duck types where usage supports them (`Iterable[str]`, `Sequence[int]`, `Mapping[str, Any]`, `Callable[[int], str]`).
- Returns: prefer concrete types (`list[str]`, not `Iterable[str]`) since callers depend on them.
- Use `X | None` (or `Optional[X]` per config) whenever `None` is a possible value — never omit the `None` arm to make a signature look cleaner.

### Step 5 — Write the Annotations

**Python:**
- Annotate in place: `def parse(spec: str, *, limit: int = 10) -> list[str]:`.
- Respect `python.style` and `target_version` (Step 4 of Configuration).
- `self`/`cls` are never annotated. `*args`/`**kwargs` are annotated with the element type (`*args: str`, `**kwargs: int`) only when provable, else skipped.
- Class attributes and dataclass fields: `name: str = ""` when `annotate_class_attributes: true` and provable.
- Add required imports per `python.add_typing_imports`: extend an existing `from typing import ...` line alphabetically; otherwise insert a new import in the stdlib import block (after `__future__` imports, shebang, encoding line, and module docstring). Prefer `collections.abc` for `Iterable`/`Sequence`/`Mapping`/`Callable` when `target_version >= "3.9"`.
- Never add `from __future__ import annotations` unless required to make your annotations valid on `target_version` — and if you do add it, it must be the first import.

**JavaScript (JSDoc):**
- If the function already has a JSDoc block, add `@param {type}`/`@returns {type}` tags into it (or add `{type}` to existing untyped tags) without rewriting the prose.
- If there is no JSDoc block, add a minimal typed block — tags only, no invented descriptions:
  ```javascript
  /**
   * @param {string} spec
   * @param {number} [limit=10]
   * @returns {string[]}
   */
  ```
- Use standard JSDoc/Closure syntax: `{string}`, `{number[]}`, `{Object<string, number>}`, `{?string}` for nullable, `{function(number): string}`, square-bracket param name for optional.
- For typedef-worthy object shapes that recur, you may add one `@typedef` block near the top of the file — only when the same shape appears in 2+ signatures.

### Step 6 — Quality Gate

Reject any annotation before insertion if:

- It is `Any`/`{*}` used as a placeholder for "I don't know" (see Step 4).
- It contradicts a docstring, an existing partial annotation, or visible usage.
- It narrows a type the code doesn't guarantee (annotating `-> list[str]` when one path returns `None`).
- It requires a runtime import that could change behavior (e.g. importing the annotated class at module scope would create a circular import — in that case skip, or use a string literal annotation if `target_version` allows).
- Adding it changes anything other than: the signature line(s), a class-attribute line, an import line, or a JSDoc comment block.

### Step 7 — Validate

- The file must be byte-for-byte identical to the input outside of: annotations added to existing lines, typing/abc import lines, and JSDoc blocks.
- Python: the result must be syntactically valid (mentally parse every changed signature; balanced brackets, valid annotation expressions for `target_version`).
- No annotation was inserted inside a string literal, comment, or docstring body.
- Re-running this process on the output would produce no changes (idempotency check).

### Step 8 — Emit Output

**Standard mode (`batch_mode: false`):**
- Default: the full modified source file, raw, no fences, no prose.
- On diff request: a unified diff only (`--- a/` / `+++ b/`, 3 context lines, applies cleanly with `patch -p1`).
- If no annotation clears the confidence gate, emit the file unchanged.

**Batch mode (`batch_mode: true`):**

Batch mode means this skill is being invoked by the code-comrades `batch-file-worker` subagent as part of a `/code-comrades:dispatch` run. You have live `Edit`/`Write` access to the one file you were given. Apply every change from Steps 1–7 directly to that file — do not emit contents as text, do not construct a diff.

Report back using this status vocabulary:

- `done, changed: true` — you added annotations.
- `done, changed: false` — processed, nothing to add (already fully annotated, or nothing cleared the confidence gate).
- `skipped, changed: false` — file not applicable; reason from: `ALREADY_TYPED_LANGUAGE`, `STUB_FILE`, `GENERATED`, `TRIVIAL`, `BINARY`, `VENDORED`.
- `error, changed: false` — processing failed; give a reason. Never leave a file half-edited — fully apply or fully revert before reporting.

The calling worker packages this into its `===BATCH-RESULT===` block — just state status/changed/reason/summary clearly in your final response.

---

## Examples

### Example 1: Python — Before

```python
def parse_csv(spec, limit=None):
    if not spec:
        return []
    parts = [p.strip() for p in spec.split(",") if p.strip()]
    if limit is not None:
        return parts[:limit]
    return parts
```

### Example 1: Python — After (defaults)

```python
def parse_csv(spec: str, limit: int | None = None) -> list[str]:
    if not spec:
        return []
    parts = [p.strip() for p in spec.split(",") if p.strip()]
    if limit is not None:
        return parts[:limit]
    return parts
```

(`spec.split(",")` proves `str`; `parts[:limit]` with an `is not None` guard and slice usage proves `int | None`; both return paths prove `list[str]`. With `target_version: "3.9"`, `int | None` would instead be `Optional[int]` with the import added.)

### Example 2: JavaScript — Before

```javascript
function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}
```

### Example 2: JavaScript — After

```javascript
/**
 * @param {Function} fn
 * @param {number} ms
 * @returns {Function}
 */
function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}
```

### Example 3: Ambiguous — Skipped

```python
def process(data):
    return handler.run(data)
```

`data` is passed through opaquely and `handler` is not visible. Under `strictness: conservative` this signature is left untouched — no `Any`, no guess. In batch mode, if this is the only candidate in the file: `done, changed: false`.

### Example 4: Partial Annotation — Fill Only the Gap

```python
def fetch(url: str, timeout=30):
    ...
```

becomes

```python
def fetch(url: str, timeout: int = 30):
    ...
```

The existing `url: str` is untouched; only `timeout` gains an annotation. The return stays unannotated unless the body proves it.

---

## Constraints — Never Do

- Never alter logic, control flow, formatting, indentation, or any name. Annotations, typing imports, and JSDoc blocks only.
- Never change runtime behavior. If an annotation would require a runtime import with side effects or a circular-import risk, skip it or use a string literal annotation.
- Never write `Any` (or JSDoc `{*}`) as a substitute for uncertainty. Skip instead.
- Never rewrite, "fix," or modernize an existing annotation unless explicitly asked. Fill gaps only.
- Never annotate `self` or `cls`.
- Never annotate a signature in a way that contradicts any visible return path, default value, or documented type.
- Never emit annotations invalid on the configured `python.target_version`.
- Never process TypeScript, `.pyi` stubs, generated, minified, or vendored files — emit unchanged with the matching skip status.
- Never invent object shapes, class names, or imported types not supported by visible code or provided `batch_context`.
- Never output prose, explanation, or markdown outside the code artifact itself, except the batch-mode status report described in Step 8.
- Never emit partial files; always the complete file or a complete diff (batch mode edits the file directly instead — see Step 8).
