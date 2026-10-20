<script setup>
import { computed } from 'vue';

const props = defineProps({
  motionEnabled: { type: Boolean, default: true },
  // 화면 좌표 기준: 오른쪽 0°, 아래 90°, 왼쪽 180°, 위 270°(-90°).
  gazeAngle: { type: Number, default: -45 },
  gazeStrength: { type: Number, default: 1 },
});

const gazePosition = computed(() => {
  const angle = (Number.isFinite(props.gazeAngle) ? props.gazeAngle : -45) * Math.PI / 180;
  const strength = Number.isFinite(props.gazeStrength) ? Math.min(1, Math.max(0, props.gazeStrength)) : 1;
  return {
    left: `${50 + Math.cos(angle) * 22 * strength}%`,
    top: `${50 + Math.sin(angle) * 22 * strength}%`,
  };
});
</script>

<template>
  <div class="avatar-eyes-position" :class="{ 'avatar-eyes-position--animated': motionEnabled }" :style="gazePosition" aria-hidden="true">
    <div class="avatar-eyes" :class="{ 'avatar-eyes--animated': motionEnabled }">
      <span v-for="eye in 2" :key="eye" class="avatar-eyes__tilt">
        <span class="avatar-eyes__capsule" />
      </span>
    </div>
  </div>
</template>

<style scoped>
.avatar-eyes-position {
  position: absolute;
  width: 32%;
  height: 20%;
  transform: translate(-50%, -50%);
}

.avatar-eyes-position--animated {
  transition: left 280ms ease-in-out, top 280ms ease-in-out;
}

.avatar-eyes {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
  height: 100%;
}

.avatar-eyes__tilt {
  display: block;
  width: 29%;
  height: 100%;
  transform: rotate(-24deg);
}

.avatar-eyes__tilt:last-child {
  height: 94%;
  margin-top: -20%;
}

.avatar-eyes__capsule {
  display: block;
  width: 100%;
  height: 100%;
  border-radius: 999px;
  background: #fafafa;
  transform-origin: center;
}

.avatar-eyes--animated {
  animation: eyes-drift 1.8s ease-in-out infinite;
}

.avatar-eyes--animated .avatar-eyes__capsule {
  animation: eyes-blink 6.8s ease-in-out infinite;
}

@keyframes eyes-drift {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-3px); }
}

@keyframes eyes-blink {
  0%, 60%, 78%, 100% { transform: scaleY(1); }
  68%, 70% { transform: scaleY(0.06); }
}

@media (prefers-reduced-motion: reduce) {
  .avatar-eyes-position--animated { transition: none; }
  .avatar-eyes--animated,
  .avatar-eyes--animated .avatar-eyes__capsule {
    animation: none;
  }
}
</style>
