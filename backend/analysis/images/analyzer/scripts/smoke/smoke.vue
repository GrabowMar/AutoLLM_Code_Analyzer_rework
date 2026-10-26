<template>
  <div v-html="html"></div>
</template>

<script setup>
defineProps({ html: String });
</script>
