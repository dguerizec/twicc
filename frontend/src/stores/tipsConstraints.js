/**
 * Pure constraint evaluation, isolated from the tips store so it can be
 * imported from anywhere (scheduler composable, settings panel) without
 * loading the full store machinery.
 *
 * `env` shape :
 *   { platform: 'mobile'|'desktop', os: 'mac'|'linux'|'windows'|null,
 *     enabledProviders: string[] }
 */
export function isTipAvailable(tip, env) {
    if (tip.platform && !tip.platform.includes(env.platform)) return false
    if (tip.os) {
        if (env.os === null) return false
        if (!tip.os.includes(env.os)) return false
    }
    if (tip.providers_any && tip.providers_any.length > 0) {
        const any = tip.providers_any.some((p) => env.enabledProviders.includes(p))
        if (!any) return false
    }
    if (tip.providers_all && tip.providers_all.length > 0) {
        const all = tip.providers_all.every((p) => env.enabledProviders.includes(p))
        if (!all) return false
    }
    return true
}
