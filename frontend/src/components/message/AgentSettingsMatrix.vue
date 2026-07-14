<script setup>
// Provider × model × effort matrix. Data is assembled provider-agnostically in
// useSessionAgentSettings.matrixBlocks: rows are a provider's models, columns
// the shared effort ladder, each cell picks provider + model + effort in one
// click. Layout: one grid so every block's effort columns stay aligned; each
// provider's name runs vertically (read bottom-to-top) down the left of its own
// sub-matrix, with a divider between providers. Disabled cells are efforts the
// (provider, model) pair doesn't support; a dot marks the default cell; the
// current selection is highlighted; "Show older models" reveals non-latest rows.
import { computed, ref } from 'vue'

const props = defineProps({
    // [{ provider, label, icon, isCurrent, rows: [{ model, label, isLatest,
    //    cells: [{ effort, enabled, selected, isDefault, score }] }] }]
    // score is the benchmark score (integer 0..100) or null when there's no
    // benchmark data for that (model, effort).
    blocks: { type: Array, default: () => [] },
    // [{ effort, label }] — shared columns across every block
    effortColumns: { type: Array, default: () => [] },
})

const emit = defineEmits(['select'])

const showOldModels = ref(false)

const hasOldModels = computed(() =>
    props.blocks.some(b => b.rows.some(r => !r.isLatest)),
)

// Whether any cell is flagged as the default (dot). The per-session popover
// always has one; the per-provider defaults editor passes no default cell (the
// selected cell IS the default there), so the "default" legend entry is hidden.
const hasDefaultCell = computed(() =>
    props.blocks.some(b => b.rows.some(r => r.cells.some(c => c.isDefault))),
)

// Assign every element an explicit grid row/column so blocks share one grid
// (aligned effort columns) while each provider label spans its own rows. Column
// 1 = vertical provider label, column 2 = model label, columns 3.. = efforts;
// row 1 = effort headers. A non-latest row stays visible when it holds the
// current selection, so the highlighted cell never hides while collapsed.
const layout = computed(() => {
    const efforts = props.effortColumns.map((c, i) => ({ ...c, col: i + 3 }))
    let row = 2
    const blocks = props.blocks.map((block, bi) => {
        const dividerRow = bi > 0 ? row++ : null
        const vis = block.rows.filter(
            r => r.isLatest || showOldModels.value || r.cells.some(c => c.selected),
        )
        const labelRow = row
        const rows = vis.map(r => {
            const built = {
                model: r.model,
                name: r.name,
                version: r.version,
                isLatest: r.isLatest,
                row,
                cells: r.cells.map((cell, ci) => ({ ...cell, col: ci + 3 })),
            }
            row += 1
            return built
        })
        return {
            provider: block.provider,
            label: block.label,
            icon: block.icon,
            dividerRow,
            labelRow,
            labelSpan: Math.max(1, rows.length),
            rows,
        }
    })
    return { efforts, blocks }
})

const gridStyle = computed(() => ({
    gridTemplateColumns: `auto auto repeat(${props.effortColumns.length}, minmax(2.25rem, 1fr))`,
}))

function onCellClick(provider, model, cell) {
    if (!cell.enabled) return
    emit('select', { provider, model, effort: cell.effort })
}
</script>

