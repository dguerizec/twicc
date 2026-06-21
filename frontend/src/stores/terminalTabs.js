import { defineStore } from 'pinia'

export const useTerminalTabsStore = defineStore('terminalTabs', {
    state: () => ({
        // contextKey → sorted array of terminal indices from backend
        indices: {},
        // contextKey → { terminalIndex: label } — labels from tmux user options
        labels: {},
        // contextKey → { terminalIndex: true } — "auto-attach into children" flags
        // from the @twicc_autoattach tmux user option (only truthy entries kept)
        autoAttach: {},
    }),
    actions: {
        setIndices(contextKey, terminalIndices) {
            this.indices[contextKey] = [...terminalIndices].sort((a, b) => a - b)
        },
        addIndex(contextKey, index) {
            if (!this.indices[contextKey]) {
                this.indices[contextKey] = [index]
                return
            }
            if (!this.indices[contextKey].includes(index)) {
                this.indices[contextKey] = [...this.indices[contextKey], index].sort((a, b) => a - b)
            }
        },
        removeIndex(contextKey, index) {
            if (this.indices[contextKey]) {
                this.indices[contextKey] = this.indices[contextKey].filter(i => i !== index)
            }
            if (this.labels[contextKey]) {
                delete this.labels[contextKey][index]
            }
            if (this.autoAttach[contextKey]) {
                delete this.autoAttach[contextKey][index]
            }
        },
        setLabels(contextKey, labelsMap) {
            this.labels[contextKey] = {}
            for (const [index, label] of Object.entries(labelsMap)) {
                if (label) {
                    this.labels[contextKey][Number(index)] = label
                }
            }
        },
        setLabel(contextKey, index, label) {
            if (!this.labels[contextKey]) {
                this.labels[contextKey] = {}
            }
            if (label) {
                this.labels[contextKey][index] = label
            } else {
                delete this.labels[contextKey][index]
            }
        },
        getLabel(contextKey, index) {
            return this.labels[contextKey]?.[index] || ''
        },
        setAutoAttachMap(contextKey, map) {
            this.autoAttach[contextKey] = {}
            for (const [index, enabled] of Object.entries(map || {})) {
                if (enabled) {
                    this.autoAttach[contextKey][Number(index)] = true
                }
            }
        },
        setAutoAttach(contextKey, index, enabled) {
            if (!this.autoAttach[contextKey]) {
                this.autoAttach[contextKey] = {}
            }
            if (enabled) {
                this.autoAttach[contextKey][index] = true
            } else {
                delete this.autoAttach[contextKey][index]
            }
        },
        isAutoAttach(contextKey, index) {
            return !!this.autoAttach[contextKey]?.[index]
        },
    },
})
