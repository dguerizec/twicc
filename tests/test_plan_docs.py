"""Unit tests for twicc.providers.plan_docs (pure module, no DB)."""

from datetime import datetime, timezone

from twicc.providers.plan_docs import (
    FOLDED_SOURCES,
    DocEditEvent,
    apply_doc_edit_events,
    extract_shell_write_targets,
    fold_concurrent_entries,
    is_plan_doc_path,
    normalize_stored_path,
    refresh_entries_existence,
)


# --- is_plan_doc_path ---------------------------------------------------------

class TestIsPlanDocPath:
    def test_keyword_stems_match(self):
        assert is_plan_doc_path('/repo/implementation-plan.md')
        assert is_plan_doc_path('/repo/NOTES.txt')
        assert is_plan_doc_path('/repo/api_spec.html')
        assert is_plan_doc_path('/repo/2026-07-04-feature-design.md')
        assert is_plan_doc_path('handoff.md')

    def test_token_matching_not_substring(self):
        assert not is_plan_doc_path('/repo/airplane.md')
        assert not is_plan_doc_path('/repo/planet.txt')
        assert not is_plan_doc_path('/repo/designer-profile.md')  # "designer" != "design"

    def test_dir_keywords(self):
        assert is_plan_doc_path('/repo/docs/plans/refactor.md')
        assert is_plan_doc_path('docs/specs/api.md')
        assert not is_plan_doc_path('/repo/docs/guide.md')  # plain docs/ is not enough

    def test_extensions(self):
        assert not is_plan_doc_path('/repo/plan.py')
        assert not is_plan_doc_path('/repo/plan.json')
        assert is_plan_doc_path('/repo/plan.rst')
        assert is_plan_doc_path('/repo/plan.adoc')

    def test_excluded_names(self):
        assert not is_plan_doc_path('/repo/README.md')
        assert not is_plan_doc_path('/repo/CHANGELOG.md')
        assert not is_plan_doc_path('/repo/CLAUDE.md')
        assert not is_plan_doc_path('/repo/CLAUDE.local.md')
        assert not is_plan_doc_path('/repo/AGENTS.md')
        # But a keyword-bearing composite name is fine.
        assert is_plan_doc_path('/repo/readme-plan.md')

    def test_excluded_segments(self):
        assert not is_plan_doc_path('/repo/node_modules/pkg/plan.md')
        assert not is_plan_doc_path('/repo/.git/notes.md')
        assert not is_plan_doc_path('/repo/vendor/lib/design.md')

    def test_garbage_inputs(self):
        assert not is_plan_doc_path('')
        assert not is_plan_doc_path(None)
        assert not is_plan_doc_path('/')


# --- extract_shell_write_targets ------------------------------------------------

class TestExtractShellWriteTargets:
    def test_simple_redirect(self):
        assert extract_shell_write_targets('echo hi > notes.md') == [('notes.md', 'write')]

    def test_append_redirect(self):
        assert extract_shell_write_targets('echo hi >> docs/plans/a.md') == [('docs/plans/a.md', 'write')]

    def test_heredoc_into_file(self):
        cmd = "cat > docs/plans/a.md <<'EOF'\nsome plan content\nEOF"
        assert ('docs/plans/a.md', 'write') in extract_shell_write_targets(cmd)

    def test_heredoc_with_apostrophe_body(self):
        # The body is prose with an unbalanced quote: the prefix fallback must
        # still catch the redirection target.
        cmd = "cat > plan.md <<'EOF'\ndon't lose this\nEOF"
        assert ('plan.md', 'write') in extract_shell_write_targets(cmd)

    def test_tee(self):
        assert extract_shell_write_targets('echo x | tee spec.md') == [('spec.md', 'write')]
        assert extract_shell_write_targets('echo x | tee -a spec.md') == [('spec.md', 'write')]

    def test_sed_in_place(self):
        assert extract_shell_write_targets('sed -i s/x/y/ design.md') == [('design.md', 'write')]
        assert extract_shell_write_targets('sed -i -e s/x/y/ design.md') == [('design.md', 'write')]

    def test_sed_without_in_place(self):
        assert extract_shell_write_targets('sed s/x/y/ design.md') == []

    def test_mv_cp_destination(self):
        assert extract_shell_write_targets('mv tmp.md docs/handoff.md') == [('docs/handoff.md', 'write')]
        assert extract_shell_write_targets('cp a.md b.md') == [('b.md', 'write')]

    def test_touch(self):
        assert extract_shell_write_targets('touch todo.md') == [('todo.md', 'write')]

    def test_rm_is_delete(self):
        assert extract_shell_write_targets('rm old-plan.md') == [('old-plan.md', 'delete')]
        assert extract_shell_write_targets('rm -f old-plan.md') == [('old-plan.md', 'delete')]

    def test_dev_null_ignored(self):
        assert extract_shell_write_targets('cmd > /dev/null 2>&1') == []

    def test_chained_commands(self):
        result = extract_shell_write_targets('mkdir -p docs && echo x > docs/plan.md; rm tmp.md')
        assert ('docs/plan.md', 'write') in result
        assert ('tmp.md', 'delete') in result

    def test_argv_list_with_shell_wrapper(self):
        assert extract_shell_write_targets(['bash', '-lc', 'echo x > notes.md']) == [('notes.md', 'write')]

    def test_argv_list_direct(self):
        assert extract_shell_write_targets(['touch', 'plan.md']) == [('plan.md', 'write')]

    def test_unparseable_returns_empty(self):
        assert extract_shell_write_targets("echo 'unbalanced") == []

    def test_non_command_types(self):
        assert extract_shell_write_targets(None) == []
        assert extract_shell_write_targets(42) == []

    def test_read_only_commands(self):
        assert extract_shell_write_targets('cat plan.md') == []
        assert extract_shell_write_targets('grep -rn foo docs/') == []
        assert extract_shell_write_targets('ls -la') == []


