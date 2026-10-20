<script setup>
import AvatarBody from './AvatarBody.vue';
import AvatarEyes from './AvatarEyes.vue';
defineProps({
  color: { type: String, default: '#111114' },
  state: { type: String, default: '' },
  motionEnabled: { type: Boolean, default: true },
  gazeAngle: { type: Number, default: -45 },
  gazeStrength: { type: Number, default: 1 },
});

</script>

<template>
  <div class="slime-avatar" role="img" aria-label="둥근 몸에 흰 눈 두 개가 있는 캐릭터" :data-state="state">
    <AvatarBody :color="color || '#111114'">
      <AvatarEyes :motion-enabled="motionEnabled" :gaze-angle="gazeAngle" :gaze-strength="gazeStrength" />
    </AvatarBody>
  </div>
</template>

<style scoped>
.slime-avatar {
  width: 176px;
  flex: 0 0 auto;
}
</style>
