<script setup>
defineProps({
  color: { type: String, default: '#111114' },
});
</script>

<template>
  <div class="avatar-body" :style="{ backgroundColor: color }">
    <slot />
  </div>
</template>

<style scoped>
.avatar-body {
  position: relative;
  width: 100%;
  aspect-ratio: 1;
  border-radius: 50%;
}
</style>
