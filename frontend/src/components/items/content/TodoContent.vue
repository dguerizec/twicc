<script setup>
import { getDetail } from '../../../utils/todoList'

defineProps({
    todos: {
        type: Array,
        required: true
    }
})
</script>

<template>
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

.todo-item-completed .todo-item-text {
    color: var(--wa-color-text-quiet);
}

.todo-item-in-progress .todo-item-text {
    font-weight: var(--wa-font-weight-semibold);
}
</style>
