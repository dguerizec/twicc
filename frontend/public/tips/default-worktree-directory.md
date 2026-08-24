---
title: "Choose where worktrees go by default"
---

Tell TwiCC where to put new git worktrees with a **template**. Use placeholders
that resolve per project: `{git_root}` (its git root), `{project_name}` (its
name, or folder name if unnamed) and `{project_basedir}` (its folder name). For
example `{git_root}/.worktrees` keeps them inside the repo, while
`/home/me/worktrees/{project_name}` gathers every project's worktrees in one
place. The worktree dialog then pre-fills the path from it — you can still
change it each time.

Set it in **Settings → General → Worktree directory template**, and override it
per project (with a plain absolute path) from that project's edit dialog.

More in the help dialog: [Working with worktrees](help/worktrees).
