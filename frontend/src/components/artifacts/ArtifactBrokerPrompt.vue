<script setup>
// The network-broker consent prompt (design §9). Dumb + controlled: it shows
// whenever `prompt` is set and emits the user's choice. The parent owns the
// pending fetch's promise and settles it (idempotently) on `decision`.
import { computed } from 'vue'

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

// The bare hostname (no scheme/port) from the normalized key — to tell a *name*
// (toto.com) from an already-obviously-local target (localhost / an IP literal).
const hostname = computed(() => {
    if (!props.prompt?.host) return ''
    try {
        return new URL(props.prompt.host).hostname
    } catch {
        return ''
    }
})

function isDomainName(h) {
    if (!h) return false
    if (h === 'localhost' || h.endsWith('.localhost')) return false
    if (/^\d{1,3}(\.\d{1,3}){3}$/.test(h)) return false // IPv4 literal
    if (h.includes(':') || h.startsWith('[')) return false // IPv6 literal
    return true
}

// A "masquerade": a *name* that resolves to a non-public address. That is the
// only case worth an amber alarm — an obvious localhost/IP is just informational
// (the user already knows it's local; what they may not know is *whose* machine).
const isMasquerade = computed(
    () => !!props.prompt && props.prompt.kind !== 'public' && isDomainName(hostname.value),
)
const calloutVariant = computed(() => (isMasquerade.value ? 'warning' : 'neutral'))
const calloutIcon = computed(() => (isMasquerade.value ? 'triangle-exclamation' : 'circle-info'))

// Two facts, composed: WHERE it resolves (the machine TwiCC runs on for loopback,
// that machine's local network for lan) and WHO fetches (the server, not your
// browser — it matters when you reach TwiCC from another computer). The
// "<name> resolves to" subject appears only for a domain, surfacing the masquerade.
const calloutText = computed(() => {
    const p = props.prompt
    if (!p || p.kind === 'public') return ''
    const subject = isDomainName(hostname.value) ? `${hostname.value} resolves to` : 'This is'
    const target =
        p.kind === 'loopback'
            ? 'the machine TwiCC runs on (localhost)'
            : 'an address on the local network of the machine TwiCC runs on'
    return `${subject} ${target} — TwiCC's server makes the request from there, not your browser.`
})
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
            <wa-callout v-if="prompt.kind !== 'public'" :variant="calloutVariant">
                <wa-icon slot="icon" :name="calloutIcon"></wa-icon>
                {{ calloutText }}
            </wa-callout>
        </div>

        <div slot="footer" class="broker-prompt-footer">
            <p class="broker-prompt-question">Do you want to allow this connection?</p>
            <div class="broker-prompt-actions">
                <wa-button @click="emit('decision', 'deny')">No, deny</wa-button>
                <wa-button v-if="prompt?.canRemember" @click="emit('decision', 'forever')">Yes, forever</wa-button>
                <wa-button variant="brand" @click="emit('decision', 'session')">Yes, this session</wa-button>
            </div>
            <p class="broker-prompt-hint">“This session” keeps this host allowed until you reload or close the tab.</p>
            <p v-if="prompt?.canRemember" class="broker-prompt-hint">
                “Yes, forever” keeps this host allowed on all your devices (saved on the server).
            </p>
            <p v-else class="broker-prompt-hint">
                Bookmark this artifact to also get a “Yes, forever” option.
            </p>
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
.broker-prompt-question {
    margin: 0;
    font-weight: 600;
}
.broker-prompt-actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: 0.5rem;
}
.broker-prompt-hint {
    margin: 0;
    font-size: var(--wa-font-size-s, 0.875rem);
    color: var(--wa-color-neutral-on-quiet, #666);
}
</style>
