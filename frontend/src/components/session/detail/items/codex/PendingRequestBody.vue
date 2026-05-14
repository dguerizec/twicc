<script setup>
import { computed } from 'vue'

const props = defineProps({
    pendingRequest: { type: Object, required: true },
    isResponding: { type: Boolean, default: false },
})
const emit = defineEmits(['submit'])

// Codex tool_name: 'commandExecution' | 'fileChange' | 'permissions'.
const toolName = computed(() => props.pendingRequest.tool_name || 'unknown')

// Wire params (as injected by make_pending_request).
const toolInput = computed(() => props.pendingRequest.tool_input || {})

// commandExecution-specific fields
const command = computed(() => toolInput.value.command)
const cwd = computed(() => toolInput.value.cwd)
const reason = computed(() => toolInput.value.reason)
const commandActions = computed(() => toolInput.value.commandActions || [])
const networkApprovalContext = computed(() => toolInput.value.networkApprovalContext)
const proposedExecpolicyAmendment = computed(() => toolInput.value.proposedExecpolicyAmendment)
const proposedNetworkPolicyAmendments = computed(() => toolInput.value.proposedNetworkPolicyAmendments)

// fileChange-specific fields
const fileChanges = computed(() => {
    const payload = toolInput.value._item_payload
    if (!payload) return []
    const changes = payload.changes
    return Array.isArray(changes) ? changes : []
})

function formatCommandAction(action) {
    const type = action?.type
    if (type === 'read') {
        return action.path ? `Read: ${action.path}` : 'Read'
    }
    if (type === 'listFiles') {
        return action.path ? `List: ${action.path}` : 'List'
    }
    if (type === 'search') {
        return action.query ? `Search: ${action.query}` : 'Search'
    }
    return action?.command || 'Action'
}

// permissions-specific fields
const requestedPermissions = computed(() => toolInput.value.permissions || {})

// Whether the "Cancel turn" button is available for this tool_name.
// Permissions don't have a "cancel" wire variant per spec §1.1.c.
const supportsCancelTurn = computed(
    () => toolName.value === 'commandExecution' || toolName.value === 'fileChange',
)

function emitApprove(variant) {
    // `variant` is one of:
    // - 'once' | 'forSession'      (command / file)
    // - 'addAllowRule'              (command only, requires proposedExecpolicyAmendment)
    // - 'allowNetwork'              (command only, requires proposedNetworkPolicyAmendments)
    // - 'turn' | 'session'          (permissions)
    if (toolName.value === 'permissions') {
        emit('submit', {
            tool_name: 'permissions',
            permissions: requestedPermissions.value,
            scope: variant === 'session' ? 'session' : 'turn',
        })
        return
    }
    if (variant === 'forSession') {
        emit('submit', { tool_name: toolName.value, decision: 'acceptForSession' })
        return
    }
    if (variant === 'addAllowRule') {
        emit('submit', {
            tool_name: toolName.value,
            decision: {
                acceptWithExecpolicyAmendment: {
                    execpolicy_amendment: proposedExecpolicyAmendment.value,
                },
            },
        })
        return
    }
    if (variant === 'allowNetwork') {
        emit('submit', {
            tool_name: toolName.value,
            decision: {
                applyNetworkPolicyAmendment: {
                    network_policy_amendment: proposedNetworkPolicyAmendments.value,
                },
            },
        })
        return
    }
    // 'once' (default)
    emit('submit', { tool_name: toolName.value, decision: 'accept' })
}

function handleDeny() {
    if (toolName.value === 'permissions') {
        emit('submit', {
            tool_name: 'permissions',
            permissions: {},
            scope: 'turn',
        })
    } else {
        emit('submit', { tool_name: toolName.value, decision: 'decline' })
    }
}

function handleCancelTurn() {
    emit('submit', { tool_name: toolName.value, decision: 'cancel' })
}
</script>

