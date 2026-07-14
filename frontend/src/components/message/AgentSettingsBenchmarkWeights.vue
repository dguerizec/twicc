<script setup>
// Benchmark score weighting, shown right below the model × effort matrix. Two
// modes, sharing the same benchmarkWeights store (so switching never resets):
//  - collapsed (default): a single "Task difficulty" slider that proxies the
//    Capability weight — no % readout (a difficulty in percent is meaningless);
//  - expanded ("More controls"): the three Capability / Economy / Speed sliders
//    (%, lock, always sum 100) and the "When Capability moves, favor:" selector.
// The "Auto-select best" switch line and the preset-profile links (expanded
// only) sit at the end; the parent popover runs the actual matrix pick. Score
// help lives in the "Model & effort" heading above the matrix, not here.
// All logic (latent normalization, exclusive lock, priority split, presets)
// lives in the store; this component is the presentation layer.
import { ref, useId } from 'vue'
import { useBenchmarkWeightsStore, WEIGHT_PRESETS } from '../../stores/benchmarkWeights'
import AppTooltip from '../ui/AppTooltip.vue'

defineProps({
    // Providers shown in the matrix; the "Default provider only" switch only
    // surfaces when there is more than one.
    providerCount: { type: Number, default: 1 },
    // Whether to render the "Auto-select best" line. The per-session popover
    // wants it (default); the per-provider defaults editor hides it — the
    // benchmarkWeights store is global and the always-mounted popover watches it,
    // so a Settings-side auto-select would silently mutate the live session.
    // The sliders stay in both surfaces (they only re-rank scores).
    showAutoSelect: { type: Boolean, default: true },
})

const store = useBenchmarkWeightsStore()
const uid = useId()

const showAll = ref(false)

const presets = WEIGHT_PRESETS

const rows = [
    { key: 'capability', label: 'Capability' },
    { key: 'economy', label: 'Economy' },
    { key: 'spd', label: 'Speed' },
]

const favorOptions = [
    { value: 'proportional', label: 'Proportional' },
    { value: 'economy', label: 'Economy' },
    { value: 'spd', label: 'Speed' },
]

function onSlide(key, event) {
    store.onDrag(key, Number(event.target.value))
}

function onFavorChange(event) {
    store.setPriority(event.target.value)
}

// Collapse back to the single Task difficulty slider. That slider proxies the
// Capability weight, so a lock left on Capability would freeze the only control
// shown — release it on the way out.
function collapseControls() {
    if (store.locks.capability) store.toggleLock('capability')
    showAll.value = false
}

// A preset is "active" when no lock is set and the displays match it exactly.
function isActivePreset(p) {
    return !store.anyLock
        && store.display.capability === p.capability
        && store.display.economy === p.economy
        && store.display.spd === p.spd
}
</script>

<template>
    <div class="weights">
        <!-- Collapsed (default): one "Task difficulty" slider proxying the
             Capability weight; no % readout. -->
        <template v-if="!showAll">
            <div class="weights-simple">
                <span class="weight-label">Task difficulty</span>
                <wa-slider
                    class="weight-slider"
                    size="small"
                    :min.prop="0"
                    :max.prop="100"
                    :step.prop="1"
                    :value.prop="store.display.capability"
                    :disabled.prop="store.locks.capability"
                    aria-label="Task difficulty"
                    @input="onSlide('capability', $event)"
                ></wa-slider>
                <button type="button" class="weights-toggle" @click="showAll = true">More controls</button>
            </div>
        </template>

        <!-- Expanded ("More controls"): "Fewer controls" (top-right), then the
             three weight sliders, the preset links, and the favor row. -->
        <template v-else>
            <button type="button" class="weights-toggle" @click="collapseControls">Fewer controls</button>

            <div class="weights-rows">
                <div
                    v-for="row in rows"
                    :key="row.key"
                    class="weight-row"
                    :class="{ locked: store.locks[row.key] }"
                >
                    <span class="weight-label">{{ row.label }}</span>
                    <wa-slider
                        class="weight-slider"
                        size="small"
                        :min.prop="0"
                        :max.prop="100"
                        :step.prop="1"
                        :value.prop="store.display[row.key]"
                        :disabled.prop="store.locks[row.key]"
                        :aria-label="`${row.label} weight`"
                        @input="onSlide(row.key, $event)"
                    ></wa-slider>
                    <span class="weight-value">{{ store.display[row.key] }}%</span>
                    <button
                        type="button"
                        class="weight-lock"
                        :class="{ active: store.locks[row.key] }"
                        :aria-pressed="store.locks[row.key]"
                        :aria-label="store.locks[row.key] ? `Unlock ${row.label}` : `Lock ${row.label}`"
                        @click="store.toggleLock(row.key)"
                    >
                        <wa-icon :name="store.locks[row.key] ? 'lock' : 'lock-open'"></wa-icon>
                    </button>
                </div>
            </div>

            <!-- Preset profiles as links, prefixed by a "Presets" label. The
                 active profile is styled distinctly. -->
            <div class="weights-presets">
                <span class="weights-presets-label">Presets</span>
                <template v-for="p in presets" :key="p.id">
                    <button
                        :id="`${uid}-preset-${p.id}`"
                        type="button"
                        class="weights-preset-link"
                        :class="{ active: isActivePreset(p) }"
                        :aria-pressed="isActivePreset(p)"
                        @click="store.applyPreset(p.capability, p.economy, p.spd)"
                    >{{ p.label }}</button>
                    <AppTooltip v-if="p.tooltip" :for="`${uid}-preset-${p.id}`">{{ p.tooltip }}</AppTooltip>
                </template>
            </div>

            <div class="weights-favor">
                <label class="weights-favor-label" :for="`${uid}-favor`">When Capability moves, favor:</label>
                <wa-select
                    :id="`${uid}-favor`"
                    class="weights-favor-select"
                    size="small"
                    :value.prop="store.priority"
                    @change="onFavorChange"
                >
                    <wa-option
                        v-for="o in favorOptions"
                        :key="o.value"
                        :value="o.value"
                    >{{ o.label }}</wa-option>
                </wa-select>
            </div>

        </template>

        <!-- Auto-select controls: shown in both modes, always at the end.
             Hidden where showAutoSelect is false (per-provider defaults editor). -->
        <div v-if="showAutoSelect" class="weights-autoselect">
            <wa-switch
                size="small"
                :checked="store.autoSelectBest"
                @change="store.autoSelectBest = $event.target.checked"
            >Auto-select best</wa-switch>
            <wa-switch
                v-if="store.autoSelectBest && providerCount > 1"
                size="small"
                :checked="store.defaultProviderOnly"
                @change="store.defaultProviderOnly = $event.target.checked"
            >Default provider only</wa-switch>
        </div>

        <!-- Closes the weighting block in both modes. -->
        <wa-divider class="weights-divider"></wa-divider>
    </div>
