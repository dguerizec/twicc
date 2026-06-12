// frontend/src/utils/projectTree.js
// Utility for building a hierarchical tree of projects from their directory paths,
// with path compression for intermediate single-child folders.

// =============================================================================
// Types (JSDoc)
// =============================================================================

/**
 * @typedef {Object} ProjectTreeNode
 * @property {string} segment - Display label (e.g. "dev" or "OBS/web/characters" after compression)
 * @property {string} path - Full accumulated directory path of this node (e.g. "/home/twidi/dev")
 * @property {Array<ProjectTreeNode>} children - Child nodes, sorted alphabetically by segment
 * @property {Object|null} project - The project object, or null for intermediate folder nodes
 */

/**
 * @typedef {Object} FlatProjectTreeItem
 * @property {boolean} isFolder - True for folder nodes without a project
 * @property {string} segment - Display label
 * @property {string} path - Full accumulated directory path of this node
 * @property {Object|null} project - The project object, or null for folder nodes
 * @property {number} depth - Nesting level (0 for top-level), for computing indent
 * @property {string} key - Unique key for v-for (project.id for projects, "__folder_N" for folders)
 */

// =============================================================================
// Internal: Trie construction
// =============================================================================

/**
 * Create an empty trie node.
 * @param {string} segment - Path segment for this node
 * @returns {{ segment: string, children: Map<string, Object>, project: Object|null }}
 */
function createTrieNode(segment) {
    return {
        segment,
        children: new Map(),
        project: null,
    }
}

/**
 * Insert a project into the trie based on its directory path.
 * @param {{ segment: string, children: Map, project: Object|null }} root - Trie root
 * @param {Object} project - Project object with a `directory` string field
 */
function insertIntoTrie(root, project) {
    const segments = project.directory.split('/').filter(s => s !== '')
    let node = root

    for (const seg of segments) {
        if (!node.children.has(seg)) {
            node.children.set(seg, createTrieNode(seg))
        }
        node = node.children.get(seg)
    }

    node.project = project
}

// =============================================================================
// Internal: Compression
// =============================================================================

/**
 * Compress the trie in a post-order traversal: when a node is NOT a project
 * and has exactly ONE child that is also NOT a project, merge by joining
 * segments with "/". This preserves the parent folder of every project node
 * so it always appears under a visible directory label.
 *
 * @param {{ segment: string, children: Map, project: Object|null }} node - Trie node to compress
 * @returns {{ segment: string, children: Map, project: Object|null }} Compressed node
 */
function compressNode(node) {
    // First, recursively compress all children
    const compressedChildren = new Map()
    for (const [key, child] of node.children) {
        compressedChildren.set(key, compressNode(child))
    }
    node.children = compressedChildren

    // Then, merge if this node is not a project and has exactly one child
    // that is also not a project (i.e. both are intermediate folders).
    // This stops compression just before a project node, so the project
    // always appears nested under its immediate parent folder.
    if (node.project === null && node.children.size === 1) {
        const [, onlyChild] = node.children.entries().next().value
        if (onlyChild.project === null) {
            return {
                segment: node.segment ? node.segment + '/' + onlyChild.segment : onlyChild.segment,
                children: onlyChild.children,
                project: onlyChild.project,
            }
        }
    }

    return node
}

// =============================================================================
// Internal: Conversion to sorted arrays
// =============================================================================

/**
 * Convert a trie node (with Map children) into a ProjectTreeNode (with sorted array children).
 * @param {{ segment: string, children: Map, project: Object|null }} trieNode
 * @returns {ProjectTreeNode}
 */
function trieNodeToTreeNode(trieNode) {
    const children = Array.from(trieNode.children.values())
        .map(trieNodeToTreeNode)
        .sort((a, b) => a.segment.localeCompare(b.segment))

    return {
        segment: trieNode.segment,
        children,
        project: trieNode.project,
    }
}

