<!-- CANARY FIXTURE: idiomatic Vue component (analyzer negative control). -->
<template>
  <span class="tag-badge">{{ label }}</span>
</template>

<script setup>
import { computed } from "vue";

const props = defineProps({
  name: { type: String, required: true },
  count: { type: Number, default: 0 },
});

const label = computed(() => `${props.name} (${props.count})`);
</script>
