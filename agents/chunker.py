"""공개 사건별 무손실 청킹. 바이트 예산은 토큰 추정용이며 최종 한도는 Ollama가 검증한다."""
from dataclasses import dataclass
import hashlib
import json

SCHEMA_VERSION = 'event-chunks-v1'


@dataclass(frozen=True)
class EventDocument:
    experience_id: str
    source: str
    round_id: int
    event_id: int
    event_type: str
    player_id: str
    text: str

    @property
    def key(self) -> str:
        digest = hashlib.sha256(self.text.encode('utf-8')).hexdigest()[:16]
        return f'{self.experience_id}:event-{self.event_id}:{digest}'

    @property
    def prefix(self) -> str:
        return f'source={self.source} round={self.round_id} event={self.event_id}\n'


@dataclass(frozen=True)
class Chunk:
    event: EventDocument
    body: str

    @property
    def text(self) -> str:
        return self.event.prefix + self.body

    def metadata(self, index: int, count: int) -> dict:
        return {'experience_id': self.event.experience_id, 'source': self.event.source,
                'round_id': self.event.round_id, 'event_id': self.event.event_id,
                'event_type': self.event.event_type, 'player_id': self.event.player_id,
                'event_key': self.event.key, 'chunk_index': index, 'chunk_count': count,
                'schema_version': SCHEMA_VERSION}


class EventChunker:
    def __init__(self, max_bytes: int = 1536):
        if max_bytes < 64:
            raise ValueError('chunk byte budget must be at least 64')
        self.max_bytes = max_bytes

    def events(self, record: dict) -> list[EventDocument]:
        result = []
        for event in record['events']:
            if event['type'] == 'Speech' and event.get('channel') not in {'game', 'mixed'}:
                continue
            # 사건 전체를 사용한다. 긴 발언과 결과 JSON도 자르거나 생략하지 않는다.
            text = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            result.append(EventDocument(record['id'], record['source'], record['round_id'],
                event['event_id'], event['type'], event.get('player_id', ''), text))
        return result

    @staticmethod
    def split_text(text: str, max_bytes: int) -> list[str]:
        """유니코드 문자를 깨지 않고 분할. 조각을 이어 붙이면 원문과 정확히 같다."""
        if not text:
            return ['']
        if max_bytes < 4:
            raise ValueError('byte budget too small for a Unicode character')
        parts = []
        start = 0
        while start < len(text):
            low, high = start + 1, len(text)
            end = start
            while low <= high:
                mid = (low + high) // 2
                if len(text[start:mid].encode('utf-8')) <= max_bytes:
                    end = mid
                    low = mid + 1
                else:
                    high = mid - 1
            if end == start:
                raise ValueError('cannot fit one character')
            if end < len(text):
                # 가능한 경우 문장/단어 경계를 택하되 공백도 보존한다.
                boundary = max(text.rfind('\n', start, end), text.rfind(' ', start, end)) + 1
                if boundary > start + (end - start) // 2:
                    end = boundary
            parts.append(text[start:end])
            start = end
        return parts

    def chunks(self, event: EventDocument) -> list[Chunk]:
        available = self.max_bytes - len(event.prefix.encode('utf-8'))
        return [Chunk(event, part) for part in self.split_text(event.text, available)]

    def bisect(self, chunk: Chunk) -> list[Chunk]:
        """실제 모델에서 context 초과가 난 조각만 더 작게 나눈다."""
        if len(chunk.body) <= 1:
            raise ValueError('embedding context cannot fit the event header and one character')
        middle = len(chunk.body) // 2
        return [Chunk(chunk.event, chunk.body[:middle]), Chunk(chunk.event, chunk.body[middle:])]
