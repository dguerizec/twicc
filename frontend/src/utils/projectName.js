// frontend/src/utils/projectName.js
// Display helpers for project names.

/**
 * Tooltip path for a project that is shown by name only.
 *
 * Unnamed projects are displayed with just the final folder name of their
 * directory, which can be ambiguous when several projects share the same leaf
 * folder. Returning the full directory path lets callers surface it as a native
 * `title` tooltip on hover. Named projects show their chosen name and get no
 * path tooltip. Returns `null` when there is nothing useful to show.
 *
 * @param {Object|null|undefined} project - Project object with `name`/`directory`
 * @returns {string|null}
 */
export function projectPathTitle(project) {
    if (!project || project.name) {
        return null
    }
    return project.directory || null
}
