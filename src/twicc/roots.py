"""Allowed root directories for a project (and optional session).

Centralizes the set of base directories that file-tree browsing, path
validation and git operations are allowed to touch. When a project is a git
worktree (``worktree_of`` set), the main repository's directories are included
so the worktree's Files / Git tabs can reach the parent checkout.

The helpers operate on already-fetched model instances. The optional ``parent``
(the worktree's main-repo project) must be fetched by the caller, because the
fetch is synchronous in some call sites and asynchronous in others.
"""

import os


def _norm(path):
    return os.path.normpath(path) if path else None


def allowed_base_dirs(project, session=None, parent=None):
    """Ordered, de-duplicated, normalized base directories a path may live under.

    Order (worktree first, main repo last):
      1. ``project.git_root`` (else ``project.directory``)
      2. ``session.cwd``, ``session.git_directory`` (when a session is given)
      3. ``parent.git_root``, ``parent.directory`` (when the project is a worktree)
    """
    bases = []

    def add(path):
        norm = _norm(path)
        if norm and norm not in bases:
            bases.append(norm)

    if project.git_root:
        add(project.git_root)
    elif project.directory:
        add(project.directory)
    if session:
        add(session.cwd)
        add(session.git_directory)
    if parent:
        add(parent.git_root)
        add(parent.directory)
    return bases


def git_roots_for(project, session=None, parent=None):
    """Ordered, de-duplicated, normalized git roots (session, project, parent).

    Used to validate a requested ``git_dir`` and to pick a default. The
    worktree's own roots come first; the main repo's git root is offered last.
    """
    roots = []

    def add(path):
        norm = _norm(path)
        if norm and norm not in roots:
            roots.append(norm)

    if session:
        add(session.git_directory)
    add(project.git_root)
    if parent:
        add(parent.git_root)
    return roots
