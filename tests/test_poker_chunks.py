"""사건 청킹·실제 Chroma 저장·매 호출 메모 첨부 검사."""
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import httpx
from agents import GameConfig, Table
from agents.chunker import EventChunker, EventDocument
from agents.mcp_server import GameTools
from agents.player_llm import PlayerLLM


class ChunkTests(unittest.TestCase):
    def event(self, text):
        return EventDocument('played-1','played',1,12,'Speech','A',text)

    def test_long_unicode_event_is_lossless_and_within_byte_budget(self):
        text=('한글 발언과 😀 emoji, 日本語, café\n'*500)+'마지막 결정'
        chunker=EventChunker(400)
        chunks=chunker.chunks(self.event(text))
        self.assertGreater(len(chunks),2)
        self.assertEqual(''.join(chunk.body for chunk in chunks),text)
        for index,chunk in enumerate(chunks,1):
            self.assertLessEqual(len(chunk.text.encode('utf-8')),400)
            metadata=chunk.metadata(index,len(chunks))
            self.assertEqual(metadata['event_id'],12)
            self.assertEqual(metadata['chunk_index'],index)
            self.assertEqual(metadata['chunk_count'],len(chunks))

    def test_events_stay_separate_and_speech_is_not_truncated(self):
        record={'id':'seed-1','source':'seed','round_id':1,'events':[
            {'event_id':1,'type':'Speech','channel':'game','player_id':'A','text':'길다'*2000},
            {'event_id':2,'type':'Speech','channel':'chat','player_id':'A','text':'잡담'},
            {'event_id':3,'type':'BetPlaced','player_id':'B','action':'call','amount':0}]}
        events=EventChunker().events(record)
        self.assertEqual([e.event_id for e in events],[1,3])
        self.assertEqual(json.loads(events[0].text)['text'],'길다'*2000)

    def test_actual_context_error_splits_only_the_oversized_input(self):
        with TemporaryDirectory() as folder:
            tools=GameTools(GameConfig(data_dir=Path(folder)))
            try:
                chunker=tools.chunker
                chunks=chunker.chunks(self.event('긴말'*300))
                def embed(texts):
                    if any(len(text.encode('utf-8'))>180 for text in texts):
                        raise httpx.HTTPStatusError('too long',request=httpx.Request('POST','http://test'),
                            response=httpx.Response(400,text='the input length exceeds the context length'))
                    return [[1.0,0.0] for _ in texts]
                with patch.object(tools,'_embed',side_effect=embed):
                    result=tools._embed_chunks(chunks)
                self.assertGreater(len(result),len(chunks))
                self.assertEqual(''.join(chunk.body for chunk,_ in result),'긴말'*300)
                self.assertTrue(all(len(chunk.text.encode('utf-8'))<=180 for chunk,_ in result))
                with patch.object(tools,'_embed',side_effect=RuntimeError('offline')):
                    with self.assertRaisesRegex(RuntimeError,'offline'):
                        tools._embed_chunks(chunks)
            finally:
                tools.close()

    def test_embedding_request_uses_latest_and_does_not_silently_truncate(self):
        with TemporaryDirectory() as folder:
            tools=GameTools(GameConfig(data_dir=Path(folder)))
            try:
                response=httpx.Response(200,request=httpx.Request('POST','http://test'),
                                        json={'embeddings':[[1.0,2.0]]})
                with patch('agents.mcp_server.httpx.post',return_value=response) as post:
                    tools._embed(['text'])
                payload=post.call_args.kwargs['json']
                self.assertEqual(payload['model'],'embeddinggemma:latest')
                self.assertFalse(payload['truncate'])
                self.assertEqual(payload['options']['num_ctx'],2048)
            finally:
                tools.close()


