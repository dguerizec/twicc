<script setup>
// Benchmark score weighting, shown right below the model × effort matrix. Two
// modes, sharing the same benchmarkWeights store (so switching never resets):
//  - collapsed (default): a single "Task difficulty" slider that proxies the
//    Capability weight — no % readout (a difficulty in percent is meaningless);
//  - expanded ("More controls"): the full block — preset profiles, the three
//    Capability / Economy / Speed sliders (%, lock, always sum 100), and the
//    "When Capability moves, favor:" selector.
// A shared "Auto-select best" switch line (both modes, at the end) toggles the
// store flags; the parent popover runs the actual matrix pick.
// All logic (latent normalization, exclusive lock, priority split, presets)
// lives in the store; this component is the presentation layer.
import { ref, useId } from 'vue'
import { useBenchmarkWeightsStore, WEIGHT_PRESETS } from '../../stores/benchmarkWeights'
import AppTooltip from '../ui/AppTooltip.vue'
import HelpIconButton from '../help/HelpIconButton.vue'

defineProps({
    // Providers shown in the matrix; the "Default provider only" switch only
    // surfaces when there is more than one.
    providerCount: { type: Number, default: 1 },
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
                <HelpIconButton help-key="model-effort-score" label="How scores are computed" />
            </div>
        </template>

        <!-- Expanded ("More controls"): the full weighting block. -->
        <template v-else>
            <div class="weights-head">
                <span class="weights-title">
                    Scoring priorities
                    <HelpIconButton help-key="model-effort-score" label="How scores are computed" />
                </span>
                <div class="weights-presets">
                    <template v-for="p in presets" :key="p.id">
                        <wa-button
                            :id="`${uid}-preset-${p.id}`"
                            size="small"
                            :appearance="isActivePreset(p) ? 'filled' : 'outlined'"
                            :variant="isActivePreset(p) ? 'brand' : 'neutral'"
                            @click="store.applyPreset(p.capability, p.economy, p.spd)"
                        >{{ p.label }}</wa-button>
                        <AppTooltip v-if="p.tooltip" :for="`${uid}-preset-${p.id}`">{{ p.tooltip }}</AppTooltip>
                    </template>
                </div>
            </div>

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

        <!-- Auto-select controls: shown in both modes, always at the end. -->
        <div class="weights-autoselect">
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

        <button v-if="showAll" type="button" class="weights-toggle" @click="showAll = false">Fewer controls</button>

        <!-- Closes the weighting block in both modes. -->
        <wa-divider class="weights-divider"></wa-divider>
    </div>
</template>

<style scoped>
.weights {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
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
    align-self: flex-start;
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

/* On the collapsed row the link sits inline, after the slider. */
.weights-simple .weights-toggle {
    align-self: center;
}

/* Closes the expanded block; the flex gap handles the spacing. */
.weights-divider {
    margin: 0;
}

/* Auto-select switch line — same wrap + gaps as the popover's switch row. */
.weights-autoselect {
    display: flex;
    flex-wrap: wrap;
    column-gap: var(--wa-space-m);
    row-gap: var(--wa-space-xs);
}

/* Collapsed mode: tighten the Task difficulty → auto-select gap. */
.weights-simple + .weights-autoselect {
    margin-top: calc(var(--wa-space-2xs) - var(--wa-space-s));
}

.weights-head {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
}

.weights-title {
    font-size: var(--wa-font-size-s);
    font-weight: var(--wa-font-weight-semibold);
    color: var(--wa-color-text-normal);
}

.weights-presets {
    display: flex;
    flex-wrap: wrap;
    gap: var(--wa-space-2xs);
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
    gap: var(--wa-space-xs);
    flex-wrap: wrap;
}

.weights-favor-label {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-normal);
    white-space: nowrap;
}

.weights-favor-select {
    min-width: 9rem;
}
</style>
