<script setup>
// Score weighting sliders shown right below the model × effort matrix. The
// three weights (Capability / Economy / Speed) always sum to 100 and feed the
// benchmark score; changing them recomputes the matrix scores live. All logic
// (latent-vector normalization, exclusive lock, priority split, presets) lives
// in the benchmarkWeights store — this component is the presentation layer.
import { useId } from 'vue'
import { useBenchmarkWeightsStore, WEIGHT_PRESETS } from '../../stores/benchmarkWeights'
import AppTooltip from '../ui/AppTooltip.vue'

const store = useBenchmarkWeightsStore()
const uid = useId()

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
        <div class="weights-head">
            <span class="weights-title">Score weighting</span>
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
    </div>
</template>

<style scoped>
.weights {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
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
