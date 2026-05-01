/**
 * Base class for per-provider frontend helpers.
 *
 * Mirrors the backend `BaseProviderHelpers` pattern: each provider ships a
 * subclass that overrides only the behaviours that differ from the neutral
 * defaults defined here. Generic frontend code resolves the right instance
 * via the registry in `providers/index.js` and never branches on provider
 * identity itself.
 */
export class BaseProviderHelpers {
    static provider = null

    /**
     * Whether the current frontend state allows sending a message to a
     * session of this provider. Default: always allowed. Providers override
     * to gate on auth, quota, or any other prerequisite.
     */
    canSendMessage() {
        return true
    }

    /**
     * Built-in slash commands provided by the provider's runtime/CLI.
     * Returned items are merged with the user-defined commands fetched
     * from the backend by the slash command picker. Default: no built-ins.
     */
    getBuiltInSlashCommands() {
        return []
    }
}
