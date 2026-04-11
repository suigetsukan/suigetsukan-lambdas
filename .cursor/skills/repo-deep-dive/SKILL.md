---
name: repo-deep-dive
description: Performs a comprehensive critical review of a repository covering code quality, code coverage (tests), and documentation quality. Use when the user types "ddd" or asks for a deep dive, repo review, comprehensive review, code quality review, test coverage review, or documentation review of the codebase.
---

# Comprehensive Repository Deep Dive

When the user types **ddd** or requests a comprehensive deep dive or critical review of the repo, perform a structured review and report on three areas. Be thorough and evidence-based; cite files and line ranges where relevant.

## Workflow

1. **Scope**: Confirm the repo root (usually workspace root). If the user specified a subdirectory or component, limit the review to that scope.
2. **Gather**: Explore structure (dirs, key config files), locate tests, locate docs, and identify coding standards (e.g. AGENTS.md, docs, .cursor/rules).
3. **Assess**: Work through each pillar below. Run or inspect tests and tooling where applicable.
4. **Report**: Use the output template. Be specific; avoid generic praise or vague criticism.

## Pillar 1: Code Quality

- **Structure and conventions**: Naming, file/module layout, separation of concerns, duplication.
- **Complexity and maintainability**: Function length, nesting, cyclomatic complexity if mentioned in project rules (e.g. CC ≤ 10).
- **Error handling and safety**: Return value checks, resource cleanup, input validation, buffer/bounds safety, thread safety if relevant.
- **Consistency**: Adherence to project rules (e.g. .cursor/rules, AGENTS.md, docs/CODING_STANDARDS.md). Note gaps.
- **Tooling**: Use project scripts if they exist (e.g. `scripts/check_complexity.py`). Mention whether they pass or fail.

Summarize strengths and list concrete issues with file/line or function references. Prioritize (e.g. critical / should-fix / nice-to-have).

## Pillar 2: Code Coverage (Tests)

- **Test layout**: Where tests live (e.g. `test/`, `*_test.c`, `tests/`), how they're run (e.g. `idf.py test`, `pytest`, `ctest`).
- **Coverage**: What is actually tested (unit vs integration, key modules vs gaps). Run tests and report pass/fail. If coverage tooling exists, run it and summarize.
- **Quality of tests**: Clarity, independence, use of mocks/fixtures, edge cases, flakiness.
- **Gaps**: Critical paths or modules with no or weak tests; suggest high-value targets.

Report test results (and coverage numbers if available) and list the most important gaps with file/module references.

## Pillar 3: Documentation Quality

- **Discoverability**: README, AGENTS.md, docs/ structure. Can a new contributor find how to build, test, and contribute?
- **Accuracy**: Docs vs actual behavior (config, APIs, workflows). Note outdated or wrong sections.
- **Completeness**: Architecture, design decisions, runbooks, troubleshooting. What's missing for onboarding or operations?
- **Consistency**: Terminology, formatting, and where docs live (e.g. all .md in docs/ per project rules).

Summarize strengths and list specific doc gaps or inaccuracies with file names and, if useful, section references.

## Output Template

Use this structure for the final report:

```markdown
# Repository Deep Dive: [Repo or Scope Name]

## 1. Code Quality
- **Summary**: [2–4 sentences]
- **Strengths**: [Bullets]
- **Issues**: [Bullets with file/line or function; tag Critical / Should-fix / Nice-to-have]
- **Tooling**: [Scripts run and results, e.g. complexity check]

## 2. Code Coverage (Tests)
- **Summary**: [2–4 sentences]
- **Test layout & commands**: [How tests are organized and run]
- **Results**: [Pass/fail, coverage if available]
- **Gaps**: [High-value untested or under-tested areas]

## 3. Documentation Quality
- **Summary**: [2–4 sentences]
- **Strengths**: [Bullets]
- **Gaps / inaccuracies**: [Bullets with doc references]

## Recommendations (prioritized)
1. [Most important actionable item]
2. ...
```

Keep the report scannable: use bullets and short paragraphs; long prose goes in optional "Details" subsections.

## Notes

- Respect repository boundaries (e.g. do not modify `build/`, `.git/`, or project-defined off-limits dirs).
- If the project has explicit quality rules (complexity, params, MISRA, etc.), reference them and call out violations.
- Prefer running real commands (build, test, coverage, scripts) over guessing; report what you ran and what you couldn't run.
