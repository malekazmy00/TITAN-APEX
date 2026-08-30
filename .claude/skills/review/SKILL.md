---
name: review
description: Runs TITAN-APEX's independent code-review protocol (docs/REQUIREMENTS.md section 10) against a diff or branch, via a genuinely isolated subagent that never sees this conversation's own history.
argument-hint: "<git ref range or branch name> [-- original task description]"
---

Run the independent review protocol documented in
`docs/REQUIREMENTS.md` section 10 against: **$ARGUMENTS**

Do this by invoking the `Agent` tool directly, with:
- `subagent_type: "reviewer"` (the isolated validator defined in
  `.claude/agents/reviewer.md` -- it has no Edit/Write access and, as
  a fresh, non-`fork` agent invocation, starts with **zero context**
  from this conversation, which is the entire point: it must not see
  this session's own reasoning about the change, only the diff itself
  and the task description below).
- `prompt`: include (a) exactly what to diff (resolve `$ARGUMENTS` to
  a concrete `git diff <base>...<head>` range or branch comparison
  before handing it off -- don't make the isolated agent guess what
  "this change" refers to), and (b) the original task description in
  `$ARGUMENTS` after any `--`, verbatim, not your own summary of it.
- Do **not** include your own commentary, justification, or account
  of how the change was built in the prompt -- only the diff
  reference and the original task description. Anything more defeats
  the isolation this whole mechanism exists for.

Once the `reviewer` subagent returns its findings, relay them to the
user as-is -- do not filter, soften, or re-interpret them, and do not
mark anything resolved yourself. If the reviewer's own report says the
review was partial (size/time cap hit) or that this was not yet a
second consecutive clean pass, say that plainly rather than treating
the run as a finished review.
