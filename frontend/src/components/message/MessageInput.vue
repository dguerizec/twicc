<script setup>
// MessageInput.vue - Text input + send/apply controls for a session.
// Per-session agent settings (model, effort, …) live in the
// ``useSessionAgentSettings`` composable; the trigger summary and the
// popover that exposes them are rendered by AgentSettingsSummary /
// AgentSettingsPopover. This component owns the textarea, attachments,
// pickers, and the send pipeline.
import { ref, computed, watch, nextTick, useId, toRef } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useDataStore } from '../../stores/data'
import { useSettingsStore } from '../../stores/settings'
import { getProviderHelpers } from '../../providers'
import { sendWsMessage, notifyUserDraftUpdated } from '../../composables/useWebSocket'
import { useSessionAgentSettings } from '../../composables/useSessionAgentSettings'
import { vPopoverFocusFix } from '../../directives/vPopoverFocusFix'
import { isSupportedMimeType, MAX_FILE_SIZE, SUPPORTED_IMAGE_TYPES, draftMediaToMediaItem } from '../../utils/fileUtils'
import { toast } from '../../composables/useToast'
import { useCodeCommentsStore, formatAllComments } from '../../stores/codeComments'
import { getParsedContent } from '../../utils/parsedContent'
import MediaThumbnailGroup from '../media/MediaThumbnailGroup.vue'
import AppTooltip from '../ui/AppTooltip.vue'
import FilePickerPopup from '../files/FilePickerPopup.vue'
import SlashCommandPickerPopup from './SlashCommandPickerPopup.vue'
import MessageHistoryPickerPopup from './MessageHistoryPickerPopup.vue'
import MessageSnippetsBar from './MessageSnippetsBar.vue'
import MessageSnippetsDialog from './MessageSnippetsDialog.vue'
import AgentSettingsSummary from './AgentSettingsSummary.vue'
import AgentSettingsPopover from './AgentSettingsPopover.vue'
import { useMessageSnippetsStore } from '../../stores/messageSnippets'
import { useWorkspacesStore } from '../../stores/workspaces'
import { getUnavailablePlaceholders, resolveSnippetText } from '../../utils/snippetPlaceholders'

const props = defineProps({
    sessionId: {
        type: String,
        required: true
    },
    projectId: {
        type: String,
        required: true
    }
})

const router = useRouter()
const route = useRoute()
const store = useDataStore()
const settingsStore = useSettingsStore()
const codeCommentsStore = useCodeCommentsStore()

const settings = useSessionAgentSettings(toRef(props, 'sessionId'))
const {
    selectedModel,
    selectedPermissionMode,
    selectedEffort,
    selectedThinking,
    selectedClaudeInChrome,
    selectedContextMax,
    activeModel,
    activePermissionMode,
    activeEffort,
    activeThinking,
    activeClaudeInChrome,
    activeContextMax,
    isStarting,
    processState,
    isContextMaxForced,
    hasDropdownsChanged,
    hasSettingsChanged,
} = settings

// Detect "All Projects" mode from route name
const isAllProjectsMode = computed(() => route.name?.startsWith('projects-'))

const emit = defineEmits(['needs-title'])

// Get session data to check if it's a draft
const session = computed(() => store.getSession(props.sessionId))
const isDraft = computed(() => session.value?.draft === true)

// Local state for the textarea
const messageText = ref('')
const textareaRef = ref(null)
const fileInputRef = ref(null)
const attachButtonId = useId()
const settingsButtonId = useId()
const textareaAnchorId = useId()

// Message snippets dialog
const messageSnippetsDialogRef = ref(null)

// File picker popup state (@ mention)
const filePickerRef = ref(null)
const atCursorPosition = ref(null)  // cursor position right after the '@' character (typed-trigger mode)
const fileMirroredLength = ref(0)   // length of filter text mirrored into textarea after '@' (typed-trigger mode)
const atButtonMode = ref(false)     // true when opened via snippets bar button (no trigger char inserted)
const atInsertPosition = ref(null)  // cursor position where the file path will be inserted (button mode)
let atLastCloseTime = 0             // timestamp of last close (to prevent reopen on same click)

// Slash command picker popup state (/ at start)
const slashPickerRef = ref(null)
const slashCursorPosition = ref(null)  // cursor position right after the '/' character (typed-trigger mode)
const slashMirroredLength = ref(0)     // length of filter text mirrored into textarea after '/' (typed-trigger mode)
const slashButtonMode = ref(false)     // true when opened via snippets bar button (no trigger char inserted)
let slashLastCloseTime = 0             // timestamp of last close (to prevent reopen on same click)

// Message history picker popup state (! at start, or PageUp on first line)
const historyPickerRef = ref(null)
const histCursorPosition = ref(null)   // cursor position right after the '!' character (bang mode only)
const histMirroredLength = ref(0)      // length of filter text mirrored into textarea after '!' (bang mode only)
const histTriggerMode = ref(null)      // 'bang' (! trigger) or 'pageup' (PageUp on first line)
const histInsertPosition = ref(null)   // cursor position for insertion (pageup mode only)
let histLastCloseTime = 0              // timestamp of last close (to prevent reopen on same click)

// Extract the text from the optimistic user message (if any) to pass to the history picker
const optimisticMessageText = computed(() => {
    const optimistic = store.localState.optimisticMessages[props.sessionId]
    if (!optimistic) return null
    const parsed = getParsedContent(optimistic)
    if (!parsed?.message?.content) return null
    const content = parsed.message.content
    // Content is either a string or an array of content blocks
    if (typeof content === 'string') return content.trim() || null
    if (Array.isArray(content)) {
        const textBlock = content.findLast(block => block.type === 'text')
        return textBlock?.text?.trim() || null
    }
    return null
})

// Attachments for this session
const attachments = computed(() => store.getAttachments(props.sessionId))
const attachmentCount = computed(() => store.getAttachmentCount(props.sessionId))

// Temporary tooltip shown when new files are attached
const attachTooltipText = ref('')
const showAttachTooltip = ref(false)
let attachTooltipTimer = null

watch(attachmentCount, (newCount, oldCount) => {
    if (newCount > oldCount) {
        const added = newCount - oldCount
        clearTimeout(attachTooltipTimer)
        attachTooltipText.value = `${added} file${added > 1 ? 's' : ''} attached`
        showAttachTooltip.value = true
        attachTooltipTimer = setTimeout(() => {
            showAttachTooltip.value = false
        }, 2000)
    }
})

// Convert DraftMedia objects to normalized MediaItem format for the thumbnail group
const mediaItems = computed(() => attachments.value.map(a => draftMediaToMediaItem(a)))