<template>
    <div class="matrix">
        <div class="matrix-grid" :style="gridStyle">
            <!-- Effort header row (col 1-2 empty corner, efforts from col 3) -->
            <div class="matrix-corner" style="grid-column: 1 / 3; grid-row: 1"></div>
            <div
                v-for="e in layout.efforts"
                :key="e.effort"
                class="matrix-col-header"
                :style="{ gridColumn: e.col, gridRow: 1 }"
            >{{ e.label }}</div>

            <template v-for="block in layout.blocks" :key="block.provider">
                <div
                    v-if="block.dividerRow"
                    class="matrix-divider"
                    :style="{ gridColumn: '1 / -1', gridRow: block.dividerRow }"
                ></div>

                <!-- Vertical provider label (read bottom-to-top), spanning its
                     block's rows; the icon rides at the bottom, upright. -->
                <div
                    class="matrix-vlabel"
                    :style="{ gridColumn: 1, gridRow: `${block.labelRow} / span ${block.labelSpan}` }"
                >
                    <span class="matrix-vlabel-text">{{ block.label }}</span>
                    <wa-icon
                        v-if="block.icon"
                        class="matrix-vlabel-icon"
                        family="brands"
                        :name="block.icon"
                    ></wa-icon>
                </div>

                <template v-for="r in block.rows" :key="r.model">
                    <div
                        class="matrix-row-header"
                        :class="{ old: !r.isLatest }"
                        :style="{ gridColumn: 2, gridRow: r.row }"
                    >
                        <span>{{ r.name }}</span>
                        <span v-if="r.version" class="matrix-model-version">{{ r.version }}</span>
                    </div>
                    <button
                        v-for="cell in r.cells"
                        :key="cell.effort"
                        type="button"
                        class="matrix-cell"
                        :class="{ selected: cell.selected, unavailable: !cell.enabled, 'top-solid': cell.borderStyle === 'solid', 'top-dashed': cell.borderStyle === 'dashed', 'score-high': cell.enabled && cell.score != null && cell.score > 80 }"
                        :style="{ gridColumn: cell.col, gridRow: r.row, '--score-alpha': cell.enabled && cell.score != null ? cell.score / 100 : 0 }"
                        :disabled="!cell.enabled"
                        :aria-label="`${block.label} · ${r.name} ${r.version} · ${cell.effort}${cell.enabled ? ` · score ${cell.score ?? 'unknown'}` : ''}`"
                        :aria-pressed="cell.selected"
                        @click="onCellClick(block.provider, r.model, cell)"
                    >
                        <span v-if="cell.isDefault" class="matrix-default-dot"></span>
                        <span v-if="cell.enabled" class="matrix-cell-score" :class="{ 'no-score': cell.score == null }">{{ cell.score ?? '?' }}</span>
                        <span v-if="cell.selected" class="matrix-cell-check">
                            <wa-icon name="check"></wa-icon>
                        </span>
                    </button>
                </template>
            </template>
        </div>

        <div class="matrix-footer">
            <button
                v-if="hasOldModels"
                type="button"
                class="matrix-old-toggle"
                @click="showOldModels = !showOldModels"
            >
                {{ showOldModels ? 'Hide older models' : 'Show older models' }}
            </button>
            <span class="matrix-legend">
                <span v-if="hasDefaultCell" class="matrix-legend-item"><span class="matrix-default-dot"></span> default</span>
                <span class="matrix-legend-item"><wa-icon class="matrix-legend-check" name="check"></wa-icon> selected</span>
            </span>
        </div>
    </div>
</template>

<style scoped>
.matrix {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
}

.matrix-grid {
    display: grid;
    gap: 3px;
    align-items: stretch;
}

.matrix-col-header {
    font-size: var(--wa-font-size-xs);
    font-weight: var(--wa-font-weight-semibold);
    color: var(--wa-color-text-quiet);
    text-align: center;
    padding-bottom: var(--wa-space-3xs);
    align-self: end;
}

.matrix-divider {
    border-top: 1px solid var(--wa-color-surface-border);
    margin: var(--wa-space-2xs) 0;
    height: 0;
}

.matrix-vlabel {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-s);
    padding-right: var(--wa-space-2xs);
}

.matrix-vlabel-text {
    /* Vertical, read bottom-to-top (rotate 180° flips vertical-rl upward). */
    writing-mode: vertical-rl;
    transform: rotate(180deg);
    white-space: nowrap;
    font-size: var(--wa-font-size-s);
    font-weight: var(--wa-font-weight-semibold);
    color: var(--wa-color-text-normal);
}

/* Sits at the bottom of the vertical label (the start of the bottom-to-top
   read), kept upright since it's a sibling of — not inside — the rotated text. */
.matrix-vlabel-icon {
    font-size: 1rem;
    color: var(--wa-color-text-normal);
}

.matrix-row-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--wa-space-xs);
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-normal);
    padding-right: var(--wa-space-xs);
    white-space: nowrap;
}

.matrix-model-version {
    color: var(--wa-color-text-quiet);
}

.matrix-row-header.old {
    color: var(--wa-color-text-quiet);
}