# --- normalize_stored_path / apply_doc_edit_events -----------------------------

T1 = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 7, 4, 13, 0, 0, tzinfo=timezone.utc)
T3 = datetime(2026, 7, 4, 14, 0, 0, tzinfo=timezone.utc)


class TestNormalizeStoredPath:
    def test_relativizes_under_project_root(self):
        assert normalize_stored_path('/repo/docs/plan.md', '/repo') == 'docs/plan.md'

    def test_absolute_outside_root(self):
        assert normalize_stored_path('/home/u/.claude/plans/slug.md', '/repo') == '/home/u/.claude/plans/slug.md'

    def test_no_root(self):
        assert normalize_stored_path('/repo/docs/plan.md', None) == '/repo/docs/plan.md'

    def test_sibling_prefix_not_relativized(self):
        # /repo-other is NOT under /repo despite the string prefix.
        assert normalize_stored_path('/repo-other/plan.md', '/repo') == '/repo-other/plan.md'


class TestApplyDocEditEvents:
    def test_create_entry(self):
        entries, changed = apply_doc_edit_events(
            [], [(DocEditEvent('/repo/docs/plan.md', 'write'), T1)], project_root='/repo',
        )
        assert changed
        assert entries == [{
            'path': 'docs/plan.md', 'exists': True,
            'created_at': T1.isoformat(), 'updated_at': T1.isoformat(),
            'source': 'detected',
        }]

    def test_update_bumps_updated_at_keeps_created_at(self):
        entries, _ = apply_doc_edit_events(
            [], [(DocEditEvent('/repo/plan-a.md', 'write'), T1)], project_root='/repo',
        )
        entries, changed = apply_doc_edit_events(
            entries, [(DocEditEvent('/repo/plan-a.md', 'write'), T2)], project_root='/repo',
        )
        assert changed
        assert entries[0]['created_at'] == T1.isoformat()
        assert entries[0]['updated_at'] == T2.isoformat()

    def test_delete_flips_exists(self):
        entries, _ = apply_doc_edit_events(
            [], [(DocEditEvent('/repo/plan.md', 'write'), T1)], project_root='/repo',
        )
        entries, changed = apply_doc_edit_events(
            entries, [(DocEditEvent('/repo/plan.md', 'delete'), T2)], project_root='/repo',
        )
        assert changed
        assert entries[0]['exists'] is False
        assert entries[0]['updated_at'] == T2.isoformat()

    def test_write_after_delete_restores_exists(self):
        events = [
            (DocEditEvent('/repo/plan.md', 'write'), T1),
            (DocEditEvent('/repo/plan.md', 'delete'), T2),
            (DocEditEvent('/repo/plan.md', 'write'), T3),
        ]
        entries, _ = apply_doc_edit_events([], events, project_root='/repo')
        assert entries[0]['exists'] is True
        assert entries[0]['updated_at'] == T3.isoformat()

    def test_delete_unknown_path_ignored(self):
        entries, changed = apply_doc_edit_events(
            [], [(DocEditEvent('/repo/plan.md', 'delete'), T1)], project_root='/repo',
        )
        assert entries == []
        assert not changed

    def test_no_events_no_change(self):
        existing = [{'path': 'plan.md', 'exists': True, 'created_at': None, 'updated_at': None, 'source': 'detected'}]
        entries, changed = apply_doc_edit_events(existing, [], project_root='/repo')
        assert entries == existing
        assert entries is not existing  # copies, input untouched
        assert not changed

    def test_stale_timestamp_does_not_regress(self):
        entries, _ = apply_doc_edit_events(
            [], [(DocEditEvent('/repo/plan.md', 'write'), T2)], project_root='/repo',
        )
        entries, changed = apply_doc_edit_events(
            entries, [(DocEditEvent('/repo/plan.md', 'write'), T1)], project_root='/repo',
        )
        assert entries[0]['updated_at'] == T2.isoformat()
        assert not changed

    def test_claude_plan_source(self):
        entries, _ = apply_doc_edit_events(
            [], [(DocEditEvent('/home/u/.claude/plans/slug.md', 'write', 'claude_plan'), T1)],
            project_root='/repo',
        )
        assert entries[0]['source'] == 'claude_plan'
        assert entries[0]['path'] == '/home/u/.claude/plans/slug.md'