// Whether files are currently being processed (encoded/resized) for this session
const isProcessingFiles = computed(() => store.isProcessingAttachments(props.sessionId))

// Determine if input/button should be disabled
const isDisabled = computed(() => {
    if (!store.wsConnected) return true
    const providerHelpers = getProviderHelpers(session.value?.provider)
    if (providerHelpers && !providerHelpers.canSendMessage()) return true
    if (store.isInitialSyncInProgress) return true
    if (isProcessingFiles.value) return true
    return isStarting.value
})

// Button label based on process state and settings changes
// On drafts, the button is always "Send" since there's no process to apply settings to.
const buttonLabel = computed(() => {
    const state = processState.value?.state
    if (state === 'starting') return 'Starting...'
    if (!isDraft.value && hasSettingsChanged.value && !messageText.value.trim()) return 'Apply settings'
    return 'Send'
})

// Button icon changes based on mode
const buttonIcon = computed(() => {
    if (!isDraft.value && hasSettingsChanged.value && !messageText.value.trim()) return 'arrows-rotate'
    return 'paper-plane'
})

// Placeholder text based on process state
const placeholderText = computed(() => {
    const state = processState.value?.state
    if (state === 'starting') {
        return 'Starting Claude process...'
    }
    if (state === 'assistant_turn') {
        return 'You can send a message now. Claude will receive it as soon as possible (while working or after). Note: it will not appear in the conversation history.'
    }
    const historyHint = isDraft.value
        ? ''
        : settingsStore.isTouchDevice
            ? ', ! = message history'
            : ', ! and PageUp = message history'
    let text = `Shortcuts: At start: / = commands${historyHint}; Anywhere: @ = file paths`
    if (!settingsStore.isTouchDevice) {
        const keys = settingsStore.isMac ? '⌘↵ or Ctrl↵' : 'Ctrl↵ or Meta↵'
        text += `, ${keys} to send`
    }
    return text
})

// Restore draft message when session changes
watch(() => props.sessionId, async (newId) => {
    const draft = store.getDraftMessage(newId)
    messageText.value = draft?.message || ''
    // Adjust textarea height after the DOM updates with restored content
    await nextTick()
    if (textareaRef.value?.updateComplete) {
        await textareaRef.value.updateComplete
    }
    adjustTextareaHeight()
}, { immediate: true })

// Also restore draft when it arrives after hydration (initial page load)
// This handles the race condition where the component mounts before IndexedDB is loaded
watch(
    () => store.getDraftMessage(props.sessionId),
    async (draft) => {
        // Only restore if textarea is still empty (don't overwrite user typing)
        if (!messageText.value && draft?.message) {
            messageText.value = draft.message
            // Adjust textarea height after the DOM updates with restored content
            await nextTick()
            if (textareaRef.value?.updateComplete) {
                await textareaRef.value.updateComplete
            }
            adjustTextareaHeight()
        }
    }
)

// Save draft message on each keystroke (debounced in store)
watch(messageText, (newText) => {
    store.setDraftMessage(props.sessionId, newText)
})

// Autofocus textarea for draft sessions (only once)
const hasAutoFocused = ref(false)

// Watch both isDraft and textareaRef - focus when both are ready
watch([isDraft, textareaRef], async ([isDraftSession, textarea]) => {
    if (isDraftSession && !hasAutoFocused.value && textarea) {
        hasAutoFocused.value = true
        // Wait for Vue's next tick
        await nextTick()
        // Wait for the Web Component to be fully rendered (Lit's updateComplete)
        if (textarea.updateComplete) {
            await textarea.updateComplete
        }
        // Wait until the textarea is visible (offsetParent !== null).
        // When creating a new session from an empty state (no session was selected),
        // the parent components (SessionView, SessionItemsList) are mounted for the first time,
        // and the textarea may not be visible yet. An element with offsetParent === null
        // cannot receive focus.
        const maxAttempts = 20
        for (let i = 0; i < maxAttempts; i++) {
            if (textarea.offsetParent !== null) {
                break
            }
            await new Promise(resolve => requestAnimationFrame(resolve))
        }
        adjustTextareaHeight()
        textarea.focus()
    }
}, { immediate: true })

/**
 * Adjust the textarea height to fit its content.
 * Accesses the internal <textarea> inside the wa-textarea shadow DOM
 * to perform a single synchronous height reset + scrollHeight read.
 * Unlike wa-textarea's built-in resize="auto", this avoids the
 * ResizeObserver feedback loop that causes 1px jitter.
 *
 * IMPORTANT: The "height = auto" reset temporarily collapses the textarea,
 * which causes the parent flex layout to reflow. During that reflow, the
 * browser synchronously clamps the VirtualScroller's scrollTop (because the
 * scroller grows when the textarea shrinks). When the textarea is restored
 * to its previous height, the clamped scrollTop is now wrong — the scroller
 * appears to jump up. To avoid this layout thrash, we skip remeasurement
 * when content and width haven't changed since the last call.
 */
let _lastMeasuredContent = null
let _lastMeasuredWidth = null

function adjustTextareaHeight() {
    const textarea = textareaRef.value?.shadowRoot?.querySelector('textarea')
    if (!textarea) return

    const currentContent = textarea.value
    const currentWidth = textarea.clientWidth

    // Skip remeasurement if content and width haven't changed and height
    // is already explicitly set. This avoids the costly height='auto' reset
    // that causes layout thrash and scroll position loss on focus events.
    if (currentContent === _lastMeasuredContent
        && currentWidth === _lastMeasuredWidth
        && textarea.style.height
        && textarea.style.height !== 'auto') {
        return
    }
    const previousContent = _lastMeasuredContent
    _lastMeasuredContent = currentContent
    _lastMeasuredWidth = currentWidth

    // Fast path for growth: if scrollHeight already exceeds clientHeight
    // (with the current explicit height still set), content has grown beyond
    // the current height. Set the new height directly — no need to reset to
    // 'auto', which would temporarily collapse the textarea and cause the
    // browser to clamp the VirtualScroller's scrollTop during the forced reflow.
    if (textarea.scrollHeight > textarea.clientHeight) {
        textarea.style.height = `${textarea.scrollHeight}px`
        return
    }

    // If content didn't shrink (same length or longer), the height can only
    // stay the same or grow — and growth was already handled by the fast path
    // above. Skip the slow path to avoid layout thrash: the height='auto' reset
    // temporarily collapses the textarea, causing the browser to clamp the
    // VirtualScroller's scrollTop during the forced reflow.
    if (previousContent !== null && currentContent.length >= previousContent.length) {
        return
    }

    // Slow path for potential shrinkage (content deleted): reset to 'auto' to
    // measure the natural scrollHeight. The reset temporarily collapses the
    // textarea, causing the VirtualScroller to grow and the browser to clamp
    // its scrollTop. Save/restore scrollTop around the measurement to prevent
    // visible scroll jumps.
    const scrollerEl = textareaRef.value?.closest('.session-items-list')?.querySelector('.virtual-scroller')
    const savedScrollTop = scrollerEl?.scrollTop

    textarea.style.height = 'auto'
    if (textarea.scrollHeight > textarea.clientHeight) {
        textarea.style.height = `${textarea.scrollHeight}px`
    }

    // Restore scrollTop — the browser will clamp it to the new valid range,
    // which is correct: if the textarea shrunk, the scroller grew, so there's
    // more content visible at the bottom and less room to scroll.
    if (scrollerEl != null && savedScrollTop != null) {
        scrollerEl.scrollTop = savedScrollTop
    }
}

