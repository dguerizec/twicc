<script setup>
/**
 * ProjectTreeNode - Recursive component that renders a single node of the project tree.
 *
 * Two rendering modes:
 * - Folder node (no project): chevron icon + segment label, click toggles open/closed.
 * - Project node (has project): ProjectCard component. If it also has children,
 *   a chevron in the title row (via title-prefix slot) toggles children visibility.
 *
 * Self-recursive: renders <ProjectTreeNode> for each child. Indentation is handled
 * via DOM nesting with padding-left on .tree-children.
 */
import { ref, computed } from 'vue'
import ProjectCard from './ProjectCard.vue'

const props = defineProps({
    node: {
        type: Object,
        required: true,
    },
})

const emit = defineEmits(['select', 'menu-select'])

// All nodes start open by default
const isOpen = ref(true)

const hasChildren = computed(() => props.node.children && props.node.children.length > 0)
const isFolder = computed(() => props.node.project === null)
const project = computed(() => props.node.project)

function toggleOpen() {
    isOpen.value = !isOpen.value
}

function handleSelect(proj) {
    emit('select', proj)
}

function handleMenuSelect(event, proj) {
    emit('menu-select', event, proj)
}

// Re-emit child events
function onChildSelect(proj) {
    emit('select', proj)
}

function onChildMenuSelect(event, proj) {
    emit('menu-select', event, proj)
}
</script>

<template>
    <div class="tree-node">
        <!-- Folder node (no project) -->
        <div v-if="isFolder" class="folder-header" @click="toggleOpen">
            <wa-icon
                :name="isOpen ? 'chevron-down' : 'chevron-right'"
                class="chevron-icon"
            ></wa-icon>
            <span class="folder-label" :title="node.path">{{ node.segment }}</span>
        </div>

        <!-- Project node (has project) -->
        <ProjectCard
            v-else
            :project="project"
            @select="handleSelect"
            @menu-select="handleMenuSelect"
        >
            <template v-if="hasChildren" #title-prefix>
                <wa-icon
                    :name="isOpen ? 'chevron-down' : 'chevron-right'"
                    class="chevron-icon tree-chevron"
                    @click.stop="toggleOpen"
                ></wa-icon>
            </template>
        </ProjectCard>

        <!-- Children (rendered recursively) -->
        <div v-if="hasChildren && isOpen" class="tree-children">
            <ProjectTreeNode
                v-for="child in node.children"
                :key="child.project ? child.project.id : `folder-${child.segment}`"
                :node="child"
                @select="onChildSelect"
                @menu-select="onChildMenuSelect"
            />
        </div>
    </div>
</template>

<style scoped>
.tree-node {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
}

.folder-header {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    padding: var(--wa-space-xs) 0;
    cursor: pointer;
    user-select: none;
    color: var(--wa-color-text-quiet);
}

.folder-header:hover {
    color: var(--wa-color-text-default);
}

.folder-label {
    font-size: var(--wa-font-size-s);
    font-family: var(--wa-font-family-code);
    font-weight: 500;
}

.chevron-icon {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    flex-shrink: 0;
}

.tree-chevron {
    cursor: pointer;
}

.tree-children {
    padding-left: var(--wa-space-m);
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
}
</style>
