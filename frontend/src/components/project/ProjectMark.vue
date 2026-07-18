<script setup>
// ProjectMark.vue — a project's visual identity: its icon image when it has
// one, else the generated color dot (the historical rendering). Replaces the
// duplicated `--dot-color` circle markup across the app. Size is driven by the
// CSS var `--project-mark-size` (default `--wa-space-m`), overridable per call
// site. See docs/plans/2026-07-17-project-icons-design.md.
defineProps({
    // Resolved icon URL (server-side, inheritance included) or null → color dot.
    iconUrl: { type: String, default: null },
    // Dot color used when there is no icon (null → the neutral empty dot).
    color: { type: String, default: null },
})
</script>

<template>
    <span class="project-mark">
        <img
            v-if="iconUrl"
            class="project-mark-icon"
            :src="iconUrl"
            alt=""
            loading="lazy"
            decoding="async"
        />
        <span
            v-else
            class="project-mark-dot"
            :style="color ? { '--dot-color': color } : null"
        ></span>
    </span>
</template>

<style scoped>
.project-mark {
    display: inline-flex;
    flex-shrink: 0;
    line-height: 0;
    /* Always use "m" size by default so the container for icons
    (m by default) and dots (s by default) use the same width) */
    width: var(--project-mark-icon-size, var(--project-mark-size, var(--wa-space-m)));
    justify-content: center;
}

.project-mark-dot {
    width: var(--project-mark-size, var(--wa-space-s));
    height: var(--project-mark-size, var(--wa-space-s));
    border-radius: 50%;
    box-sizing: border-box;
    border: 1px solid;
    background-color: var(--dot-color, transparent);
    border-color: var(--dot-color, var(--wa-color-border-quiet));
}

.project-mark-icon {
    width: var(--project-mark-icon-size, var(--project-mark-size, var(--wa-space-m)));
    height: var(--project-mark-icon-size, var(--project-mark-size, var(--wa-space-m)));
    max-width: unset; /* override any global `img { max-width: 100% }` reset */
    border-radius: 0; /* square — override any inherited/global rounding */
    object-fit: contain; /* whole image, centered, no crop and no distortion */
    display: block;
    box-sizing: border-box;
}
</style>
