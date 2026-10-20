"""개인 텍스트 메모 + 종료 라운드 경험의 Chroma/Ollama 검색 MCP 서버.

직접 실행: .venv/bin/python agents/mcp_server.py
연결/출처 바인딩은 Table.call_player_tool()을 사용한다.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sys
import threading

# 기존 MCPClient는 스크립트 경로를 직접 실행한다.
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from fastmcp import FastMCP
from agents.config import GameConfig, load_mcp_config
from agents.chunker import Chunk, EventChunker, EventDocument, SCHEMA_VERSION
from agents.models import safe_id


class GameTools:
    """작은 파일 저장 로직과 벡터 검색을 도구 서버 안에 모은다."""
    def __init__(self, config: GameConfig | None = None):
        self.config = config or GameConfig()
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='poker-memory')
        self._jobs: dict[str, object] = {}
        self._errors: dict[str, str] = {}
        self._closed = False
        self.chunker = EventChunker(self.config.embedding_chunk_max_bytes)
        self._chroma = None
        self._collections = {}

    def _identity(self, game_id: str, agent_id: str, round_id: int):
        safe_id(game_id)
        safe_id(agent_id)
        if type(round_id) is not int or round_id < 1:
            raise ValueError('round_id must be a positive integer')

    def _memo_path(self, game_id: str, agent_id: str, round_id: int, scope: str) -> Path:
        self._identity(game_id, agent_id, round_id)
        root = self.config.data_dir / 'memos'
        if scope == 'long':
            return root / agent_id / 'opponents.txt'
        folder = root / agent_id / game_id
        if scope == 'game':
            return folder / 'game.txt'
        if scope == 'round':
            return folder / 'rounds' / f'{round_id:06d}.txt'
        raise ValueError('scope must be round, game or long')

    @staticmethod
    def _write(path: Path, content: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + '.tmp')
        temporary.write_text(content, encoding='utf-8')
        temporary.replace(path)

    def read_memo(self, game_id: str, agent_id: str, round_id: int, scope: str) -> dict:
        with self._lock:
            path = self._memo_path(game_id, agent_id, round_id, scope)
            return {'agent_id': agent_id, 'scope': scope,
                    'text': (path.read_text(encoding='utf-8') if path.exists() else '')[:self.config.memo_limit(scope)]}

    def write_memo(self, game_id: str, agent_id: str, round_id: int,
                   scope: str, text: str, append: bool = False) -> dict:
        with self._lock:
            path = self._memo_path(game_id, agent_id, round_id, scope)
            previous = path.read_text(encoding='utf-8') if append and path.exists() else ''
            content = previous + ('\n' if previous else '') + text
            if len(content) > self.config.memo_limit(scope):
                raise ValueError('memo is too long; rewrite or summarize it')
            self._write(path, content)
            return {'agent_id': agent_id, 'scope': scope, 'written': True, 'chars': len(content)}

    def read_memories(self, game_id: str, agent_id: str, round_id: int) -> dict:
        """각 LLM 호출의 기본 입력. 세 메모를 하나의 lock 안에서 읽는다."""
        with self._lock:
            return {scope: self.read_memo(game_id, agent_id, round_id, scope)['text']
                    for scope in ('round', 'game', 'long')}

    def _experience_path(self, game_id: str) -> Path:
        return self.config.data_dir / 'experiences' / f'{safe_id(game_id)}.json'

    def _load(self, game_id: str) -> list[dict]:
        path = self._experience_path(game_id)
        if not path.exists():
            seeds = json.loads((Path(__file__).parent / 'seed_rounds.json').read_text(encoding='utf-8'))
            self._write(path, json.dumps(seeds[-self.config.memory_round_limit:], ensure_ascii=False))
        rows = json.loads(path.read_text(encoding='utf-8'))
        retained = rows[-self.config.memory_round_limit:]
        if retained != rows:
            self._write(path, json.dumps(retained, ensure_ascii=False))
        return retained

    @staticmethod
    def _public_record(record: dict) -> dict:
        """Table의 공개 기록만 수용. 폴드 카드·전체 카드·개인 메모는 받지 않는다."""
        if set(record) - {'game_id', 'round_id', 'source', 'events', 'result', 'players'}:
            raise ValueError('unknown or private record fields')
        safe_id(record['game_id'])
        if type(record['round_id']) is not int or record['round_id'] < 1:
            raise ValueError('invalid round ID')
        events = record['events']
        allowed = {
            'RoundStarted': {'order', 'pot'},
            'Speech': {'player_id', 'channel', 'text', 'target', 'request_id'},
            'PlayerSilent': {'player_id'},
            'AgentFailed': {'player_id', 'message'},
            'BetPlaced': {'player_id', 'action', 'amount', 'raise_size', 'contributions', 'current_bet', 'pot', 'automatic'},
            'RoundEnded': {'winner', 'draw', 'pot', 'carry_pot', 'revealed_cards', 'eliminated', 'game_winner', 'finished'},
        }
        folded = set()
        last_id = -1
        for event in events:
            kind = event.get('type')
            if kind not in allowed or set(event) - allowed[kind] - {'event_id', 'round_id', 'type'}:
                raise ValueError('unknown or private event fields')
            if event['event_id'] <= last_id or event['round_id'] != record['round_id']:
                raise ValueError('unordered events or mismatched round')
            last_id = event['event_id']
            if kind == 'BetPlaced' and event['action'] == 'fold':
                folded.add(event['player_id'])
        if not events or events[-1]['type'] != 'RoundEnded':
            raise ValueError('only completed rounds can be stored')
        result = record['result']
        if set(result) != {'winner', 'draw', 'pot', 'carry_pot', 'revealed_cards', 'eliminated'}:
            raise ValueError('invalid result fields')
        if folded & result['revealed_cards'].keys():
            raise ValueError('folded cards must not be published')
        if events[-1].get('revealed_cards') != result['revealed_cards']:
            raise ValueError('inconsistent public cards')
        # players 원본은 장기 경험 검색에 필요 없으므로 보관하지 않는다.
        return {'id': f"played-{record['round_id']}", 'source': 'played',
                'round_id': record['round_id'], 'events': events, 'result': result}

    def store_experience(self, record: dict) -> dict:
        public = self._public_record(record)
        game_id = record['game_id']
        with self._lock:
            rows = self._load(game_id)
            existing = next((row for row in rows if row['id'] == public['id']), None)
            if existing is not None and existing != public:
                raise ValueError('round ID already stores a different record')
            # 오래된 기록 재전송은 FIFO에 다시 넣지 않는다.
            played = [row['round_id'] for row in rows if row['source'] == 'played']
            if existing is None and (not played or public['round_id'] > max(played)):
                rows = (rows + [public])[-self.config.memory_round_limit:]
                self._write(self._experience_path(game_id), json.dumps(rows, ensure_ascii=False))
            queued = self._queue(game_id)
        return {'stored': True, 'experience_id': public['id'], 'index_queued': queued}

    def _queue(self, game_id: str) -> bool:
        if self._closed:
            return False
        job = self._jobs.get(game_id)
        if job is not None and not job.done():
            return True
        self._jobs[game_id] = self._executor.submit(self._index_worker, game_id)
        return True

    def _index_worker(self, game_id: str):
        try:
            # 저장 중 들어온 라운드도 빠짐없이 반영한다.
            while True:
                with self._lock:
                    rows = self._load(game_id)
                self.index_records(game_id, rows)
                with self._lock:
                    if self._load(game_id) == rows:
                        self._errors.pop(game_id, None)
                        return
        except Exception as error:
            with self._lock:
                self._errors[game_id] = f'{type(error).__name__}: {error}'

    def _collection(self, game_id: str):
        try:
            import chromadb
        except ImportError as error:
            raise RuntimeError('RAG requires: pip install -r agents/requirements-rag.txt') from error
        signature = (f'{game_id}:{self.config.embedding_model}:{SCHEMA_VERSION}:'
                     f'{self.config.embedding_chunk_max_bytes}:{self.config.embedding_context_tokens}')
        key = hashlib.sha256(signature.encode()).hexdigest()[:24]
        with self._lock:
            if self._chroma is None:
                self._chroma = chromadb.PersistentClient(path=str(self.config.data_dir / 'chroma'))
            if key not in self._collections:
                self._collections[key] = self._chroma.get_or_create_collection(
                    name='poker-' + key, embedding_function=None)
            return self._collections[key]

    def _embed(self, texts: list[str]) -> list[list[float]]:
        response = httpx.post(self.config.embedding_url, json={
            'model': self.config.embedding_model, 'input': texts, 'truncate': False,
            'options': {'num_ctx': self.config.embedding_context_tokens}},
            timeout=self.config.embedding_timeout)
        response.raise_for_status()
        vectors = response.json()['embeddings']
        if len(vectors) != len(texts) or any(not vector for vector in vectors):
            raise ValueError('invalid embedding response')
        return vectors

    def _embed_chunks(self, chunks: list[Chunk]) -> list[tuple[Chunk, list[float]]]:
        """실제 길이 초과만 분할 재시도. 네트워크/모델 오류는 숨기지 않는다."""
        try:
            return list(zip(chunks, self._embed([chunk.text for chunk in chunks])))
        except httpx.HTTPStatusError as error:
            message = error.response.text.lower()
            is_length = error.response.status_code == 400 and any(
                word in message for word in ('context length', 'input length', 'too long'))
            if not is_length:
                raise
            if len(chunks) > 1:
                middle = len(chunks) // 2
                return self._embed_chunks(chunks[:middle]) + self._embed_chunks(chunks[middle:])
            return self._embed_chunks(self.chunker.bisect(chunks[0]))

    @staticmethod
    def _complete_event(metadata: list[dict]) -> bool:
        if not metadata:
            return False
        count = metadata[0].get('chunk_count', 0)
        return (count > 0 and len(metadata) == count
                and all(item.get('chunk_count') == count for item in metadata)
                and {item.get('chunk_index') for item in metadata} == set(range(1, count + 1)))

    def index_records(self, game_id: str, rows: list[dict]):
        """라운드 보관량을 유지하며 사건별 청크를 추가/퇴출. 완성된 사건은 재임베딩하지 않는다."""
        collection = self._collection(game_id)
        events = [event for row in rows for event in self.chunker.events(row)]
        wanted = {event.key for event in events}
        current = collection.get(include=['metadatas'])
        grouped = {}
        stale = []
        for item_id, meta in zip(current['ids'], current['metadatas']):
            key = (meta or {}).get('event_key')
            if key not in wanted:
                stale.append(item_id)
            else:
                grouped.setdefault(key, []).append(meta)
        if stale:
            collection.delete(ids=stale)
        pending = [event for event in events if not self._complete_event(grouped.get(event.key, []))]
        chunks = [chunk for event in pending for chunk in self.chunker.chunks(event)]
        embedded = []
        for start in range(0, len(chunks), self.config.embedding_batch_size):
            embedded.extend(self._embed_chunks(chunks[start:start + self.config.embedding_batch_size]))
        by_event = {}
        for chunk, vector in embedded:
            by_event.setdefault(chunk.event.key, []).append((chunk, vector))
        payload = []
        for key, parts in by_event.items():
            for index, (chunk, vector) in enumerate(parts, 1):
                payload.append((f'{key}:chunk-{index}', chunk.text, vector,
                                chunk.metadata(index, len(parts))))
        # 부분 저장 후 재시도한 사건은 더 이상 필요 없는 조각을 제거한다.
        replacing = set(by_event)
        desired_ids = {item[0] for item in payload}
        obsolete = [item_id for item_id, meta in zip(current['ids'], current['metadatas'])
                    if (meta or {}).get('event_key') in replacing and item_id not in desired_ids]
        if obsolete:
            collection.delete(ids=obsolete)
        for start in range(0, len(payload), self.config.embedding_batch_size):
            batch = payload[start:start + self.config.embedding_batch_size]
            collection.upsert(ids=[item[0] for item in batch], documents=[item[1] for item in batch],
                              embeddings=[item[2] for item in batch], metadatas=[item[3] for item in batch])

    def search_experiences(self, game_id: str, agent_id: str, round_id: int,
                           query: str, limit: int = 3) -> dict:
        self._identity(game_id, agent_id, round_id)
        if not query.strip() or len(query) > self.config.max_message_chars or not 1 <= limit <= 20:
            raise ValueError('invalid query or limit')
        with self._lock:
            rows = self._load(game_id)
            job = self._jobs.get(game_id)
            if job is None or job.done():
                self._queue(game_id)
            error = self._errors.get(game_id)
        try:
            collection = self._collection(game_id)
            current = collection.get(include=['metadatas'])
            parents = {row['id'] for row in rows}
            count = sum(1 for meta in current['metadatas']
                        if (meta or {}).get('experience_id') in parents)
            if not count:
                return {'status': 'unavailable' if error else 'indexing', 'matches': [], 'error': error}
            # 긴 질의도 생략하지 않고 분할 검색한 뒤 거리 기준으로 중복 결과를 합친다.
            event = EventDocument('query', 'query', round_id, 0, 'query', agent_id, query)
            embedded = self._embed_chunks(self.chunker.chunks(event))
            matches = {}
            for start in range(0, len(embedded), self.config.embedding_batch_size):
                batch = embedded[start:start + self.config.embedding_batch_size]
                result = collection.query(query_embeddings=[vector for _, vector in batch],
                    n_results=min(limit, count), where={'experience_id': {'$in': sorted(parents)}},
                    include=['documents', 'metadatas', 'distances'])
                for ids, documents, metadatas, distances in zip(result['ids'], result['documents'],
                                                              result['metadatas'], result['distances']):
                    for item_id, text, meta, distance in zip(ids, documents, metadatas, distances):
                        if item_id not in matches or distance < matches[item_id]['distance']:
                            matches[item_id] = {'id': item_id, 'text': text,
                                                'metadata': meta, 'distance': distance}
            return {'status': 'ready', 'matches': sorted(matches.values(),
                    key=lambda item: item['distance'])[:limit]}
        except Exception as error:
            return {'status': 'unavailable', 'matches': [], 'error': f'{type(error).__name__}: {error}'}

    def memory_status(self, game_id: str, agent_id: str, round_id: int) -> dict:
        self._identity(game_id, agent_id, round_id)
        with self._lock:
            rows = self._load(game_id)
            job = self._jobs.get(game_id)
            return {'records': len(rows), 'indexing': job is not None and not job.done(),
                    'error': self._errors.get(game_id)}

    def close(self):
        self._closed = True
        self._executor.shutdown(wait=True)


mcp = FastMCP('indian-poker-memory')
_tools: GameTools | None = None


def get_tools() -> GameTools:
    global _tools
    if _tools is None:
        _tools = GameTools(load_mcp_config())
    return _tools


@mcp.tool()
def read_memo(game_id: str, agent_id: str, round_id: int, scope: str) -> dict:
    """출처 참가자의 round/game/long 텍스트 메모를 읽는다. 출처는 실행 코드가 주입한다."""
    return get_tools().read_memo(game_id, agent_id, round_id, scope)


@mcp.tool()
def read_memories(game_id: str, agent_id: str, round_id: int) -> dict:
    """매 LLM 호출에 넣을 round/game/long 메모를 함께 읽는다."""
    return get_tools().read_memories(game_id, agent_id, round_id)


@mcp.tool()
def write_memo(game_id: str, agent_id: str, round_id: int, scope: str,
               text: str, append: bool = False) -> dict:
    """출처 참가자의 메모를 덮어쓰거나 append한다. 모델은 출처 ID를 선택하지 않는다."""
    return get_tools().write_memo(game_id, agent_id, round_id, scope, text, append)


@mcp.tool()
def search_experiences(game_id: str, agent_id: str, round_id: int, query: str, limit: int = 3) -> dict:
    """해당 테이블의 공개 라운드 경험을 의미 검색한다. 없으면 indexing/unavailable 반환."""
    return get_tools().search_experiences(game_id, agent_id, round_id, query, limit)


@mcp.tool()
def memory_status(game_id: str, agent_id: str, round_id: int) -> dict:
    """경험 보관 수·색인 작업 상태·오류를 조회한다."""
    return get_tools().memory_status(game_id, agent_id, round_id)


@mcp.tool()
def store_experience(record: dict) -> dict:
    """테이블 전용: 종료 라운드를 저장하고 색인 작업을 예약한다. 플레이어에게 노출하지 않는다."""
    return get_tools().store_experience(record)


if __name__ == '__main__':
    try:
        mcp.run(show_banner=False)
    finally:
        if _tools is not None:
            _tools.close()
