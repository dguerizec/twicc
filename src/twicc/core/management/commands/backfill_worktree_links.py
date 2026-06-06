"""Management command: backfill Project.worktree_of from filesystem signals.

Standalone-testable wrapper around :func:`twicc.worktree_backfill.backfill_worktree_links`.
The ``run`` startup calls the same function directly, right after ``migrate``.
"""

import asyncio

from django.core.management.base import BaseCommand

from twicc.worktree_backfill import backfill_worktree_links


class Command(BaseCommand):
    help = "Backfill Project.worktree_of links from strong filesystem signals."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report the links that would be created, without writing.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        plans, linked, unresolved = asyncio.run(
            backfill_worktree_links(
                dry_run=dry_run, log=self.stdout.write, warn=self.stderr.write
            )
        )
        if dry_run:
            self.stdout.write(f"{len(plans)} worktree link(s) would be created.")
        else:
            self.stdout.write(
                f"{linked} worktree link(s) created (out of {len(plans)} detected)."
            )
        if unresolved:
            self.stdout.write(
                f"{len(unresolved)} project(s) look like a worktree but could not be linked."
            )