@unittest.skipUnless(importlib.util.find_spec('chromadb'),'optional Chroma dependency is not installed')
class ChromaTests(unittest.TestCase):
    def test_persistent_event_chunks_query_retry_and_round_eviction(self):
        with TemporaryDirectory() as folder:
            config=GameConfig(data_dir=Path(folder),embedding_chunk_max_bytes=400)
            tools=GameTools(config)
            try:
                rows=tools._load('game')[:2]
                rows[0]['events'][1]['text']='아주 긴 발언 '*600
                with patch.object(tools,'_embed',side_effect=lambda texts:[[1.0,0.5,0.25] for _ in texts]) as embed, \
                     patch.object(tools,'_queue',return_value=True):
                    tools.index_records('game',rows)
                    collection=tools._collection('game')
                    saved=collection.get(include=['metadatas','documents'])
                    self.assertGreater(len(saved['ids']),len(rows))
                    long=[meta for meta in saved['metadatas'] if meta['experience_id']==rows[0]['id']
                          and meta['event_id']==rows[0]['events'][1]['event_id']]
                    self.assertGreater(len(long),1)
                    self.assertEqual({meta['chunk_count'] for meta in long},{len(long)})
                    calls=embed.call_count
                    tools.index_records('game',rows)
                    self.assertEqual(embed.call_count,calls)
                    # 부분 저장된 사건도 다음 색인에서 복구한다.
                    first=saved['ids'][0]
                    collection.delete(ids=[first])
                    tools.index_records('game',rows)
                    self.assertIn(first,collection.get(include=[])['ids'])
                    result=tools.search_experiences('game','A',1,'과거 발언',3)
                    self.assertEqual(result['status'],'ready',result)
                    self.assertEqual(len(result['matches']),3)
                    self.assertIn('chunk_index',result['matches'][0]['metadata'])
                    tools.index_records('game',rows[1:])
                    remaining=collection.get(include=['metadatas'])
                    self.assertFalse(any(meta['experience_id']==rows[0]['id'] for meta in remaining['metadatas']))
                # 같은 디렉터리를 새 클라이언트로 열어도 벡터가 남는다.
                other=GameTools(config)
                try:
                    self.assertEqual(other._collection('game').count(),len(remaining['ids']))
                finally:
                    other.close()
            finally:
                tools.close()


class MemoryInjectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_three_memories_are_in_every_call_and_refreshed(self):
        with TemporaryDirectory() as folder:
            config=GameConfig(data_dir=Path(folder),ollama_model='another-model',
                              round_memo_max_chars=10,game_memo_max_chars=12,long_memo_max_chars=14)
            table=Table(['A','B'],config=config)
            table.start_round()
            tools=GameTools(config)
            self.addCleanup(tools.close)
            class MCP:
                async def call_tool(self,name,arguments,server):
                    self.last=(name,arguments,server)
                    return getattr(tools,name)(**arguments)
            class Client:
                name='fake'
                calls=[]
                def complete(self,system,user,schema=None):
                    self.calls.append((system,user,schema))
                    return 'ok',{'usd':0}
                async def stream(self,system,user,on_token=None):
                    self.calls.append((system,user,None))
                    if on_token:
                        on_token('ok')
                    return 'ok',{'usd':0}
            client=Client()
            mcp=MCP()
            llm=PlayerLLM(table,mcp,'A',client)
            self.assertEqual(PlayerLLM(table,mcp,'A').name,'ollama:another-model')
            for scope,text in [('round','단기 판단'),('game','중기 판단'),('long','장기 판단')]:
                tools.write_memo(table.game_id,'A',1,scope,text)
            await llm.complete('지침','현재 입력',schema={'type':'object'})
            prompt=client.calls[-1][0]
            for text in ('단기 판단','중기 판단','장기 판단'):
                self.assertIn(text,prompt)
            tools.write_memo(table.game_id,'A',1,'round','바뀐 판단')
            tokens=[]
            await llm.stream('지침','다음 입력',tokens.append)
            self.assertIn('바뀐 판단',client.calls[-1][0])
            self.assertNotIn('단기 판단',client.calls[-1][0])
            self.assertEqual(tokens,['ok'])
            self.assertEqual(mcp.last[1]['agent_id'],'A')
            self.assertEqual(mcp.last[2],'poker')

    async def test_scope_limits_apply_on_write_read_and_old_files(self):
        with TemporaryDirectory() as folder:
            tools=GameTools(GameConfig(data_dir=Path(folder),round_memo_max_chars=3,
                                      game_memo_max_chars=4,long_memo_max_chars=5))
            self.addCleanup(tools.close)
            for scope,limit in [('round',3),('game',4),('long',5)]:
                with self.assertRaises(ValueError):
                    tools.write_memo('game','A',1,scope,'가'*(limit+1))
                tools._write(tools._memo_path('game','A',1,scope),'기존긴파일'*100)
                self.assertEqual(len(tools.read_memo('game','A',1,scope)['text']),limit)
            bundle=tools.read_memories('game','A',1)
            self.assertEqual({key:len(value) for key,value in bundle.items()},
                             {'round':3,'game':4,'long':5})


if __name__=='__main__':
    unittest.main()