/**
 * Handle textarea input event.
 * Detects '@' insertion to trigger the file picker popup.
 * Detects '/' at position 0 to trigger the slash command picker popup.
 * Also notifies the server that the user is actively drafting (debounced).
 */
function onInput(event) {
    const newText = event.target.value
    const oldText = messageText.value

    // Detect single character insertion
    if (newText.length === oldText.length + 1) {
        const inner = textareaRef.value?.shadowRoot?.querySelector('textarea')
        const cursorPos = inner?.selectionStart

        // Detect '@' to trigger file picker (only at start of text or after whitespace)
        if (!filePickerRef.value?.isOpen && cursorPos > 0 && newText[cursorPos - 1] === '@'
            && (cursorPos === 1 || /\s/.test(newText[cursorPos - 2]))) {
            atCursorPosition.value = cursorPos  // right after the '@'
            fileMirroredLength.value = 0
            nextTick(() => filePickerRef.value?.open())
        }

        // Detect '/' at position 0 (first character of the message) to trigger slash command picker
        if (!slashPickerRef.value?.isOpen && cursorPos === 1 && newText[0] === '/') {
            slashCursorPosition.value = cursorPos  // right after the '/'
            slashMirroredLength.value = 0
            nextTick(() => slashPickerRef.value?.open())
        }

        // Detect '!' at position 0 (first character of the message) to trigger message history picker
        // Skip on draft sessions — no message history to show
        if (!isDraft.value && !historyPickerRef.value?.isOpen && cursorPos === 1 && newText[0] === '!') {
            histTriggerMode.value = 'bang'
            histCursorPosition.value = cursorPos  // right after the '!'
            histMirroredLength.value = 0
            histInsertPosition.value = null
            nextTick(() => historyPickerRef.value?.open())
        }
    }

    messageText.value = newText
    adjustTextareaHeight()
    // Notify server that user is actively preparing a message (debounced)
    // This prevents auto-stop of the process due to inactivity timeout
    notifyUserDraftUpdated(props.sessionId)
}

/**
 * Update textarea content programmatically (without triggering input events).
 * Sets the value on the Vue reactive ref, the wa-textarea web component,
 * and the inner shadow DOM textarea.
 */
function updateTextareaContent(newText) {
    messageText.value = newText
    if (textareaRef.value) {
        textareaRef.value.value = newText
        const inner = textareaRef.value.shadowRoot?.querySelector('textarea')
        if (inner) {
            inner.value = newText
        }
    }
    adjustTextareaHeight()
}

/**
 * Mirror popup filter text into the textarea at the given cursor position.
 * Replaces the previously mirrored text (tracked by mirroredLengthRef) with
 * the new filter text, keeping surrounding content intact.
 */
function mirrorFilterToTextarea(pos, mirroredLengthRef, filterText) {
    if (pos == null) return

    const currentText = messageText.value
    const before = currentText.slice(0, pos)
    const after = currentText.slice(pos + mirroredLengthRef.value)
    const newText = before + filterText + after

    mirroredLengthRef.value = filterText.length
    updateTextareaContent(newText)
}

/**
 * Handle filter text changes from the file picker popup.
 * Mirrors the typed filter text into the textarea right after the '@'.
 * In button mode, no mirroring — the filter stays inside the popup.
 */
function onFilePickerFilterChange(filterText) {
    if (atButtonMode.value) return
    mirrorFilterToTextarea(atCursorPosition.value, fileMirroredLength, filterText)
}

/**
 * Handle filter text changes from the slash command picker popup.
 * Mirrors the typed filter text into the textarea right after the '/'.
 * In button mode, no mirroring — the filter stays inside the popup.
 */
function onSlashPickerFilterChange(filterText) {
    if (slashButtonMode.value) return
    mirrorFilterToTextarea(slashCursorPosition.value, slashMirroredLength, filterText)
}

/**
 * Handle file selection from the file picker popup.
 * Typed-trigger mode: replaces '@' + mirrored filter with '@path '.
 * Button mode: inserts '[space?]@path ' at the memorized cursor position.
 */
async function onFilePickerSelect(relativePath) {
    if (atButtonMode.value) {
        const pos = atInsertPosition.value
        if (pos != null && pos <= messageText.value.length) {
            const before = messageText.value.slice(0, pos)
            const after = messageText.value.slice(pos)
            // Prepend a space if the preceding char is non-whitespace (so '@' parses correctly)
            const prevChar = pos > 0 ? messageText.value[pos - 1] : ''
            const needsLeadingSpace = pos > 0 && !/\s/.test(prevChar)
            const leading = needsLeadingSpace ? ' ' : ''
            const trailing = after.startsWith(' ') ? '' : ' '
            const insertion = leading + '@' + relativePath + trailing
            const newText = before + insertion + after
            messageText.value = newText

            if (textareaRef.value) {
                textareaRef.value.value = newText
                const inner = textareaRef.value.shadowRoot?.querySelector('textarea')
                if (inner) {
                    inner.value = newText
                    const newPos = pos + insertion.length
                    inner.setSelectionRange(newPos, newPos)
                }
            }
        }
    } else {
        const pos = atCursorPosition.value
        if (pos != null && pos <= messageText.value.length) {
            const before = messageText.value.slice(0, pos)
            // Skip the mirrored filter text that was transparently inserted
            const after = messageText.value.slice(pos + fileMirroredLength.value)
            // Add a trailing space unless the text after already starts with one
            const space = after.startsWith(' ') ? '' : ' '
            const newText = before + relativePath + space + after
            messageText.value = newText

            // Force update the web component and inner textarea
            if (textareaRef.value) {
                textareaRef.value.value = newText
                const inner = textareaRef.value.shadowRoot?.querySelector('textarea')
                if (inner) {
                    inner.value = newText
                    const newPos = pos + relativePath.length + space.length
                    inner.setSelectionRange(newPos, newPos)
                }
            }
        }
    }

    atCursorPosition.value = null
    fileMirroredLength.value = 0
    atButtonMode.value = false
    atInsertPosition.value = null
    await nextTick()
    textareaRef.value?.focus()
    adjustTextareaHeight()
}

