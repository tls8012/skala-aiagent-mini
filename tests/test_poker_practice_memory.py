"""연습 파일의 무호출 경계와 실행본의 실제 개인 메모 수명 검사."""
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from agents import GameConfig, Table
from agents.cards import Card
from agents.demo import ScriptedPokerLLM
from agents.graph import build_graph
from agents.mcp_server import GameTools
from agents.models import AgentDecision, Bet, PlayerCommand
from agents.player_llm import PlayerLLM
from agents.poker_llm import PokerLLM
from agents.runner import RoundRunner


class PracticeAndMemoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder=TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.config=GameConfig(data_dir=Path(self.folder.name),target_chips=999)

    async def test_practice_stubs_do_not_delegate_or_call_a_model(self):
        from agents.graph_practice import build_graph as practice_graph
        from agents.poker_llm_practice import PokerLLMPractice
        table=Table(['A','B'],config=self.config)
        table.start_round()
        class Trap:
            async def complete(self,*args,**kwargs):
                raise AssertionError('practice must not call a model before implementation')
        backend=PokerLLMPractice(table,None,'A',Trap())
        observation=table.observe('A')
        with self.assertRaises(NotImplementedError):
            practice_graph(backend)
        with self.assertRaises(NotImplementedError):
            backend.route(observation,{})
        decision=AgentDecision(round_id=1,basis_event=table.event_id,speech='silent')
        calls=[backend.research(observation,{}),backend.chat(observation,{}),
               backend.decide(observation,{}),backend.remember(observation,decision),
               backend.summarize_round(observation)]
        for call in calls:
            with self.assertRaises(NotImplementedError):
                await call

    async def test_real_graph_writes_private_notes_and_revises_them_after_reveal(self):
        import json
        table=Table(['A','B'],config=self.config)
        table.start_round({'A':Card(13,'S'),'B':Card(4,'H')})
        tools=GameTools(self.config)
        self.addCleanup(tools.close)
        class MCP:
            async def call_tool(self,name,arguments,server):
                return getattr(tools,name)(**arguments)
        class Client:
            name='test-client'
            calls=[]
            replies=[json.dumps({'speech':'speak','text':'한번 보자','bet':None,
                'wait_for_dialogue':False,'memory_updates':{
                    'round':'잠정적으로 관찰할 계획','game':'초반에 정보 수집','long':'상대 주장을 검증하자'}},ensure_ascii=False),
                json.dumps({'round':'실제 공개 결과 A가 승리','game':'다음 라운드도 관찰',
                            'long':'한 판만으로 B의 성향을 단정하지 말자'},ensure_ascii=False)]
            def complete(self,system,user,schema=None):
                self.calls.append((system,user,schema))
                return self.replies.pop(0),{}
        client=Client()
        mcp=MCP()
        tools.write_memo(table.game_id,'B',1,'long','B만의 비밀 메모')
        backend=PokerLLM(table,mcp,'A',PlayerLLM(table,mcp,'A',client=client))
        graph=build_graph(backend)
        result=await graph.ainvoke({'observation':table.observe('A').model_dump(),
            'event':table.events[0],'force_bet':False})
        self.assertEqual(tools.read_memo(table.game_id,'A',1,'round')['text'],'잠정적으로 관찰할 계획')
        self.assertEqual(table.events[-1]['type'],'RoundStarted')
        table.apply_decision('A',AgentDecision.model_validate(result['decision']),'first')
        table.submit('B',PlayerCommand(request_id='b',round_id=1,basis_event=table.event_id,
                                     kind='speak',text='나도 보자'))
        for pid in ['A','B']:
            table.submit(pid,PlayerCommand(request_id=pid+'-bet',round_id=1,basis_event=table.event_id,
                                         kind='bet',bet=Bet(action='check')))
        public_before=list(table.events)
        summary=await graph.ainvoke({'observation':table.observe('A').model_dump(),
                                    'event':table.events[-1],'force_bet':False})
        self.assertEqual(summary['decision']['speech'],'unavailable')
        self.assertEqual(table.events,public_before)
        self.assertEqual(tools.read_memories(table.game_id,'A',1),{
            'round':'실제 공개 결과 A가 승리','game':'다음 라운드도 관찰',
            'long':'한 판만으로 B의 성향을 단정하지 말자'})
        self.assertIn('잠정적으로 관찰할 계획',client.calls[-1][0])
        self.assertNotIn('B만의 비밀 메모',client.calls[-1][0])
        archived=next(table.archive_dir.glob('*.json')).read_text()
        self.assertNotIn('초반에 정보 수집',archived)
        self.assertNotIn('memory_updates',archived)

    async def test_runner_summarizes_folded_players_without_revealing_their_card(self):
        table=Table(['A','B'],config=self.config)
        summaries={}
        class Backend(ScriptedPokerLLM):
            async def decide(self,observation,research,force_bet=False,feedback=''):
                result=await super().decide(observation,research,force_bet,feedback)
                if self.player_id=='B' and observation.allowed_bets:
                    return result.model_copy(update={'bet':Bet(action='fold')})
                return result
            async def summarize_round(self,observation,feedback=''):
                summaries[self.player_id]=observation.result
                return await super().summarize_round(observation,feedback)
        graphs={pid:build_graph(Backend(table,None,pid,None)) for pid in table.players}
        runner=RoundRunner(table,graphs)
        await runner.run_round()
        self.assertEqual(set(summaries),{'A','B'})
        self.assertNotIn('B',summaries['A']['revealed_cards'])
        self.assertNotIn('B',summaries['B']['revealed_cards'])
        self.assertEqual(runner.errors,{})
        self.assertEqual(table.events[-1]['type'],'RoundEnded')


if __name__=='__main__':
    unittest.main()
