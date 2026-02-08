# Coding Guidelines (MISRA-Inspired for Python)

This document defines coding standards for suigetsukan-lambdas, adapted from MISRA principles for safety-critical software. MISRA C/C++ emphasizes defensive programming, explicit behavior, and bounded complexity; these guidelines translate those principles to Python.

## Core Principles

1. **Explicit over implicit** — No magic numbers, no bare except, no wildcard imports.
2. **Defensive programming** — Validate inputs, handle errors explicitly.
3. **Bounded complexity** — Functions and modules stay within cyclomatic complexity limits.
4. **Single responsibility** — Each function does one thing.
5. **No dangerous constructs** — No eval, exec, or mutable default arguments.

---

## Rule Categories

### 1. Error Handling (MISRA: Rule 15.x)

| Rule | Description | Example |
|------|-------------|---------|
| No bare `except` | Explicitly catch specific exception types | `except ValueError:` not `except:` |
| No `try/except/pass` | Always log or re-raise; never silently swallow | Use `logging.exception()` or `raise` |
| No blind except | Don't catch `Exception` without good reason | Prefer specific exceptions |
| Re-raise with context | Use `raise ... from err` when re-raising | `raise CustomError() from exc` |

### 2. Constants and Magic Numbers (MISRA: Rule 2.1)

| Rule | Description | Example |
|------|-------------|---------|
| No magic numbers | Use named constants | `MAX_RETRIES = 5` not `for i in range(5)` |
| Constants in `common/` | Shared values go in `common/constants.py` | Regions, HTTP codes, key names |

### 3. Type Safety (MISRA: Rule 10.x)

| Rule | Description | Example |
|------|-------------|---------|
| No mutable default args | Use `None` and assign inside function | `def f(x=None): x = x or []` |
| No function calls in defaults | Defaults must be literals or constants | Avoid `def f(t=time.now())` |
| Prefer explicit types | Add type hints to public functions | `def process(data: dict) -> int:` |

### 4. Complexity (MISRA: Rule 15.2)

| Rule | Description | Limit |
|------|-------------|-------|
| Cyclomatic complexity | Per-function | ≤ 10 |
| Nesting depth | Reduce with early returns | ≤ 4 levels |
| Line length | Per line | ≤ 100 chars |

### 5. Security (MISRA: Secure Coding)

| Rule | Description | Example |
|------|-------------|---------|
| No eval/exec | Avoid dynamic code execution | Use `ast.literal_eval` if needed |
| No hardcoded secrets | Use env vars or parameter store | `os.environ['AWS_REGION']` |
| No subprocess with shell=True | Prefer list-form args | `subprocess.run(['cmd', 'arg'])` |
| No assert for production logic | Use proper error handling | `if not valid: raise ValueError()` |

### 6. Code Style

| Rule | Description |
|------|-------------|
| No unused imports | Remove or use |
| No unused variables | Remove or prefix with `_` |
| No wildcard imports | Use explicit imports |
| No shadowing builtins | Don't name vars `list`, `dict`, `id`, etc. |
| Trailing commas | Use in multi-line collections |
| Docstrings | Required for public functions |

### 7. Lambda-Specific

| Rule | Description |
|------|-------------|
| Handler naming | Use `lambda_handler` for SNS/EventBridge; `handler` for Cognito triggers |
| Env vars | Declare in `config.json` and use `os.environ`; never hardcode |
| Boto3 clients | Create per-invocation or cache; use region from env |

---

## Enforcement

- **Pre-commit hook**: Runs ruff, mypy, bandit on staged files.
- **CI**: All checks must pass before deploy.
- **Tools**: ruff (lint), mypy (types), bandit (security), pytest (tests).

---

## Exceptions

Some rules may be relaxed with justification:

- **Tests**: `assert` is allowed in `tests/`.
- **Scripts**: `.github/scripts/` may use `subprocess` with `shell=True` where necessary.
- **Legacy**: Document in a comment and file an issue to address.

---

## References

- [MISRA C:2012](https://www.misra.org.uk/)
- [PEP 8](https://peps.python.org/pep-0008/)
- [Ruff Rules](https://docs.astral.sh/ruff/rules/)