/**
 * Handle file picker popup close (without selection).
 * Returns focus to the textarea and positions the cursor after the
 * trigger character + any filter text that was mirrored.
 */
function onFilePickerClose() {
    atLastCloseTime = Date.now()
    const isButtonMode = atButtonMode.value
    const pos = atCursorPosition.value
    const mirrorLen = fileMirroredLength.value
    const buttonPos = atInsertPosition.value
    atCursorPosition.value = null
    fileMirroredLength.value = 0
    atButtonMode.value = false
    atInsertPosition.value = null

    textareaRef.value?.focus()
    const inner = textareaRef.value?.shadowRoot?.querySelector('textarea')
    if (inner) {
        if (isButtonMode && buttonPos != null) {
            // Button mode: restore cursor to the memorized position, textarea untouched
            inner.setSelectionRange(buttonPos, buttonPos)
        } else if (pos != null) {
            const cursorTarget = pos + mirrorLen
            inner.setSelectionRange(cursorTarget, cursorTarget)
        }
    }
}

/**
 * Handle slash command selection from the slash command picker popup.
 * Replaces the entire textarea content with the selected command text.
 * (The button is only enabled when textarea is empty, so typed-trigger and
 * button-mode behave identically here: the final textarea is just the command.)
 */
async function onSlashCommandSelect(commandText) {
    slashCursorPosition.value = null
    slashMirroredLength.value = 0
    slashButtonMode.value = false
    messageText.value = commandText

    // Force update the web component and inner textarea
    if (textareaRef.value) {
        textareaRef.value.value = commandText
        const inner = textareaRef.value.shadowRoot?.querySelector('textarea')
        if (inner) {
            inner.value = commandText
            const newPos = commandText.length
            inner.setSelectionRange(newPos, newPos)
        }
    }

    await nextTick()
    textareaRef.value?.focus()
    adjustTextareaHeight()
}

/**
 * Handle slash command picker popup close (without selection).
 * Returns focus to the textarea and positions the cursor after the
 * trigger character + any filter text that was mirrored.
 */
function onSlashCommandPickerClose() {
    slashLastCloseTime = Date.now()
    const isButtonMode = slashButtonMode.value
    const pos = slashCursorPosition.value
    const mirrorLen = slashMirroredLength.value
    slashCursorPosition.value = null
    slashMirroredLength.value = 0
    slashButtonMode.value = false

    // Button mode: nothing to restore — textarea was never modified
    if (isButtonMode) {
        textareaRef.value?.focus()
        return
    }

    textareaRef.value?.focus()
    if (pos != null) {
        const inner = textareaRef.value?.shadowRoot?.querySelector('textarea')
        if (inner) {
            const cursorTarget = pos + mirrorLen
            inner.setSelectionRange(cursorTarget, cursorTarget)
        }
    }
}

/**
 * Handle filter text changes from the message history picker popup.
 * In bang mode, mirrors the typed filter text into the textarea right after the '!'.
 * In pageup mode, no mirroring is needed.
 */
function onHistoryPickerFilterChange(filterText) {
    if (histTriggerMode.value === 'bang') {
        mirrorFilterToTextarea(histCursorPosition.value, histMirroredLength, filterText)
    }
}

/**
 * Handle message selection from the message history picker popup.
 *
 * Bang mode ('!'): Replaces the '!' trigger character and any mirrored filter
 * text with the selected message text. Preserves surrounding textarea content.
 *
 * PageUp mode: Inserts the selected message text at the cursor position
 * where PageUp was pressed. No trigger character to remove.
 */
async function onHistoryMessageSelect(selectedText) {
    const mode = histTriggerMode.value
    const triggerPos = histCursorPosition.value
    const mirrorLen = histMirroredLength.value
    const insertPos = histInsertPosition.value

    // Reset all state
    histTriggerMode.value = null
    histCursorPosition.value = null
    histMirroredLength.value = 0
    histInsertPosition.value = null

    if (mode === 'bang' && triggerPos != null) {
        const currentContent = messageText.value
        // triggerPos is right after '!', so the '!' is at triggerPos-1
        const before = currentContent.slice(0, triggerPos - 1)
        const after = currentContent.slice(triggerPos + mirrorLen)
        const newText = before + selectedText + after
        const newCursorPos = before.length + selectedText.length

        updateTextareaContent(newText)
        await nextTick()

        const inner = textareaRef.value?.shadowRoot?.querySelector('textarea')
        if (inner) {
            inner.setSelectionRange(newCursorPos, newCursorPos)
        }
    } else if (mode === 'pageup' && insertPos != null) {
        const currentContent = messageText.value
        const before = currentContent.slice(0, insertPos)
        const after = currentContent.slice(insertPos)
        const newText = before + selectedText + after
        const newCursorPos = before.length + selectedText.length

        updateTextareaContent(newText)
        await nextTick()

        const inner = textareaRef.value?.shadowRoot?.querySelector('textarea')
        if (inner) {
            inner.setSelectionRange(newCursorPos, newCursorPos)
        }
    }

    await nextTick()
    textareaRef.value?.focus()
    adjustTextareaHeight()
}

/**
 * Handle message history picker popup close (without selection).
 * Returns focus to the textarea and restores the cursor position.
 *
 * Bang mode: positions cursor after '!' + any mirrored filter text.
 * PageUp mode: restores cursor to original position.
 */
function onHistoryPickerClose() {
    histLastCloseTime = Date.now()
    const mode = histTriggerMode.value
    const pos = histCursorPosition.value
    const mirrorLen = histMirroredLength.value
    const insertPos = histInsertPosition.value

    // Reset all state
    histTriggerMode.value = null
    histCursorPosition.value = null
    histMirroredLength.value = 0
    histInsertPosition.value = null

    textareaRef.value?.focus()
    const inner = textareaRef.value?.shadowRoot?.querySelector('textarea')
    if (inner) {
        if (mode === 'bang' && pos != null) {
            const cursorTarget = pos + mirrorLen
            inner.setSelectionRange(cursorTarget, cursorTarget)
        } else if (mode === 'pageup' && insertPos != null) {
            inner.setSelectionRange(insertPos, insertPos)
        }
    }
}

