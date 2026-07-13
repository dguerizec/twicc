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
                        :class="{ selected: cell.selected, unavailable: !cell.enabled }"
                        :style="{ gridColumn: cell.col, gridRow: r.row }"
                        :disabled="!cell.enabled"
                        :aria-label="`${block.label} · ${r.name} ${r.version} · ${cell.effort}${cell.enabled ? ` · score ${cell.score ?? 'unknown'}` : ''}`"
                        :aria-pressed="cell.selected"
                        @click="onCellClick(block.provider, r.model, cell)"
                    >
                        <span v-if="cell.isDefault" class="matrix-default-dot"></span>
                        <span v-if="cell.enabled" class="matrix-cell-score">{{ cell.score ?? '?' }}</span>
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
            <span class="matrix-legend"><span class="matrix-default-dot"></span> default</span>
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
    position: relative;
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 1.9rem;
    border: 1px solid var(--wa-color-neutral-border-normal, var(--wa-color-surface-border));
    border-radius: var(--wa-border-radius-s);
    background: var(--wa-color-neutral-fill-quiet, transparent);
    cursor: pointer;
    padding: 0;
    transition: background 0.1s, border-color 0.1s;
}

/* Benchmark score (integer 0..100, or "?" when there's no benchmark data). */
.matrix-cell-score {
    font-size: var(--wa-font-size-xs);
    font-variant-numeric: tabular-nums;
    line-height: 1;
    color: var(--wa-color-text-normal);
}

.matrix-cell.selected .matrix-cell-score {
    font-weight: var(--wa-font-weight-semibold);
}

.matrix-cell:hover:not(:disabled) {
    background: var(--wa-color-brand-fill-quiet);
    border-color: var(--wa-color-brand-border-normal, var(--wa-color-brand-60));
}

.matrix-cell.selected {
    background: var(--wa-color-brand-fill-normal, var(--wa-color-brand-fill-quiet));
    border-color: var(--wa-color-brand-60);
    box-shadow: inset 0 0 0 1px var(--wa-color-brand-60);
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
    background: var(--wa-color-brand-60);
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
    gap: var(--wa-space-2xs);
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    margin-left: auto;

    .matrix-default-dot {
        position: static;
    }
}
</style>
