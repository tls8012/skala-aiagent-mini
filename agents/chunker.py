"""공개 원문을 보존하고 검색용 게임 구절을 무손실 청킹한다. 최종 길이는 Ollama가 검증한다."""
from dataclasses import dataclass
import hashlib
import json

SCHEMA_VERSION = 'event-chunks-v1'


def game_speech_text(message: dict) -> str:
    """해석된 인간 발언은 game 구절만 사용. 해석 없는 기존 게임 발언은 원문 유지."""
    if message.get('channel') not in {'game', 'mixed'}:
        return ''
    interpretation = message.get('interpretation')
    if interpretation is None:
        return message['text']
    return '\n'.join(intent['text'] for intent in interpretation['intents']
                     if intent['kind'] == 'game')


@dataclass(frozen=True)
class EventDocument:
    experience_id: str
    source: str
    round_id: int
    event_id: int
    event_type: str
    player_id: str
    text: str
    embedding_text: str | None = None

    @property
    def indexed_text(self) -> str:
        return self.text if self.embedding_text is None else self.embedding_text

    @property
    def key(self) -> str:
        digest = hashlib.sha256(self.indexed_text.encode('utf-8')).hexdigest()[:16]
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
            embedding_text = None
            if event['type'] == 'Speech':
                game_text = game_speech_text(event)
                if not game_text.strip():
                    continue
                if event.get('interpretation') is not None:
                    # interpretation에도 잡담 원문이 있으므로 색인용 복사본에서 함께 제외한다.
                    searchable = {key: value for key, value in event.items() if key != 'interpretation'}
                    searchable['text'] = game_text
                    embedding_text = json.dumps(searchable, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            # 원문과 색인용 본문을 분리한다. 반환/보관용 사건은 변경하지 않는다.
            text = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            result.append(EventDocument(record['id'], record['source'], record['round_id'],
                event['event_id'], event['type'], event.get('player_id', ''), text, embedding_text))
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
        return [Chunk(event, part) for part in self.split_text(event.indexed_text, available)]

    def bisect(self, chunk: Chunk) -> list[Chunk]:
        """실제 모델에서 context 초과가 난 조각만 더 작게 나눈다."""
        if len(chunk.body) <= 1:
            raise ValueError('embedding context cannot fit the event header and one character')
        middle = len(chunk.body) // 2
        return [Chunk(chunk.event, chunk.body[:middle]), Chunk(chunk.event, chunk.body[middle:])]
