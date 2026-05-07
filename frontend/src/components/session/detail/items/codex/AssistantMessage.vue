<script setup>
import TextContent from '../TextContent.vue'

defineProps({
    text: {
        type: String,
        required: true
    }
})
</script>

<template>
    <TextContent :text="text" role="assistant" />
</template>