</template>

<style scoped>
.weights {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
}

/* Collapsed mode: the single Task difficulty row. */
.weights-simple {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
}

.weights-simple .weight-slider {
    flex: 1;
}

/* More / Fewer controls link. */
.weights-toggle {
    align-self: flex-end;
    white-space: nowrap;
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

/* The "Fewer controls" toggle (expanded mode — a direct child of .weights, not
   the collapsed one nested in .weights-simple) sits at the top: reset the WA
   native-button forced height so it stays a compact link. */
.weights > .weights-toggle {
    height: auto;
}

/* On the collapsed row the link sits inline, after the slider. */
.weights-simple .weights-toggle {
    align-self: center;
}

/* Closes the weighting block; a little breathing room above it. */
.weights-divider {
    margin: var(--wa-space-s) 0 0;
}

/* Auto-select switch line — same wrap + gaps as the popover's switch row. */
.weights-autoselect {
    display: flex;
    flex-wrap: wrap;
    column-gap: var(--wa-space-m);
    row-gap: var(--wa-space-xs);
}

/* Collapsed mode: tighten the Task difficulty → auto-select gap (keep it at 2xs
   now that the base .weights gap is xs). */
.weights-simple + .weights-autoselect {
    margin-top: calc(var(--wa-space-2xs) - var(--wa-space-xs));
}

/* Preset-profile links, on one wrapping line prefixed by a "Presets" label. */
.weights-presets {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    column-gap: var(--wa-space-s);
    row-gap: var(--wa-space-2xs);
}

.weights-presets-label {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-normal);
    white-space: nowrap;
}

/* Each profile as a text link (compact — reset the WA-less native button). The
   active profile reads bolder + underlined to stand apart from the rest. */
.weights-preset-link {
    background: none;
    border: none;
    padding: 0;
    cursor: pointer;
    white-space: nowrap;
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-brand-60);
    text-decoration: none;
}

.weights-preset-link:hover {
    text-decoration: underline;
}

.weights-preset-link.active {
    font-weight: var(--wa-font-weight-semibold);
    text-decoration: underline;
}

.weights-rows {
    display: grid;
    grid-template-columns: auto 1fr auto auto;
    align-items: center;
    gap: var(--wa-space-2xs) var(--wa-space-xs);
}

.weight-row {
    display: contents;
}

.weight-label {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-normal);
    white-space: nowrap;
}

.weight-row.locked .weight-label {
    color: var(--wa-color-text-quiet);
}

.weight-slider {
    min-width: 6rem;
}

.weight-value {
    font-size: var(--wa-font-size-s);
    font-variant-numeric: tabular-nums;
    color: var(--wa-color-text-normal);
    text-align: right;
    min-width: 2.75rem;
}

/* Compact icon toggle — reset the WA native-button height/inline sizing. */
.weight-lock {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    height: auto;
    padding: var(--wa-space-3xs);
    border: none;
    background: none;
    border-radius: var(--wa-border-radius-s);
    color: var(--wa-color-text-quiet);
    cursor: pointer;
    transition: color 0.1s, background 0.1s;
}

.weight-lock:hover {
    color: var(--wa-color-text-normal);
    background: var(--wa-color-neutral-fill-quiet);
}

.weight-lock.active {
    color: var(--wa-color-brand-60);
}

.weights-favor {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    column-gap: var(--wa-space-m);
    row-gap: var(--wa-space-s);
}

.weights-favor-label {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-normal);
    white-space: nowrap;
}

.weights-favor-select {
    width: 9rem;
}
</style>
