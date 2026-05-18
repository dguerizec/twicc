<script setup>
import { getDetail } from '../../../../utils/todoList'

// Provider-agnostic todo/plan renderer. Each ``todos`` entry must carry
// ``status`` plus at least one of ``content`` / ``activeForm`` (see
// ``utils/todoList.js``). Codex's ``update_plan`` carries a single
// ``step`` field per item, mapped to ``content`` upstream.
//
// ``explanation`` is an optional preamble shown above the list. Only
// Codex's ``update_plan`` populates it today (no equivalent in Claude
// Code's TodoWrite); kept optional so other providers can ignore it.
defineProps({
    todos: {
        type: Array,
        required: true,
    },
    explanation: {
        type: String,
        default: null,
    },
})
</script>

<template>
    <p v-if="explanation" class="todo-explanation">{{ explanation }}</p>
    <ol class="todo-list">
        <li
            v-for="(todo, i) in todos"
            :key="i"
            class="todo-item"
            :class="`todo-item-${todo.status}`"
        >
            <wa-icon
                v-if="todo.status === 'completed'"
                name="check"
                class="todo-item-icon todo-item-icon-completed"
            ></wa-icon>
            <wa-icon
                v-else-if="todo.status === 'in_progress'"
                name="arrow-right"
                class="todo-item-icon todo-item-icon-in-progress"
            ></wa-icon>
            <wa-icon
                v-else-if="todo.status === 'deleted'"
                name="xmark"
                class="todo-item-icon todo-item-icon-deleted"
            ></wa-icon>
            <wa-icon
                v-else
                name="circle"
                class="todo-item-icon todo-item-icon-pending"
                variant="regular"
            ></wa-icon>
            <span class="todo-item-text">{{ getDetail(todo) }}</span>
        </li>
    </ol>
</template>

<style scoped>
.todo-explanation {
    margin: 0 0 var(--wa-space-xs) 0;
    color: var(--wa-color-text-quiet);
    font-style: italic;
}

.todo-list {
    list-style: none;
    margin: 0;
    padding: var(--wa-space-xs) 0;
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
}

.todo-item {
    display: flex;
    align-items: baseline;
    gap: var(--wa-space-xs);
}

.todo-item-icon {
    flex-shrink: 0;
    font-size: 0.85em;
}

.todo-item-icon-completed {
    color: var(--wa-color-success-60);
}

.todo-item-icon-in-progress {
    color: var(--wa-color-brand-text);
}

.todo-item-icon-pending {
    color: var(--wa-color-text-quiet);
}

.todo-item-icon-deleted {
    color: var(--wa-color-danger-50);
}

.todo-item-completed .todo-item-text {
    color: var(--wa-color-text-quiet);
}

.todo-item-deleted .todo-item-text {
    color: var(--wa-color-text-quiet);
    text-decoration: line-through;
}

.todo-item-in-progress .todo-item-text {
    font-weight: var(--wa-font-weight-semibold);
}
</style>
