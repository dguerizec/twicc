<script setup>
import { computed } from 'vue'
import { SYNTHETIC_ITEM } from '../../../../../constants'
import { useDataStore } from '../../../../../stores/data'
import { emptyAssistantMessageMarkdown } from '../../../../../utils/emptyMessage'
import ContentList from './ContentList.vue'
import WorkingAssistantMessage from '../WorkingAssistantMessage.vue'

const props = defineProps({
    data: {
        type: Object,
        required: true
    },
    role: {
        type: String,
        required: true,
        validator: (value) => ['user', 'assistant', 'items'].includes(value)
    },
    // Context for store lookups (propagated to ContentList)
    projectId: {
        type: String,
        required: true
    },
    sessionId: {
        type: String,
        required: true
    },
    parentSessionId: {
        type: String,
        default: null
    },
    lineNum: {
        type: Number,
        required: true
    },
    externallyGrouped: {
        type: Boolean,
        default: false
    },
    // Group props for prefix/suffix
    groupHead: {
        type: Number,
        default: null
    },
    groupTail: {
        type: Number,
        default: null
    },
    prefixExpanded: {
        type: Boolean,
        default: false
    },
    suffixExpanded: {
        type: Boolean,
        default: false
    }
})

const emit = defineEmits(['toggle-suffix'])

const isStartingAssistantMessage = computed(() =>
    props.data?.syntheticKind === SYNTHETIC_ITEM.STARTING_ASSISTANT_MESSAGE.kind
)

const isWorkingAssistantMessage = computed(() =>
    props.data?.syntheticKind === SYNTHETIC_ITEM.WORKING_ASSISTANT_MESSAGE.kind
)

const contentItems = computed(() => {
    const content = props.data?.message?.content

    // If content is a string, treat it as a single text item
    if (typeof content === 'string') {
        return [{ type: 'text', text: content }]
    }

    // If content is an array, return it as-is
    if (Array.isArray(content)) {
        return content
    }

    return []
})

const dataStore = useDataStore()

const isStreamingBlock = computed(() =>
    props.data?.syntheticKind === SYNTHETIC_ITEM.STREAMING_BLOCK.kind
)

// An assistant message the provider wrote without any content renders as an
// empty bubble, which reads as a display bug — replace it with a text saying
// so. Blank ``text`` blocks count as no content; a ``thinking`` or
// ``tool_use`` block does not, so a message is only replaced when it has
// nothing else to show. Streaming placeholders are excluded: their text is
// legitimately empty until the first delta lands.
const displayItems = computed(() => {
    if (props.role !== 'assistant' || isStreamingBlock.value) return contentItems.value
    const isEmpty = contentItems.value.every(
        item => item?.type === 'text' && !(item.text || '').trim()
    )
    if (!isEmpty) return contentItems.value
    const provider = dataStore.getSession(props.sessionId)?.provider
    return [{ type: 'text', text: emptyAssistantMessageMarkdown(provider) }]
})
</script>

<template>
    <WorkingAssistantMessage v-if="isStartingAssistantMessage" label="starting" process-state="starting" />
    <WorkingAssistantMessage v-else-if="isWorkingAssistantMessage" :label="data.label || null" :tools="data.tools || []" :last-started-tool-id="data.lastStartedToolId || null" :last-tool-visible="data.lastToolVisible !== false" :session-id="sessionId" />
    <ContentList
        v-else
        :items="displayItems"
        :role="role"
        :project-id="projectId"
        :session-id="sessionId"
        :parent-session-id="parentSessionId"
        :line-num="lineNum"
        :timestamp="data?.timestamp || null"
        :externally-grouped="externallyGrouped"
        :group-head="groupHead"
        :group-tail="groupTail"
        :prefix-expanded="prefixExpanded"
        :suffix-expanded="suffixExpanded"
        @toggle-suffix="emit('toggle-suffix')"
    />
</template>
