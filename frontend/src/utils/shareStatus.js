// Whether a share currently serves a stale copy: the artifact's live files have
// changed since the snapshot the link serves (source_updated_at > snapshot_at).
// Session shares are never "outdated" (the frozen line is authoritative), so this
// is artifact-only. Drives the per-row "outdated" tag + "Push update" button, the
// per-target "Push update to all" banner, and the warning tint on share buttons.
export function isShareOutdated(share) {
    return !!(share
        && share.kind === 'artifact'
        && share.source_updated_at
        && share.options?.snapshot_at
        && share.source_updated_at > share.options.snapshot_at)
}
