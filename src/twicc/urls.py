from django.urls import path, re_path

from . import views
from .auth import views as auth_views
from .rpc import views as rpc_views

urlpatterns = [
    # Auth endpoints (always accessible, no auth required)
    path("api/auth/check/", auth_views.auth_check),
    path("api/auth/login/", auth_views.login),
    path("api/auth/logout/", auth_views.logout),
    # API endpoints
    path("api/bootstrap/", views.bootstrap),
    path("api/changelog/", views.changelog),
    path("api/home/", views.home_data),
    path("api/daily-activity/", views.daily_activity),  # Global daily activity
    path("api/sessions/", views.all_sessions),
    # Static route must come BEFORE the <str:session_id> catch-all, otherwise
    # `bulk-archive` is consumed as a session_id and matched by session_by_id.
    path("api/sessions/bulk-archive/", views.bulk_archive_sessions),
    path("api/sessions/<str:session_id>/", views.session_by_id),
    path("api/search/", views.search_sessions),
    path("api/usage-history/", views.usage_history),
    # Standalone filesystem endpoints (for directory picker, no project required)
    path("api/directory-tree/", views.standalone_directory_tree),
    path("api/file-search/", views.standalone_file_search),
    path("api/file-content/", views.standalone_file_content),
    path("api/file-rename/", views.standalone_file_rename),
    path("api/file-delete/", views.standalone_file_delete),
    path("api/file-move/", views.standalone_file_move),
    path("api/file-create/", views.standalone_file_create),
    path("api/home-directory/", views.home_directory),
    path("api/projects/", views.project_list),
    path("api/projects/<str:project_id>/", views.project_detail),
    path("api/projects/<str:project_id>/trust/resolve/", views.project_trust_resolve),
    path("api/projects/<str:project_id>/trust/decide/", views.project_trust_decide),
    path("api/projects/<str:project_id>/commands/", views.commands),
    path("api/projects/<str:project_id>/daily-activity/", views.daily_activity),  # Per-project daily activity
    path("api/projects/<str:project_id>/sessions/", views.project_sessions),
    # Project-level file system endpoints (for draft sessions and project-level browsing)
    path("api/projects/<str:project_id>/directory-tree/", views.directory_tree),
    path("api/projects/<str:project_id>/file-search/", views.file_search),
    path("api/projects/<str:project_id>/file-content/", views.file_content),
    path("api/projects/<str:project_id>/file-rename/", views.file_rename),
    path("api/projects/<str:project_id>/file-delete/", views.file_delete),
    path("api/projects/<str:project_id>/file-move/", views.file_move),
    path("api/projects/<str:project_id>/file-create/", views.file_create),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/user-messages/", views.user_messages),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/", views.session_detail),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/items/", views.session_items),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/items/metadata/", views.session_items_metadata),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/items/<int:line_num>/tool-results/<str:tool_id>/", views.tool_results),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/subagents/", views.subagents_state),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/tool-states/", views.tool_states),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/topology/", views.session_topology),
    # Subagent routes (same views, with parent_session_id for validation)
    path("api/projects/<str:project_id>/sessions/<str:parent_session_id>/subagent/<str:session_id>/", views.session_detail),
    path("api/projects/<str:project_id>/sessions/<str:parent_session_id>/subagent/<str:session_id>/items/", views.session_items),
    path("api/projects/<str:project_id>/sessions/<str:parent_session_id>/subagent/<str:session_id>/items/metadata/", views.session_items_metadata),
    path("api/projects/<str:project_id>/sessions/<str:parent_session_id>/subagent/<str:session_id>/items/<int:line_num>/tool-results/<str:tool_id>/", views.tool_results),
    # Project-level git endpoints (for draft sessions)
    path("api/projects/<str:project_id>/git-log/", views.git_log),
    path("api/projects/<str:project_id>/git-index-files/", views.git_index_files),
    path("api/projects/<str:project_id>/git-commit-detail/<str:commit_hash>/", views.git_commit_detail),
    path("api/projects/<str:project_id>/git-commit-files/<str:commit_hash>/", views.git_commit_files),
    path("api/projects/<str:project_id>/git-index-file-diff/", views.git_index_file_diff),
    path("api/projects/<str:project_id>/git-commit-file-diff/<str:commit_hash>/", views.git_commit_file_diff),
    path("api/projects/<str:project_id>/git-stage/", views.git_stage_file),
    path("api/projects/<str:project_id>/git-unstage/", views.git_unstage_file),
    path("api/projects/<str:project_id>/git-discard/", views.git_discard_file),
    # Git endpoints (session-level, no subagent support)
    path("api/projects/<str:project_id>/sessions/<str:session_id>/git-log/", views.git_log),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/git-index-files/", views.git_index_files),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/git-commit-detail/<str:commit_hash>/", views.git_commit_detail),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/git-commit-files/<str:commit_hash>/", views.git_commit_files),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/git-index-file-diff/", views.git_index_file_diff),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/git-commit-file-diff/<str:commit_hash>/", views.git_commit_file_diff),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/git-stage/", views.git_stage_file),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/git-unstage/", views.git_unstage_file),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/git-discard/", views.git_discard_file),
    # File system endpoints (scoped to project + session for security)
    path("api/projects/<str:project_id>/sessions/<str:session_id>/directory-tree/", views.directory_tree),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/file-search/", views.file_search),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/file-content/", views.file_content),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/file-rename/", views.file_rename),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/file-delete/", views.file_delete),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/file-move/", views.file_move),
    path("api/projects/<str:project_id>/sessions/<str:session_id>/file-create/", views.file_create),
    # Session-scoped artifact serving. Not nested under ``/api/`` because
    # this is a media endpoint rather than a JSON API; not nested under any
    # project/session SPA path because no project ownership is implied.
    # Must come before the SPA catch-all below — otherwise ``spa_index``
    # would happily serve ``index.html`` for these URLs. Authentication is
    # enforced by ``PasswordAuthMiddleware`` via its protected non-API
    # path list.
    path(
        "artifacts/<str:session_id>/<str:artifact_file_name>",
        views.session_artifact,
    ),
    # RPC API: every CLI command auto-exposed as ``POST /rpc/<command>``.
    # Gated by Bearer API tokens via ``RpcTokenAuthMiddleware`` (open only when
    # neither a password nor any token is configured). Must precede the SPA
    # catch-all, which excludes ``rpc/`` so unknown RPC URLs 404 instead of
    # serving ``index.html``.
    path("rpc/", rpc_views.index),
    path("rpc/openapi.json", rpc_views.openapi),
    re_path(r"^rpc/(?P<command_path>[a-z0-9/-]+)/?$", rpc_views.dispatch),
    # Catch-all for Vue Router (must be last). ``artifacts/`` and ``rpc/`` are
    # excluded so those URLs surface as 404 instead of serving the SPA HTML.
    # Static files (/static/) are served by BlackNoise at the ASGI level,
    # before reaching Django's URL routing (see asgi.py).
    re_path(r"^(?!api/|rpc/|static/|ws/|artifacts/).*$", views.spa_index),
]