/**
 * Handle keyboard shortcuts in textarea.
 * Cmd/Ctrl+Enter submits the message.
 * PageUp on first line opens message history picker.
 */
function onKeydown(event) {
    if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
        event.preventDefault()
        handleSend()
        return
    }

    // PageUp on first line, or ArrowUp at position 0 → open message history picker
    // Skip on draft sessions — no message history to show
    if (!isDraft.value && (event.key === 'PageUp' || event.key === 'ArrowUp') && !historyPickerRef.value?.isOpen) {
        const inner = textareaRef.value?.shadowRoot?.querySelector('textarea')
        if (inner) {
            const cursorPos = inner.selectionStart
            // ArrowUp requires cursor at the very start (position 0)
            // PageUp requires cursor on the first line (no newline before cursor)
            const shouldOpen = event.key === 'ArrowUp'
                ? cursorPos === 0
                : !inner.value.slice(0, cursorPos).includes('\n')
            if (shouldOpen) {
                event.preventDefault()
                histTriggerMode.value = 'pageup'
                histInsertPosition.value = cursorPos
                histCursorPosition.value = null
                histMirroredLength.value = 0
                nextTick(() => historyPickerRef.value?.open())
            }
        }
    }
}

/**
 * Open the message history picker from the snippets bar button.
 * Uses pageup mode with cursor at position 0 (insert at start of textarea).
 */
function openHistoryFromButton() {
    // If the picker just closed (via click-outside from this same click), skip reopening
    if (Date.now() - histLastCloseTime < 300) return
    if (historyPickerRef.value?.isOpen) return
    const inner = textareaRef.value?.shadowRoot?.querySelector('textarea')
    const cursorPos = inner ? inner.selectionStart : 0
    histTriggerMode.value = 'pageup'
    histInsertPosition.value = cursorPos
    histCursorPosition.value = null
    histMirroredLength.value = 0
    nextTick(() => historyPickerRef.value?.open())
}

/**
 * Open the slash command picker from the snippets bar button.
 * Only available when the textarea is empty. Does NOT insert '/' in the
 * textarea — the trigger character is added only if the user actually
 * selects a command. If the popup is already open, clicking elsewhere
 * closes it via the popup's click-outside handler; the 300ms guard
 * prevents reopening on the same click.
 */
async function openSlashFromButton() {
    // If the picker just closed (via click-outside from this same click), skip reopening
    if (Date.now() - slashLastCloseTime < 300) return
    if (slashPickerRef.value?.isOpen) return
    // Guard: button should already be disabled when not empty, but double-check
    if (messageText.value.length > 0) return

    slashButtonMode.value = true
    slashCursorPosition.value = null
    slashMirroredLength.value = 0
    await nextTick()
    slashPickerRef.value?.open()
}

/**
 * Open the file picker from the snippets bar button.
 * Does NOT insert '@' in the textarea — the trigger character is added
 * only if the user actually selects a file path. The cursor position is
 * memorized so the selected path can be inserted at the right place.
 * If the popup is already open, clicking elsewhere closes it via the
 * popup's click-outside handler; the 300ms guard prevents reopening on
 * the same click.
 */
async function openAtFromButton() {
    // If the picker just closed (via click-outside from this same click), skip reopening
    if (Date.now() - atLastCloseTime < 300) return
    if (filePickerRef.value?.isOpen) return

    const inner = textareaRef.value?.shadowRoot?.querySelector('textarea')
    const cursorPos = inner ? inner.selectionStart : messageText.value.length

    atButtonMode.value = true
    atInsertPosition.value = cursorPos
    atCursorPosition.value = null
    fileMirroredLength.value = 0
    await nextTick()
    filePickerRef.value?.open()
}

/**
 * Handle paste event to capture images from clipboard.
 * Only processes image files from clipboard.
 */
async function onPaste(event) {
    const items = event.clipboardData?.items
    if (!items) return

    for (const item of items) {
        // Only handle image files from clipboard
        if (item.kind === 'file' && SUPPORTED_IMAGE_TYPES.includes(item.type)) {
            const file = item.getAsFile()
            if (file) {
                event.preventDefault()
                await processFile(file)
                return // Process only the first image
            }
        }
    }
}

/**
 * Process and add a file as an attachment.
 */
async function processFile(file) {
    // Validate MIME type
    if (!isSupportedMimeType(file.type)) {
        const extension = file.name.split('.').pop()?.toLowerCase() || 'unknown'
        toast.error(`Unsupported file type: .${extension}`, {
            title: 'Cannot attach file'
        })
        return
    }

    // Validate file size
    if (file.size > MAX_FILE_SIZE) {
        const sizeMB = (file.size / 1024 / 1024).toFixed(1)
        toast.error(`File too large: ${sizeMB} MB (max 5 MB)`, {
            title: 'Cannot attach file'
        })
        return
    }

    try {
        await store.addAttachment(props.sessionId, file)
        // Notify server that user is actively preparing a message
        notifyUserDraftUpdated(props.sessionId)
    } catch (error) {
        toast.error(error.message || 'Failed to process file', {
            title: 'Cannot attach file'
        })
    }
}

/**
 * Open the file picker dialog.
 */
function openFilePicker() {
    fileInputRef.value?.click()
}

/**
 * Handle file selection from the file picker.
 */
async function onFileSelected(event) {
    const files = event.target.files
    if (!files) return

    for (const file of files) {
        await processFile(file)
    }

    // Reset input so the same file can be selected again
    event.target.value = ''
}

/**
 * Remove an attachment by index (from MediaThumbnailGroup).
 * Translates the index back to the DraftMedia id for the store.
 */
function removeAttachmentByIndex(index) {
    const attachment = attachments.value[index]
    if (attachment) {
        store.removeAttachment(props.sessionId, attachment.id)
    }
}

/**
 * Remove all attachments.
 */
function removeAllAttachments() {
    store.clearAttachmentsForSession(props.sessionId)
}

/**
 * Send the message via WebSocket.
 * Backend handles both new and existing sessions with the same message type.
 * For draft sessions with a custom title, include the title in the message.
 * For draft sessions without a title, send the message AND open the rename dialog.
 *
 * Also handles settings-only updates: when text is empty but model/permission
 * mode has changed on an active process, sends a payload with empty text so
 * the backend applies the settings via SDK methods without sending a query.
 */
