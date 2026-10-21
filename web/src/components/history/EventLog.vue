<script setup>
import { nextTick } from 'vue';

const props = defineProps({
  entries: { type: Array, default: () => [] },
  selectedEventId: { type: Number, default: null },
  selectedFrameId: { type: String, default: null },
  replayAvailable: { type: Boolean, default: false },
});
const emit = defineEmits(['seek-frame']);
function seek(entry) { if (props.replayAvailable && entry.frameId) emit('seek-frame', entry.frameId); }
const elements = new Map();
function setEntryRef(id, element) {
  if (id == null) return;
  if (element) elements.set(id, element);
  else elements.delete(id);
}
async function focusEvent(id) {
  await nextTick();
  const element = elements.get(id);
  element?.scrollIntoView({ block: 'nearest' });
  element?.focus({ preventScroll: true });
}
// 같은 말풍선을 다시 눌러도 기록으로 돌아갈 수 있는 화면 전용 메서드.
defineExpose({ focusEvent });
</script>

<template>
  <aside class="event-log" aria-label="대화와 사건 기록">
    <header class="event-log__header"><h2>테이블 기록</h2><span>{{ entries.length }}개 사건</span></header>
    <div class="event-log__entries">
      <p v-if="!entries.length" class="event-log__empty">아직 기록이 없습니다.</p>
      <article
        v-for="entry in entries"
        :key="entry.id"
        :ref="element => setEntryRef(entry.eventId, element)"
        class="event-log__entry"
        :class="{ 'event-log__entry--selected': (selectedEventId != null && entry.eventId === selectedEventId) || (entry.frameId && entry.frameId === selectedFrameId), 'event-log__entry--seekable': replayAvailable && entry.frameId }"
        tabindex="-1"
        @click="seek(entry)"
      >
        <div class="event-log__meta"><strong>{{ entry.playerLabel || (entry.playerId === 'human' ? '나' : entry.playerId || '테이블') }}</strong><span>라운드 {{ entry.roundId }} · #{{ entry.eventId ?? '—' }}</span></div>
        <p>{{ entry.text }}</p>
        <button v-if="replayAvailable && entry.frameId" type="button" :aria-current="entry.frameId === selectedFrameId ? 'step' : undefined" @click.stop="seek(entry)">이 시점 보기</button>
      </article>
    </div>
  </aside>
</template>

<style scoped>
.event-log { display: flex; flex-direction: column; min-height: 0; height: 100%; max-height: 820px; overflow: hidden; border: 1px solid #e0e3e9; border-radius: 20px; background: #ffffffb3; }
.event-log__header { display: flex; justify-content: space-between; align-items: center; padding: 20px; border-bottom: 1px solid #e8eaf0; }
.event-log__header h2 { margin: 0; font-size: 15px; }
.event-log__header > span { color: #8a91a0; font-size: 11px; }
.event-log__entries { overflow-y: auto; overscroll-behavior: contain; min-height: 0; padding: 12px; }
.event-log__empty { padding: 15px; color: #8a91a0; font-size: 13px; }
.event-log__entry { padding: 14px 12px; border: 1px solid transparent; border-radius: 12px; outline: none; }
.event-log__entry + .event-log__entry { margin-top: 5px; }
.event-log__entry--seekable { cursor: pointer; }
.event-log__entry--seekable:hover { background: #f0f3f9; }
.event-log__entry--selected, .event-log__entry:focus-visible { border-color: #bfc9df; background: #edf1f9; }
.event-log__meta { display: flex; align-items: center; justify-content: space-between; gap: 10px; font-size: 12px; }
.event-log__meta > span { color: #949ba8; font-size: 10px; }
.event-log__entry p { margin: 8px 0 0; color: #5a6272; font-size: 13px; line-height: 1.8; overflow-wrap: anywhere; white-space: pre-wrap; }
.event-log__entry button { margin-top: 10px; border: 1px solid #ccd3e0; border-radius: 7px; padding: 5px 9px; background: #fff; color: #4c5c7e; cursor: pointer; }
</style>
