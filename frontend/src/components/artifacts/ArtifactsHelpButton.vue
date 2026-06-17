<script setup>
// ArtifactsHelpButton.vue — a "What are artifacts?" button that opens a short
// info dialog explaining the artifacts feature. Dropped into the empty states
// of the Artifacts view (the bookmark-list sidebar when nothing is bookmarked,
// and the main render pane's "Select an artifact" placeholder) so someone who
// lands there — e.g. via the home's Artifacts button — can learn what
// artifacts are, where they come from, and how this view relates to them.
//
// Self-contained: the button and its dialog travel together, so it can be
// dropped anywhere without wiring an open() call through a parent. Closing
// uses the dialog's standard affordances (footer button, header ✕, Escape).
import { ref } from 'vue'

const dialogRef = ref(null)

function open() {
    if (dialogRef.value) dialogRef.value.open = true
}
function close() {
    if (dialogRef.value) dialogRef.value.open = false
}
</script>

<template>
    <wa-button appearance="outlined" variant="brand" size="small" @click="open">
        <wa-icon slot="start" name="circle-question"></wa-icon>
        What are artifacts?
    </wa-button>

    <wa-dialog ref="dialogRef" label="What are artifacts?" class="artifacts-help-dialog">
        <div class="artifacts-help">
            <p>Artifacts are content an agent creates for you, on request. They're kept outside the project's own files, so your project stays untouched.</p>

            <p>
                The agent builds each one as a file and saves it in the session's
                artifacts folder — images, web pages (interactive playgrounds,
                mini apps, etc), PDFs, Markdown, diagrams, audio, video. Ask the agent up front to
                make it an artifact and it's more likely to end up as one — being
                explicit also helps it grasp what you're after.
            </p>

            <p>
                You'll find them in that session's <strong>Artifacts</strong> tab,
                where TwiCC renders them for you. That tab only appears once the
                session has at least one artifact.
            </p>

            <p>
                From there you can bookmark
                <wa-icon name="bookmark" class="artifacts-help__icon"></wa-icon>
                any artifact, giving it a name and a scope: the project the session
                belongs to, that project's workspace, or everywhere.
            </p>

            <p>
                The current view gathers the artifacts you've bookmarked, each shown
                according to the scope you chose for it. When bookmarking, give them
                clear, meaningful names so they're easy to find here.
            </p>
        </div>

        <wa-button slot="footer" variant="brand" @click="close">Close</wa-button>
    </wa-dialog>
</template>

<style scoped>
.artifacts-help-dialog {
    --width: min(520px, calc(100vw - 2rem));
}

.artifacts-help {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
    line-height: 1.5;
}

.artifacts-help p {
    margin: 0;
}

/* Inline bookmark glyph, matching the toolbar toggle's "bookmarked" look so
   the reader recognizes the control. */
.artifacts-help__icon {
    vertical-align: -0.125em;
    color: var(--wa-color-brand-60);
}
</style>