async function handleSend() {
    const text = messageText.value.trim()
    const isSettingsOnlyUpdate = !text && hasSettingsChanged.value

    // Need either text or settings change to proceed
    if ((!text && !isSettingsOnlyUpdate) || isDisabled.value) return

    // Build the message payload
    // For context_max: when the auto-force-to-1M rule is active we send 1M
    // explicitly instead of the user's null/200K choice — the UI shows
    // "Forced to 1M" so it would be inconsistent to start the process at 200K.
    const payload = {
        type: 'send_message',
        session_id: props.sessionId,
        project_id: props.projectId,
        provider: session.value?.provider,
        text: text,
        // Settings: null = use global default, explicit value = forced for this session
        permission_mode: selectedPermissionMode.value,
        selected_model: selectedModel.value,
        effort: selectedEffort.value,
        thinking_enabled: selectedThinking.value,
        claude_in_chrome: selectedClaudeInChrome.value,
        context_max: isContextMaxForced.value
            ? store.getEffectiveContextMax(props.sessionId, selectedModel.value ?? settings.providerStore.value?.defaultModel)
            : selectedContextMax.value,
    }

    // For draft sessions with a title, include it
    if (isDraft.value && session.value?.title) {
        payload.title = session.value.title
    }

    // For draft sessions without a title, open the rename dialog (non-blocking)
    // The message is still sent, allowing the agent to start working
    if (isDraft.value && !session.value?.title) {
        emit('needs-title')
    }

    // Include attachments in SDK format if any
    if (attachmentCount.value > 0) {
        const { images, documents } = store.getAttachmentsForSdk(props.sessionId)
        if (images.length > 0) {
            payload.images = images
        }
        if (documents.length > 0) {
            payload.documents = documents
        }
    }

    const success = sendWsMessage(payload)

    if (success) {
        // Sync active values to match what was just sent to the backend.
        // This makes the "Update..." button disappear immediately.
        activeModel.value = selectedModel.value
        activePermissionMode.value = selectedPermissionMode.value
        activeEffort.value = selectedEffort.value
        activeThinking.value = selectedThinking.value
        activeClaudeInChrome.value = selectedClaudeInChrome.value
        activeContextMax.value = selectedContextMax.value

        // For settings-only updates, nothing else to clean up
        if (isSettingsOnlyUpdate) return

        // Show optimistic user message immediately (only when not in assistant_turn,
        // because during assistant_turn the message is queued and the user_message
        // won't arrive until later)
        const state = processState.value?.state
        if (state !== 'assistant_turn') {
            const attachments = (payload.images || payload.documents)
                ? { images: payload.images, documents: payload.documents }
                : undefined
            store.setOptimisticMessage(props.sessionId, text, attachments)

            // Set optimistic STARTING state if no process is running yet.
            // The backend broadcasts STARTING before spawning the subprocess,
            // but the SDK connect() blocks the asyncio event loop, so the
            // WebSocket message only arrives after the subprocess is ready
            // (~2-4 seconds later, alongside ASSISTANT_TURN). This optimistic
            // state gives immediate visual feedback to the user.
            if (!state) {
                store.setProcessState(props.sessionId, props.projectId, 'starting')
            }
        }

        // Clear draft message from store (and IndexedDB)
        store.clearDraftMessage(props.sessionId)

        // Clear attachments from store and IndexedDB
        if (attachmentCount.value > 0) {
            await store.clearAttachmentsForSession(props.sessionId)
        }

        // Clear draft session from IndexedDB only (if this was a draft session)
        // Keep in store so session stays visible until backend confirms with session_updated
        if (isDraft.value) {
            store.deleteDraftSession(props.sessionId, { keepInStore: true })
        }

        // Clear the textarea on successful send.
        // Force-clear the Web Component's value property directly: Vue may skip
        // re-pushing "" via :value.prop if it already pushed "" on a previous send
        // (Vue's template binding deduplicates identical prop values).
        messageText.value = ''
        if (textareaRef.value) {
            // Force-clear both the Web Component property and its internal <textarea>.
            // Setting wa.value alone may be ignored by the Lit setter's dedup check
            // (if _value is already ""), and even when accepted, the Lit re-render
            // with live() can be skipped if Vue's binding already pushed the same value.
            // Directly clearing the inner textarea ensures the DOM is always updated.
            textareaRef.value.value = ''
            const inner = textareaRef.value.shadowRoot?.querySelector('textarea')
            if (inner) inner.value = ''
            await nextTick()
            adjustTextareaHeight()
        }
    }
}

/**
 * Cancel the draft session and navigate back to project list.
 * Navigates to 'projects-all' if in All Projects mode, otherwise to 'project'.
 */
function handleCancel() {
    // Clear draft message from store and IndexedDB
    store.clearDraftMessage(props.sessionId)
    store.deleteDraftSession(props.sessionId)

    if (isAllProjectsMode.value) {
        router.push({ name: 'projects-all', query: route.query.workspace ? { workspace: route.query.workspace } : {} })
    } else {
        router.push({ name: 'project', params: { projectId: props.projectId } })
    }
}

/**
 * Reset the form to its initial state: clear textarea text and
 * restore dropdowns to their active (server-side) values.
 */
async function handleReset() {
    // Clear text if any
    if (messageText.value) {
        messageText.value = ''
        store.clearDraftMessage(props.sessionId)
        if (textareaRef.value) {
            textareaRef.value.value = ''
            const inner = textareaRef.value.shadowRoot?.querySelector('textarea')
            if (inner) inner.value = ''
            await nextTick()
            adjustTextareaHeight()
        }
    }
    // Reset dropdowns to their reference values (active process or DB, including null)
    if (hasDropdownsChanged.value) {
        settings.restoreSettings()
    }
}

/**
 * Insert text at the current cursor position in the textarea.
 * If no cursor position is available, appends to the end.
 * Focuses the textarea and positions the cursor after the inserted text.
 */
function insertTextAtCursor(text) {
    const inner = textareaRef.value?.shadowRoot?.querySelector('textarea')
    const current = messageText.value
    const pos = inner?.selectionStart ?? current.length

    const before = current.slice(0, pos)
    const after = current.slice(inner?.selectionEnd ?? pos)
    const newText = before + text + after

    updateTextareaContent(newText)

    // Position cursor after the inserted text and focus
    const newPos = pos + text.length
    nextTick(() => {
        const innerEl = textareaRef.value?.shadowRoot?.querySelector('textarea')
        if (innerEl) {
            innerEl.setSelectionRange(newPos, newPos)
        }
        textareaRef.value?.focus()
    })
}

// ─── Code comments: "Add all comments to message" button ─────────────────────

const sessionCommentsWithContent = computed(() =>
    codeCommentsStore.getCommentsBySession(props.projectId, props.sessionId)
        .filter(c => c.content.trim())
)

const commentsWithContentCount = computed(() => sessionCommentsWithContent.value.length)

function clearAllSessionComments() {
    codeCommentsStore.removeAllSessionComments(props.projectId, props.sessionId)
}

