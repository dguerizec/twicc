<script setup>
// The network-broker consent prompt (design §9). Dumb + controlled: it shows
// whenever `prompt` is set and emits the user's choice. The parent owns the
// pending fetch's promise and settles it (idempotently) on `decision`.
const props = defineProps({
    // { host, ip, kind, canRemember } | null
    prompt: { type: Object, default: null },
})
const emit = defineEmits(['decision'])

const KIND_LABEL = {
    public: 'a public site',
    loopback: "this server's own machine (localhost)",
    lan: "this server's local network",
}
</script>

<template>
    <!-- `.self`: only the dialog's own wa-hide means "dismissed" (→ deny); a
         nested wa-* event must never be read as a decision. Dismiss == deny is
         the safe default. -->
    <wa-dialog
        :open="!!prompt"
        label="Network request"
        @wa-hide.self="emit('decision', 'deny')"
    >
        <div v-if="prompt" class="broker-prompt">
            <p>This artifact wants to connect to:</p>
            <p class="broker-prompt-host"><strong>{{ prompt.host }}</strong></p>
            <p class="broker-prompt-target">
                → resolves to <code>{{ prompt.ip }}</code> ({{ KIND_LABEL[prompt.kind] || prompt.kind }})
            </p>
            <wa-callout v-if="prompt.kind !== 'public'" variant="warning">
                <wa-icon slot="icon" name="triangle-exclamation"></wa-icon>
                This points at the machine TwiCC runs on — not a public website.
            </wa-callout>
        </div>

        <div slot="footer" class="broker-prompt-footer">
            <p class="broker-prompt-hint">“This session” is kept until you reload this tab.</p>
            <div class="broker-prompt-actions">
                <wa-button @click="emit('decision', 'deny')">Deny</wa-button>
                <wa-button v-if="prompt?.canRemember" @click="emit('decision', 'forever')">Forever</wa-button>
                <wa-button variant="brand" @click="emit('decision', 'session')">This session</wa-button>
            </div>
        </div>
    </wa-dialog>
</template>

<style scoped>
.broker-prompt-host {
    word-break: break-all;
    font-size: 1.05em;
}
.broker-prompt-target {
    color: var(--wa-color-neutral-on-quiet, #666);
}
.broker-prompt-footer {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
}
.broker-prompt-hint {
    margin: 0;
    font-size: var(--wa-font-size-s, 0.875rem);
    color: var(--wa-color-neutral-on-quiet, #666);
    text-align: right;
}
.broker-prompt-actions {
    display: flex;
    justify-content: flex-end;
    gap: 0.5rem;
}
</style>