/**
 * Assign a full accumulated `path` to every node, in place.
 *
 * Root nodes already carry a leading "/" on their segment (see buildProjectTree),
 * so a root's path is its own segment; a child's path is the parent path joined
 * with the child segment. For project leaf nodes this reproduces the project's
 * `directory`; for intermediate folder nodes it is the directory that folder
 * stands for — surfaced as a hover tooltip in tree views.
 *
 * @param {ProjectTreeNode} node
 * @param {string} parentPath - Accumulated path of the parent ("" for roots)
 */
function assignPaths(node, parentPath) {
    node.path = parentPath ? `${parentPath}/${node.segment}` : node.segment
    for (const child of node.children) {
        assignPaths(child, node.path)
    }
}

// =============================================================================
// Public API
// =============================================================================

/**
 * Build a hierarchical tree from an array of project objects.
 *
 * Algorithm:
 * 1. Build a trie by inserting each project's `directory`, split on `/`,
 *    filtering out empty segments.
 * 2. Compress: post-order traversal. If a node is NOT a project AND has
 *    exactly ONE child, merge by joining segments with `/`.
 * 3. Convert Map children to sorted arrays (alphabetically by segment).
 * 4. If the virtual root was compressed (all projects share a common prefix),
 *    return it as the sole root. Otherwise return root's children as top-level roots.
 *
 * @param {Array<Object>} projects - Array of project objects, each with a `directory` string field
 * @returns {Array<ProjectTreeNode>} Array of root tree nodes
 */
export function buildProjectTree(projects) {
    if (!projects || projects.length === 0) {
        return []
    }

    // 1. Build trie
    const root = createTrieNode('')
    for (const project of projects) {
        if (project.directory) {
            insertIntoTrie(root, project)
        }
    }

    // 2. Compress
    const compressed = compressNode(root)

    // 3. Convert to sorted arrays
    // 4. If the root itself was compressed (non-empty segment), it becomes the sole root.
    //    Otherwise, return its children as top-level roots.
    if (compressed.segment !== '') {
        // The virtual root was compressed — all projects share a common prefix.
        // Return it as the sole root node, with a leading "/" since directories are absolute paths.
        const node = trieNodeToTreeNode(compressed)
        node.segment = '/' + node.segment
        assignPaths(node, '')
        return [node]
    }

    // Root was not compressed — return its children as top-level roots.
    // Prefix each root segment with "/" since directories are absolute paths.
    const roots = Array.from(compressed.children.values())
        .map(trieNodeToTreeNode)
        .map(node => ({ ...node, segment: '/' + node.segment }))
        .sort((a, b) => a.segment.localeCompare(b.segment))
    for (const root of roots) {
        assignPaths(root, '')
    }
    return roots
}

/**
 * Flatten a project tree into a flat array suitable for `<wa-select>` dropdowns.
 *
 * Performs a depth-first traversal, producing one item per node. Each item has:
 * - `isFolder`: true for intermediate folder nodes (no project)
 * - `segment`: display label
 * - `path`: full accumulated directory path of the node
 * - `project`: the project object, or null
 * - `depth`: nesting level (0 for top-level roots)
 * - `key`: unique key for v-for (project.id for project nodes, "__folder_N" for folders)
 *
 * @param {Array<ProjectTreeNode>} roots - Tree roots from `buildProjectTree()`
 * @returns {Array<FlatProjectTreeItem>} Flat array in depth-first order
 */
export function flattenProjectTree(roots) {
    const items = []
    let folderCounter = 0

    /**
     * Recursively traverse and flatten.
     * @param {ProjectTreeNode} node
     * @param {number} depth
     */
    function walk(node, depth) {
        const isFolder = node.project === null
        items.push({
            isFolder,
            segment: node.segment,
            path: node.path,
            project: node.project,
            depth,
            key: isFolder ? `__folder_${folderCounter++}` : node.project.id,
        })

        for (const child of node.children) {
            walk(child, depth + 1)
        }
    }

    for (const root of roots) {
        walk(root, 0)
    }

    return items
}
