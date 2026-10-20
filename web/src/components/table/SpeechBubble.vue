<script setup>
defineProps({ speech: { type: Object, default: null } });
defineEmits(['expand-speech']);
</script>

<template>
  <div class="speech-slot">
    <div v-if="speech" class="speech-bubble" aria-label="최근 확정 발언">
      <p
        :key="speech.eventId"
        class="speech-bubble__text"
        tabindex="0"
        role="region"
        aria-label="발언 내용. 긴 발언은 스크롤해서 읽을 수 있습니다."
      >{{ speech.text }}</p>
      <button
        v-if="Number.isInteger(speech.eventId)"
        type="button"
        class="speech-bubble__expand"
        @click="$emit('expand-speech', speech.eventId)"
      >기록에서 전체 보기 <span aria-hidden="true">↗</span></button>
    </div>
  </div>
</template>

<style scoped>
.speech-slot { width: 100%; height: 128px; padding-bottom: 16px; }
.speech-bubble { position: relative; padding: 12px 15px 10px; border: 1px solid #e2e4eb; border-radius: 16px; background: #fff; text-align: left; box-shadow: 0 3px 8px #20252b05; }
.speech-bubble::after { content: ''; position: absolute; left: calc(50% - 6px); bottom: -7px; width: 12px; height: 12px; border-right: 1px solid #e2e4eb; border-bottom: 1px solid #e2e4eb; background: #fff; transform: rotate(45deg); }
.speech-bubble__text { max-height: 63px; overflow-y: auto; overscroll-behavior: contain; scrollbar-width: thin; scrollbar-color: #b9c2d3 transparent; scrollbar-gutter: stable; margin: 0; font-size: 14px; line-height: 21px; overflow-wrap: anywhere; white-space: pre-wrap; }
.speech-bubble__text:focus-visible { outline: 2px solid #6878ac; outline-offset: 2px; border-radius: 3px; }
.speech-bubble__expand { display: block; margin: 6px 0 0 auto; padding: 0; border: 0; background: none; color: #68718a; font: inherit; font-size: 11px; cursor: pointer; }
.speech-bubble__expand:hover { color: #343d62; text-decoration: underline; }
.speech-bubble__expand:focus-visible { outline: 2px solid #6878ac; outline-offset: 3px; }
</style>
