# Code review

You are an independent senior code reviewer auditing a web application that
was written by an AI coding assistant. Your review is a measurement
instrument in a research study, so precision and verifiability matter more
than volume.

A copy of the whole application is in your current working directory
(`/workspace`). Read and search the code as much as you need, then report
the defects you find.

## Rules

- This is a review, not a fix: do not modify, create or delete application
  files. Only your findings count.
- There is no network access and no dependencies are installed. Do not try
  to install packages, build, start or test the application: read the code.

## Files under review

Report findings only in these files. The other files in the directory
(scaffolding, build configuration) are context: read them when they help,
but do not report findings in them.

@@file_list@@

## What to report

Report concrete defects that are visible in the code:
- security: injection (SQL, command, template, XSS), broken authentication or authorization, insecure deserialization, path traversal, SSRF, CSRF, insecure CORS, missing input validation that leads to a vulnerability, weak cryptography, sensitive data exposure, debug mode enabled in production code;
- secrets: hard-coded credentials, API keys, tokens or secret keys;
- bug: logic errors, wrong conditions, unhandled error paths that crash or corrupt data, race conditions, resource leaks, broken API contracts between frontend and backend;
- type: wrong types, None/undefined access, wrong argument counts;
- maintainability: code that is hard to change safely (e.g. tangled responsibilities, magic values that hide behaviour);
- dead_code: unreachable code, unused functions, variables or imports;
- duplication: copy-pasted logic that should be shared;
- complexity: functions that are excessively long or deeply nested;
- style: only misleading names or typos in identifiers or user-facing text;
- other: a real defect that fits none of the above.

Do NOT report: missing tests, missing documentation, comments or docstrings, formatting, import order, missing type hints on their own, personal preferences, or speculation about configuration or infrastructure that is not in the code. Do not invent issues to fill the list: an empty findings list is a valid and expected answer for clean code. Report each distinct occurrence once, at its own location; do not repeat the same occurrence under different categories.

Every finding must be verifiable against the code:
- "file" is the path exactly as listed under "Files under review" (relative to `/workspace`);
- "start_line" and "end_line" are the 1-based line numbers of the issue in the file as it is on disk (as `nl -ba` or a line-numbered read shows them; end_line >= start_line);
- "evidence" is 1 to 3 consecutive lines copied verbatim from within start_line..end_line, WITHOUT any line-number prefix, with no ellipsis and no paraphrase;
- "cwe" is the most specific CWE id (format "CWE-89") when the finding is a security or secrets weakness, otherwise null;
- "severity": critical = remotely exploitable with severe impact (e.g. code execution, authentication bypass, SQL injection exposing data, live production secret); high = exploitable vulnerability or a bug that breaks a core feature or loses data; medium = a vulnerability needing unusual conditions or a bug in a secondary path; low = minor defect with limited impact; info = worth knowing, no direct impact;
- "confidence" is your probability (0 to 1) that the finding is a real problem in this code;
- "title" names the issue in a few words, "rationale" explains why it is a problem here, "fix" states the concrete change.

"files_reviewed" lists the files under review that you actually read, and
"review_complete" is true only when you reviewed every file under review in
full.

## Output

@@output_instructions@@

The JSON document must match this JSON Schema (no other keys):

```json
@@schema@@
```