.matrix-cell {
    /* Shared foreground for the score, the selected check and the default dot.
       Flipped to the surface color above score 80 in light mode only (see
       .score-high) so it stays readable on the strong fill. */
    --cell-fg: var(--wa-color-text-normal);
    position: relative;
    box-sizing: border-box;
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 1.9rem;
    border: 1px solid var(--wa-color-neutral-border-normal, var(--wa-color-surface-border));
    border-radius: var(--wa-border-radius-s);
    /* Score-driven fill: brand at alpha = score/100 (0 -> transparent, 100 ->
       full brand). --score-alpha is set inline per cell. */
    background-color: color-mix(in srgb, var(--wa-color-brand-60) calc(var(--score-alpha, 0) * 100%), transparent);
    cursor: pointer;
    padding: 0;
    transition: background-color 0.1s, border-color 0.1s;
}

/* Above score 80, in light mode only, flip the shared foreground to the surface
   color so the number stays readable on the strong fill. In dark mode
   text-normal already reads on the fill, so we leave it (no inversion). */
:root:not(.wa-dark) .matrix-cell.score-high {
    --cell-fg: var(--wa-color-surface-default);
}

/* Benchmark score (integer 0..100, or "?" when there's no benchmark data). */
.matrix-cell-score {
    font-size: var(--wa-font-size-xs);
    font-variant-numeric: tabular-nums;
    line-height: 1;
    color: var(--cell-fg);
}

/* "?" placeholder for cells without benchmark data — kept quiet. */
.matrix-cell-score.no-score {
    color: var(--wa-color-text-quiet);
}

.matrix-cell.selected .matrix-cell-score {
    font-weight: var(--wa-font-weight-semibold);
}

.matrix-cell:hover:not(:disabled) {
    border-color: var(--wa-color-brand-border-normal, var(--wa-color-brand-60));
}

/* Best score of a provider block: solid border for the user's default provider
   (or a lone provider), dashed for each other provider. Drawn as an overlay
   ring (::after, out of flow) so the thick border never shifts the grid — every
   cell keeps its 1px base border — and `outline` stays free for keyboard focus.
   inset: -2px bleeds the ring 1px into the 3px grid gap instead of shrinking the
   fill; the ring paints over the base border underneath. */
.matrix-cell.top-solid::after,
.matrix-cell.top-dashed::after {
    content: "";
    position: absolute;
    inset: -2px;
    border-radius: inherit;
    pointer-events: none;
    border: 3px solid var(--wa-color-text-normal);
}

.matrix-cell.top-dashed::after {
    border-style: dashed;
}

/* Selection marker — green check in the bottom-right corner (score-sized).
   The selection no longer draws a border: that's reserved for later use. */
.matrix-cell-check {
    position: absolute;
    right: 3px;
    bottom: 2px;
    display: inline-flex;
    font-size: var(--wa-font-size-s);
    line-height: 1;
    color: var(--cell-fg);
    transform: translateX(3px);
}

.matrix-cell.unavailable {
    cursor: not-allowed;
    background: repeating-linear-gradient(
        -45deg,
        transparent,
        transparent 4px,
        var(--wa-color-neutral-fill-quiet, rgba(128, 128, 128, 0.08)) 4px,
        var(--wa-color-neutral-fill-quiet, rgba(128, 128, 128, 0.08)) 8px
    );
    border-color: transparent;
    opacity: 0.55;
}

.matrix-default-dot {
    position: absolute;
    top: 3px;
    right: 3px;
    width: 6px;
    height: 6px;
    border-radius: 50%;
    /* In a cell: matches the score (inverts above 80). In the legend (no
       --cell-fg ancestor): falls back to the normal text color. */
    background: var(--cell-fg, var(--wa-color-text-normal));
}

.matrix-footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--wa-space-s);
    flex-wrap: wrap;
}

.matrix-old-toggle {
    background: none;
    border: none;
    padding: 0;
    cursor: pointer;
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-brand-60);
    &:hover {
        text-decoration: underline;
    }
}

.matrix-legend {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-s);
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    margin-left: auto;

    .matrix-default-dot {
        position: static;
    }
}

.matrix-legend-item {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-2xs);
}

.matrix-legend-check {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-normal);
}
</style>
