---
name: ccn
description: Runs cyclomatic complexity checks via check_complexity.py, fixes violations, runs tests, and repeats until CCN≤10 and function params≤7. Use when the user types "ccn" or asks to check complexity, reduce CCN, or enforce complexity limits.
---

# CCN (Cyclomatic Complexity) Check and Fix

## When to Use

Apply this skill when the user types **ccn** or asks to check/fix code complexity, reduce cyclomatic complexity, or enforce CCN/parameter limits.

## Workflow

Repeat until **all** of the following are satisfied:
- **CCN ≤ 10** (cyclomatic complexity number)
- **Function parameters ≤ 7**

### Step 1: Choose target directory

From the **project root**, determine which directory to analyze:
- If `src/` exists at the root → use **src**
- Otherwise if `main/` exists at the root → use **main**

If neither exists, use the project’s primary source directory (e.g. `src` or `main` if present elsewhere) and document the choice.

### Step 2: Run complexity check

Run the checker with `--strict`:

```bash
python check_complexity.py <src|main> --strict
```

Examples:
- `python check_complexity.py src --strict`
- `python check_complexity.py main --strict`

Use the directory chosen in Step 1.

### Step 3: Fix reported issues

- Address every issue reported by `check_complexity.py`.
- Reduce CCN (e.g. split functions, simplify conditionals, extract helpers).
- Reduce parameter count (e.g. use a config object, group related args).

### Step 4: Run local tests

Run the project’s test suite (e.g. `pytest`, `npm test`, `cargo test`, or the project’s documented test command). Fix any failing tests.

### Step 5: Loop

- Run **Step 2** again.
- If the report still shows CCN > 10 or params > 7, repeat **Steps 3 → 4 → 2**.
- Stop when the checker passes with CCN ≤ 10 and function params ≤ 7 and all tests pass.

## Summary

| Goal              | Requirement        |
|-------------------|--------------------|
| Cyclomatic complexity | CCN ≤ 10        |
| Function parameters   | ≤ 7              |
| Tests                 | All passing      |

Do not stop until both complexity limits are met and the test suite is green.
