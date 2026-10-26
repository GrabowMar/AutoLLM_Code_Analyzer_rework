<!-- CANARY FIXTURE: deliberately vulnerable Vue component (analyzer self-test). -->
<template>
  <article class="card">
    <h2>{{ title }}</h2>
    <div v-html="body"></div>
  </article>
</template>

<script setup>
defineProps({
  title: { type: String, required: true },
  body: { type: String, default: "" },
});
</script>