function addAllCommentsToMessage() {
    const comments = sessionCommentsWithContent.value
    if (comments.length === 0) return
    insertTextAtCursor(formatAllComments(comments) + '\n')
    codeCommentsStore.removeAllSessionComments(props.projectId, props.sessionId)
}

// ── Message snippets ────────────────────────────────────────────────
const messageSnippetsStore = useMessageSnippetsStore()

/** Placeholder resolution context (same shape as terminal uses). */
const placeholderContext = computed(() => {
    const s = session.value
    const pid = props.projectId
    const project = pid ? store.getProject(pid) : null
    const projectName = pid ? store.getProjectDisplayName(pid) : null
    return { session: s, project, projectName }
})

/** Workspace IDs for snippet display: active workspace, or all workspaces containing this project. */
const snippetWorkspaceIds = computed(() => {
    const wsId = route.query.workspace
    if (wsId) return [wsId]
    if (!props.projectId) return []
    const workspacesStore = useWorkspacesStore()
    return workspacesStore.getWorkspacesForProject(props.projectId).map(ws => ws.id)
})

/** Snippets for this project, enriched with _disabled / _disabledReason for unresolvable placeholders. */
const snippetsForProject = computed(() => {
    const raw = props.projectId ? messageSnippetsStore.getSnippetsForProject(props.projectId, snippetWorkspaceIds.value) : []
    const ctx = placeholderContext.value

    return raw.map(snippet => {
        const placeholders = snippet.placeholders || []
        if (placeholders.length === 0) return snippet
        const unavailable = getUnavailablePlaceholders(placeholders, ctx)
        if (unavailable.length === 0) return snippet
        return {
            ...snippet,
            _disabled: true,
            _disabledReason: `Not available: ${unavailable.map(p => p.label).join(', ')}`,
        }
    })
})

function handleSnippetPress(snippet) {
    const placeholders = snippet.placeholders || []
    const resolved = resolveSnippetText(snippet.text, placeholders, placeholderContext.value)
    insertTextAtCursor(resolved)
}

function handleSnippetDisabledPress(snippet) {
    toast(snippet._disabledReason || 'Some placeholders are not available', { variant: 'warning' })
}

function openMessageSnippetsDialog() {
    messageSnippetsDialogRef.value?.open()
}

function getSessionSetting(key) {
    const ref_ = settings.SELECTED_REFS[key]
    return ref_ ? ref_.value : null
}

function setSessionSetting(key, value) {
    const ref_ = settings.SELECTED_REFS[key]
    if (ref_) ref_.value = value
}

function getSessionGateState() {
    return {
        isStarting: settings.isStarting.value,
        isContextMaxForced: settings.isContextMaxForced.value,
        isContextMaxForcedByModel: settings.isContextMaxForcedByModel.value,
        isEffortXhighAvailable: settings.isEffortXhighAvailable.value,
        isEffortMaxAvailable: settings.isEffortMaxAvailable.value,
        effectiveModel: settings.selectedModel.value ?? settings.providerStore.value?.defaultModel,
    }
}

defineExpose({ insertTextAtCursor, getSessionSetting, setSessionSetting, getSessionGateState })
</script>