<template>
    <div class="codex-pending-body">
        <!-- commandExecution rich body -->
        <template v-if="toolName === 'commandExecution'">
            <div class="codex-pending-section">
                <div class="codex-pending-summary">
                    <span class="codex-summary-label">Command</span>
                    <code class="codex-summary-code">{{ command }}</code>
                </div>
                <div v-if="cwd" class="codex-pending-summary">
                    <span class="codex-summary-label">cwd</span>
                    <code class="codex-summary-code">{{ cwd }}</code>
                </div>
                <div v-if="reason" class="codex-pending-reason">
                    <wa-icon name="comment" variant="classic"></wa-icon>
                    <span>{{ reason }}</span>
                </div>
                <div v-if="commandActions.length" class="codex-action-chips">
                    <wa-badge
                        v-for="(action, idx) in commandActions"
                        :key="idx"
                        variant="neutral"
                    >{{ formatCommandAction(action) }}</wa-badge>
                </div>
                <div v-if="networkApprovalContext" class="codex-pending-network">
                    <wa-icon name="globe" variant="classic"></wa-icon>
                    <span>
                        Wants network access to
                        <code>{{ networkApprovalContext.host }}</code>
                        via {{ networkApprovalContext.protocol || 'unknown' }}
                    </span>
                </div>
            </div>
        </template>

        <!-- fileChange rich body -->
        <template v-else-if="toolName === 'fileChange'">
            <div class="codex-pending-section">
                <div class="codex-pending-summary">
                    <span class="codex-summary-label">
                        Wants to modify {{ fileChanges.length }} file{{ fileChanges.length === 1 ? '' : 's' }}
                    </span>
                </div>
                <ul v-if="fileChanges.length" class="codex-file-list">
                    <li v-for="(change, idx) in fileChanges" :key="idx" class="codex-file-row">
                        <wa-badge
                            :variant="change.kind?.type === 'delete' ? 'danger'
                                : change.kind?.type === 'add' ? 'success' : 'neutral'"
                        >{{ change.kind?.type || 'update' }}</wa-badge>
                        <code class="codex-file-path">{{ change.path }}</code>
                    </li>
                </ul>
                <div v-if="reason" class="codex-pending-reason">
                    <wa-icon name="comment" variant="classic"></wa-icon>
                    <span>{{ reason }}</span>
                </div>
            </div>
        </template>

        <!-- permissions rich body -->
        <template v-else-if="toolName === 'permissions'">
            <div class="codex-pending-section">
                <div class="codex-pending-summary">
                    <span class="codex-summary-label">Requests additional permissions</span>
                </div>
                <ul class="codex-permission-list">
                    <li v-for="(value, key) in requestedPermissions" :key="key" class="codex-permission-row">
                        <code class="codex-permission-key">{{ key }}</code>
                        <span class="codex-permission-value">{{ JSON.stringify(value) }}</span>
                    </li>
                </ul>
                <div v-if="reason" class="codex-pending-reason">
                    <wa-icon name="comment" variant="classic"></wa-icon>
                    <span>{{ reason }}</span>
                </div>
            </div>
        </template>

        <!-- Unknown tool_name safety net -->
        <template v-else>
            <div class="codex-pending-section"><em>Unknown tool_name: {{ toolName }}</em></div>
        </template>

        <!-- Shared action row. Approve is a plain button for now; menus in Task 7. -->
        <div class="codex-pending-actions">
            <wa-button
                variant="danger"
                appearance="outlined"
                size="small"
                :disabled="isResponding"
                @click="handleDeny"
            >
                <wa-icon slot="start" name="xmark" variant="classic"></wa-icon>
                Deny
            </wa-button>
            <wa-button
                v-if="supportsCancelTurn"
                variant="neutral"
                appearance="outlined"
                size="small"
                :disabled="isResponding"
                @click="handleCancelTurn"
            >
                <wa-icon slot="start" name="stop" variant="classic"></wa-icon>
                Cancel turn
            </wa-button>
            <wa-dropdown placement="top-end">
                <wa-button
                    slot="trigger"
                    variant="brand"
                    size="small"
                    :disabled="isResponding"
                    caret
                >
                    <wa-icon slot="start" name="check" variant="classic"></wa-icon>
                    Approve
                </wa-button>

                <!-- command / file menu -->
                <template v-if="toolName === 'commandExecution' || toolName === 'fileChange'">
                    <wa-dropdown-item @click="emitApprove('once')">
                        <wa-icon slot="icon" name="check" variant="classic"></wa-icon>
                        Once
                    </wa-dropdown-item>
                    <wa-dropdown-item @click="emitApprove('forSession')">
                        <wa-icon slot="icon" name="rotate" variant="classic"></wa-icon>
                        For this session
                    </wa-dropdown-item>
                    <wa-dropdown-item
                        v-if="toolName === 'commandExecution' && proposedExecpolicyAmendment"
                        @click="emitApprove('addAllowRule')"
                    >
                        <wa-icon slot="icon" name="plus" variant="classic"></wa-icon>
                        + Add allow rule
                    </wa-dropdown-item>
                    <wa-dropdown-item
                        v-if="toolName === 'commandExecution' && proposedNetworkPolicyAmendments"
                        @click="emitApprove('allowNetwork')"
                    >
                        <wa-icon slot="icon" name="globe" variant="classic"></wa-icon>
                        + Allow network access
                    </wa-dropdown-item>
                </template>

                <!-- permissions menu -->
                <template v-else-if="toolName === 'permissions'">
                    <wa-dropdown-item @click="emitApprove('turn')">
                        <wa-icon slot="icon" name="clock" variant="classic"></wa-icon>
                        For this turn
                    </wa-dropdown-item>
                    <wa-dropdown-item @click="emitApprove('session')">
                        <wa-icon slot="icon" name="rotate" variant="classic"></wa-icon>
                        For this session
                    </wa-dropdown-item>
                </template>
            </wa-dropdown>
        </div>
    </div>
</template>

<style scoped>
.codex-pending-body {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
}

.codex-pending-section {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
    background: var(--wa-color-neutral-5);
    border-radius: var(--wa-border-radius-m);
    padding: var(--wa-space-s);
}

.codex-pending-summary {
    display: flex;
    align-items: baseline;
    gap: var(--wa-space-s);
    flex-wrap: wrap;
}

.codex-summary-label {
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-xs);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-weight: 600;
}

.codex-summary-code {
    font-family: var(--wa-font-family-mono);
    font-size: var(--wa-font-size-s);
    background: var(--wa-color-neutral-fill-quiet);
    padding: 2px var(--wa-space-2xs);
    border-radius: var(--wa-border-radius-s);
    word-break: break-all;
}

.codex-pending-reason {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}

.codex-pending-network {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}

.codex-action-chips {
    display: flex;
    gap: var(--wa-space-xs);
    flex-wrap: wrap;
}

.codex-file-list,
.codex-permission-list {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
}

.codex-file-row,
.codex-permission-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
}

.codex-file-path,
.codex-permission-key {
    font-family: var(--wa-font-family-mono);
    font-size: var(--wa-font-size-s);
    word-break: break-all;
}

.codex-permission-value {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

.codex-pending-actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: var(--wa-space-s);
}
</style>