# --- refresh_entries_existence ---------------------------------------------------

class TestRefreshEntriesExistence:
    def test_absolute_and_relative(self, tmp_path):
        real = tmp_path / 'docs' / 'plan.md'
        real.parent.mkdir()
        real.write_text('x')
        entries = [
            {'path': 'docs/plan.md', 'exists': False},
            {'path': str(tmp_path / 'missing.md'), 'exists': True},
        ]
        changed = refresh_entries_existence(entries, [str(tmp_path)])
        assert changed
        assert entries[0]['exists'] is True
        assert entries[1]['exists'] is False

    def test_fallback_root(self, tmp_path):
        # Entry relative to a gone worktree, resolvable in the parent root.
        parent = tmp_path / 'main'
        (parent / 'docs').mkdir(parents=True)
        (parent / 'docs' / 'plan.md').write_text('x')
        entries = [{'path': 'docs/plan.md', 'exists': False}]
        changed = refresh_entries_existence(entries, [str(tmp_path / 'gone-worktree'), str(parent)])
        assert changed
        assert entries[0]['exists'] is True

    def test_no_change(self, tmp_path):
        entries = [{'path': 'nope.md', 'exists': False}]
        assert not refresh_entries_existence(entries, [str(tmp_path)])


# --- fold_concurrent_entries -----------------------------------------------------

class TestFoldConcurrentEntries:
    def test_fresher_db_claude_plan_entry_wins(self):
        computed = [{'path': '/plans/slug.md', 'exists': True, 'created_at': T1.isoformat(),
                     'updated_at': T1.isoformat(), 'source': 'claude_plan'}]
        db = [{'path': '/plans/slug.md', 'exists': True, 'created_at': T1.isoformat(),
               'updated_at': T2.isoformat(), 'source': 'claude_plan'}]
        result = fold_concurrent_entries(computed, db, sources=FOLDED_SOURCES)
        assert result[0]['updated_at'] == T2.isoformat()

    def test_missing_db_entries_added(self):
        computed = [{'path': 'docs/plan.md', 'source': 'detected', 'updated_at': T1.isoformat()}]
        db = [
            {'path': '/plans/slug.md', 'source': 'claude_plan', 'updated_at': T2.isoformat()},
            {'path': 'docs/subagent-handoff.md', 'source': 'subagent', 'updated_at': T2.isoformat()},
        ]
        result = fold_concurrent_entries(computed, db, sources=FOLDED_SOURCES)
        assert len(result) == 3
        assert any(e['path'] == '/plans/slug.md' for e in result)
        assert any(e['path'] == 'docs/subagent-handoff.md' for e in result)

    def test_detected_db_entries_ignored(self):
        computed = []
        db = [{'path': 'docs/plan.md', 'source': 'detected', 'updated_at': T2.isoformat()}]
        assert fold_concurrent_entries(computed, db, sources=FOLDED_SOURCES) == []

    def test_stale_db_entry_does_not_regress(self):
        computed = [{'path': '/plans/slug.md', 'source': 'claude_plan', 'updated_at': T2.isoformat()}]
        db = [{'path': '/plans/slug.md', 'source': 'claude_plan', 'updated_at': T1.isoformat()}]
        result = fold_concurrent_entries(computed, db, sources=FOLDED_SOURCES)
        assert result[0]['updated_at'] == T2.isoformat()

    def test_empty_db(self):
        computed = [{'path': 'a.md', 'source': 'detected'}]
        assert fold_concurrent_entries(computed, None, sources=FOLDED_SOURCES) == computed