<template>
    <div class="message-input">
        <div v-if="commentsWithContentCount > 0" class="code-comments-bar">
            <wa-button
                variant="brand"
                appearance="filled-outlined"
                size="small"
                @click="addAllCommentsToMessage"
            >
                {{ commentsWithContentCount === 1
                    ? 'Add comment to message'
                    : `Add all comments (${commentsWithContentCount}) to message`
                }}
            </wa-button>
            <wa-button
                variant="neutral"
                appearance="outlined"
                size="small"
                @click="clearAllSessionComments"
            >
                {{ commentsWithContentCount === 1 ? 'Clear comment' : 'Clear comments' }}
            </wa-button>
        </div>
        <wa-textarea
            ref="textareaRef"
            :id="textareaAnchorId"
            :value.prop="messageText"
            :placeholder="placeholderText"
            rows="3"
            resize="none"
            @input="onInput"
            @keydown="onKeydown"
            @paste="onPaste"
            @focus="adjustTextareaHeight"
        ></wa-textarea>

        <!-- Popups teleported out of the flex container -->
        <Teleport to="body">
            <!-- File picker popup triggered by @ -->
            <FilePickerPopup
                ref="filePickerRef"
                :session-id="sessionId"
                :project-id="projectId"
                :anchor-id="textareaAnchorId"
                @select="onFilePickerSelect"
                @close="onFilePickerClose"
                @filter-change="onFilePickerFilterChange"
            />

            <!-- Slash command picker popup triggered by / at start -->
            <SlashCommandPickerPopup
                ref="slashPickerRef"
                :project-id="projectId"
                :provider="session?.provider"
                :anchor-id="textareaAnchorId"
                @select="onSlashCommandSelect"
                @close="onSlashCommandPickerClose"
                @filter-change="onSlashPickerFilterChange"
            />

            <!-- Message history picker popup triggered by ! at start -->
            <MessageHistoryPickerPopup
                ref="historyPickerRef"
                :project-id="projectId"
                :session-id="sessionId"
                :anchor-id="textareaAnchorId"
                :synthetic-message-text="optimisticMessageText"
                @select="onHistoryMessageSelect"
                @close="onHistoryPickerClose"
                @filter-change="onHistoryPickerFilterChange"
            />
        </Teleport>

        <!-- Message snippets bar -->
        <MessageSnippetsBar
            :snippets="snippetsForProject"
            :show-history-button="!isDraft"
            :can-open-slash="messageText.length === 0"
            @snippet-press="handleSnippetPress"
            @snippet-disabled-press="handleSnippetDisabledPress"
            @manage-snippets="openMessageSnippetsDialog"
            @open-history="openHistoryFromButton"
            @open-slash="openSlashFromButton"
            @open-at="openAtFromButton"
        />

        <!-- Message snippets dialog (teleported out of the flex container) -->
        <Teleport to="body">
            <MessageSnippetsDialog
                ref="messageSnippetsDialogRef"
                :current-project-id="projectId"
            />
        </Teleport>

        <div class="message-input-toolbar">
            <!-- Attachments row: button on left, thumbnails on right -->
            <div class="message-input-attachments">
                <!-- Hidden file input -->
                <input
                    ref="fileInputRef"
                    type="file"
                    multiple
                    accept="image/png,image/jpeg,image/gif,image/webp,application/pdf,text/plain"
                    style="display: none;"
                    @change="onFileSelected"
                />

                <!-- Attach button -->
                <wa-button
                    variant="neutral"
                    appearance="plain"
                    size="small"
                    @click="openFilePicker"
                    :id="attachButtonId"
                >
                    <wa-icon name="paperclip"></wa-icon>
                </wa-button>
                <AppTooltip :for="attachButtonId">Attach files (images, PDF, text)</AppTooltip>

                <!-- Attachment badge + popover -->
                <template v-if="attachmentCount > 0">
                    <button
                        :id="`attachments-popover-trigger-${sessionId}`"
                        class="attachments-badge-trigger"
                    >
                        <wa-badge variant="primary" pill>{{ attachmentCount }}</wa-badge>
                    </button>
                    <AppTooltip :for="`attachments-popover-trigger-${sessionId}`">{{ attachmentCount }} file{{ attachmentCount > 1 ? 's' : '' }} attached</AppTooltip>
                    <!-- Temporary tooltip shown when new files are attached -->
                    <wa-tooltip
                        :for="`attachments-popover-trigger-${sessionId}`"
                        trigger="manual"
                        placement="top"
                        :open="showAttachTooltip || undefined"
                    >{{ attachTooltipText }}</wa-tooltip>
                    <wa-popover
                        v-popover-focus-fix
                        :for="`attachments-popover-trigger-${sessionId}`"
                        placement="top"
                        class="attachments-popover"
                    >
                        <MediaThumbnailGroup
                            :items="mediaItems"
                            removable
                            @remove="removeAttachmentByIndex"
                        />
                        <div class="popover-actions">
                            <wa-button
                                variant="danger"
                                appearance="outlined"
                                size="small"
                                @click="removeAllAttachments"
                            >
                                <wa-icon name="trash" slot="start"></wa-icon>
                                Remove all
                            </wa-button>
                        </div>
                    </wa-popover>
                </template>
            </div>

            <div class="message-input-actions">
                <!-- Settings trigger: button (with summary) + popover -->
                <wa-button
                    :id="settingsButtonId"
                    appearance="plain"
                    variant="neutral"
                    size="small"
                    class="settings-button"
                >
                    <wa-icon name="gear"></wa-icon>
                    <AgentSettingsSummary :session="session" :settings="settings" />
                </wa-button>
                <AgentSettingsPopover
                    :for="settingsButtonId"
                    :session="session"
                    :settings="settings"
                    :is-draft="isDraft"
                    :message-text="messageText"
                    :button-label="buttonLabel"
                />

                <!-- Cancel button for draft sessions -->
                <wa-button
                    v-if="isDraft"
                    variant="neutral"
                    appearance="outlined"
                    @click="handleCancel"
                    size="small"
                    class="cancel-button"
                >
                    <wa-icon name="xmark" variant="classic"></wa-icon>
                    <span>Cancel</span>
                </wa-button>
                <!-- Reset button for existing sessions: resets text and/or dropdowns -->
                <wa-button
                    v-else-if="messageText.trim() || hasDropdownsChanged"
                    variant="neutral"
                    appearance="outlined"
                    @click="handleReset"
                    size="small"
                    class="reset-button"
                >
                    <wa-icon name="xmark" variant="classic"></wa-icon>
                    <span>Reset</span>
                </wa-button>
                <!-- Send / Update button: dynamically labeled based on state -->
                <wa-button
                    variant="brand"
                    :disabled="isDisabled || (!messageText.trim() && !(hasSettingsChanged && !isDraft))"
                    @click="handleSend"
                    size="small"
                    class="send-button"
                >
                    <wa-icon :name="buttonIcon" variant="classic"></wa-icon>
                    <span>{{ buttonLabel }}</span>
                </wa-button>
            </div>
        </div>
    </div>
</template>

<style scoped>
.message-input {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
    padding: var(--wa-space-s);
    padding-top: 0;
    background: var(--main-header-footer-bg-color);
    container: message-input / inline-size;
}

.code-comments-bar {
    display: flex;
    flex-wrap: wrap;
    gap: var(--wa-space-xs);
    align-items: center;
}

.message-input wa-textarea::part(textarea) {
    /* Limit height to 40% of visual viewport (accounts for mobile keyboard) */
    max-height: 40dvh;
    /* Allow scrolling when content exceeds max-height */
    overflow-y: auto;
}

.message-input-toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--wa-space-s);
    @media (width < 640px) {
        padding-left: 2.75rem;
    }
}

/* When sidebar is closed, the sidebar toggle button overlaps
   the attach button area. Add left padding to make room. */
body.sidebar-closed .message-input-toolbar {
    @media (width >= 640px) {
        padding-left: 3.5rem;
    }
}

.message-input-attachments {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    min-width: 0;
    @media (width < 640px) {
        gap: var(--wa-space-xs);
    }
}

.settings-button {
    wa-icon {
        display: none;
    }
    min-width: 0;
    flex-shrink: 1;
    &::part(label) {
        white-space: wrap;
        font-weight: normal;
        font-size: var(--wa-font-size-s);
    }
}

.message-input-actions {
    display: flex;
    gap: var(--wa-space-s);
    flex-shrink: 1;
    min-width: 0;
    align-items: center;
    justify-content: flex-end;
    max-width: calc(100% - 6rem);

    .cancel-button, .reset-button, .send-button {
        flex-shrink: 0;
        wa-icon {
            display: none;
        }
        & > span {
            display: inline-block;
        }
    }
}

/* On narrow widths, show only icons for action buttons */
@container message-input (width < 35rem) {
    .message-input-actions {
        .settings-button {
            &::part(label) {
                line-height: 1.1;
            }
            &::part(base) {
                padding-inline: var(--wa-space-2xs);
            }
        }

        gap: var(--wa-space-2xs);

        .cancel-button, .reset-button, .send-button {
            &::part(base) {
                padding-inline: var(--wa-space-s);
            }

            wa-icon {
                display: inline-flex;
            }

            & > span {
                display: none;
            }
        }
    }
}
@container message-input (width < 24rem) {
    .message-input-actions {
        .settings-button {
            wa-icon {
                display: block;
            }
            & > span {
                display: none;
            }
            &::part(base) {
                padding-inline: var(--wa-space-s);
            }
        }
    }
}

.attachments-badge-trigger {
    background: none;
    border: none;
    padding: 0;
    cursor: pointer;
    display: flex;
    align-items: center;
    box-shadow: none;
    background: var(--wa-color-brand);
    height: 1.5rem;
    min-width: 1.5rem;
    margin-bottom: 0;
}

.attachments-popover {
    --max-width: min(400px, 90vw);
    --arrow-size: 16px;
}

.popover-actions {
    display: flex;
    justify-content: center;
    margin-top: var(--wa-space-l);
}

</style>
