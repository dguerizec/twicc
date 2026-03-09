/**
 * useCommandRegistry composable — Singleton registry for command palette commands.
 *
 * Manages command registration, filtering by context (`when()` guards),
 * grouping by category, and palette open/close state.
 *
 * Commands are registered decentrally by components and stores:
 *
 *   import { useCommandRegistry } from '@/composables/useCommandRegistry'
 *
 *   const { registerCommands, unregisterCommands } = useCommandRegistry()
 *
 *   registerCommands([
 *     {
 *       id: 'session.archive',
 *       label: 'Archive Session',
 *       icon: 'box-archive',
 *       category: 'session',
 *       action: () => { ... },
 *       when: () => !!currentSession.value,
 *     },
 *   ])
 *
 * Command schema:
 *   {
 *     id: string,              // unique identifier (e.g. 'session.archive')
 *     label: string,           // display label
 *     icon: string,            // Web Awesome icon name (Font Awesome 6 kebab-case)
 *     category: string,        // grouping key (one of CATEGORIES keys)
 *     action: () => void,      // executed on selection (for commands without items)
 *     when: () => boolean,     // optional, controls visibility (default: always visible)
 *     items: () => Array<{ id, label, action, active? }>,  // optional, for nested sub-selection
 *     toggled: () => boolean,  // optional, for toggle commands (shows on/off state)
 *   }
 */

import { ref, shallowRef, computed } from 'vue'

/**
 * Category definitions in display order.
 * Used to group and sort commands in the palette UI.
 */
export const CATEGORIES = [
    { key: 'navigation', label: 'Navigation' },
    { key: 'session', label: 'Session' },
    { key: 'creation', label: 'Creation' },
    { key: 'display', label: 'Display' },
    { key: 'claude', label: 'Claude Defaults' },
    { key: 'ui', label: 'UI' },
]

const VALID_CATEGORY_KEYS = new Set(CATEGORIES.map((c) => c.key))

// ---------------------------------------------------------------------------
// Module-level singleton state
// ---------------------------------------------------------------------------

/**
 * @type {import('vue').ShallowRef<Map<string, object>>} id -> command
 * Uses shallowRef to avoid deep-reactive wrapping of command objects
 * (which contain functions like action/when/items/toggled).
 */
const commandMap = shallowRef(new Map())

/** Whether the command palette overlay is open */
const isOpen = ref(false)

/**
 * Bumped to force re-evaluation of `when()` conditions.
 * Referenced inside the `availableCommands` computed so Vue tracks it as a
 * dependency and recomputes whenever external context changes.
 */
const contextVersion = ref(0)

// ---------------------------------------------------------------------------
// Computed
// ---------------------------------------------------------------------------

/**
 * Commands whose `when()` guard (if any) returns true.
 * Re-evaluated when commandMap or contextVersion changes.
 */
const availableCommands = computed(() => {
    // Reference contextVersion so Vue tracks it as a dependency
    void contextVersion.value

    const result = []
    for (const command of commandMap.value.values()) {
        if (command.when && !command.when()) continue
        result.push(command)
    }
    return result
})

/**
 * Available commands grouped by category, following CATEGORIES display order.
 * Empty categories are omitted.
 */
const commandsByCategory = computed(() => {
    const groups = []
    // Build a lookup: category key -> array of commands
    const byCategoryKey = new Map()
    for (const command of availableCommands.value) {
        let list = byCategoryKey.get(command.category)
        if (!list) {
            list = []
            byCategoryKey.set(command.category, list)
        }
        list.push(command)
    }
    // Iterate in CATEGORIES order, skip empty
    for (const category of CATEGORIES) {
        const commands = byCategoryKey.get(category.key)
        if (commands && commands.length > 0) {
            groups.push({ ...category, commands })
        }
    }
    return groups
})

// ---------------------------------------------------------------------------
// Composable
// ---------------------------------------------------------------------------

export function useCommandRegistry() {

    /**
     * Register a single command. If a command with the same id already exists
     * it is silently replaced.
     * @param {object} command
     */
    function registerCommand(command) {
        if (!VALID_CATEGORY_KEYS.has(command.category)) {
            console.warn(`[CommandRegistry] Unknown category "${command.category}" for command "${command.id}"`)
        }
        const next = new Map(commandMap.value)
        next.set(command.id, command)
        commandMap.value = next
    }

    /**
     * Register multiple commands at once (single reactivity trigger).
     * @param {object[]} commands
     */
    function registerCommands(commands) {
        const next = new Map(commandMap.value)
        for (const command of commands) {
            if (!VALID_CATEGORY_KEYS.has(command.category)) {
                console.warn(`[CommandRegistry] Unknown category "${command.category}" for command "${command.id}"`)
            }
            next.set(command.id, command)
        }
        commandMap.value = next
    }

    /**
     * Remove a command by id.
     * @param {string} id
     */
    function unregisterCommand(id) {
        const next = new Map(commandMap.value)
        next.delete(id)
        commandMap.value = next
    }

    /**
     * Remove multiple commands at once (single reactivity trigger).
     * @param {string[]} ids
     */
    function unregisterCommands(ids) {
        const next = new Map(commandMap.value)
        for (const id of ids) {
            next.delete(id)
        }
        commandMap.value = next
    }

    /**
     * Open the command palette.
     * Bumps contextVersion first so that `when()` guards are freshly evaluated
     * before the UI reads `availableCommands`.
     */
    function openPalette() {
        contextVersion.value++
        isOpen.value = true
    }

    /** Close the command palette. */
    function closePalette() {
        isOpen.value = false
    }

    /**
     * Bump contextVersion to force re-evaluation of `when()` guards.
     * Useful when external state changes (e.g. route navigation, selection change)
     * that may affect command visibility.
     */
    function bumpContext() {
        contextVersion.value++
    }

    return {
        // State
        isOpen,

        // Computed
        availableCommands,
        commandsByCategory,

        // Methods
        registerCommand,
        registerCommands,
        unregisterCommand,
        unregisterCommands,
        openPalette,
        closePalette,
        bumpContext,
    }
}
