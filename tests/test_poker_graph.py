"""사용자가 편집할 그래프와 AI 실행기의 핵심 연결 검사."""
import asyncio
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from agents import GameConfig, Table
from agents.demo import ScriptedPokerLLM
from agents.graph import build_graph
from agents.models import AgentDecision, Bet, PlayerCommand
from agents.poker_llm import PokerLLM
from agents.runner import RoundRunner


class GraphTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder=TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.config=GameConfig(data_dir=Path(self.folder.name),target_chips=999)

    async def test_slow_player_does_not_block_another_players_speech(self):
        table=Table(['A','B','C'],config=self.config)
        first_b_speech=asyncio.Event()
        class Backend(ScriptedPokerLLM):
            async def decide(self,observation,research,force_bet=False,feedback=''):
                assert self.player_id not in observation.visible_cards
                if self.player_id=='A' and observation.required_game_utterances:
                    await first_b_speech.wait()
                return await super().decide(observation,research,force_bet,feedback)
        events=[]
        def on_event(event):
            events.append(event)
            if event['type']=='Speech' and event['player_id']=='B':
                first_b_speech.set()
        graphs={pid:build_graph(Backend(table,None,pid,None)) for pid in table.players}
        runner=RoundRunner(table,graphs,on_event=on_event)
        result=await asyncio.wait_for(runner.run_round(),5)
        speech=[event for event in events if event['type']=='Speech']
        self.assertNotEqual(speech[0]['player_id'],'A')
        self.assertTrue(first_b_speech.is_set())
        self.assertEqual(len(speech),3)
        self.assertEqual(runner.errors,{})
        self.assertEqual(table.round.phase,'revealed')
        self.assertEqual(result['pot'],6)
        self.assertTrue(any(item['rejudge'] for row in runner.results for item in row['results']))

    async def test_one_runner_can_run_next_round_with_rotated_seats(self):
        table=Table(['A','B'],config=self.config)
        graphs={pid:build_graph(ScriptedPokerLLM(table,None,pid,None)) for pid in table.players}
        runner=RoundRunner(table,graphs)
        await runner.run_round()
        await runner.run_round()
        self.assertEqual(table.round.order,['B','A'])
        self.assertEqual(table.round.number,2)
        self.assertEqual(len(list(table.archive_dir.glob('*.json'))),2)
        self.assertFalse(runner._tasks)

    async def test_invalid_decision_gets_bounded_feedback_retry(self):
        table=Table(['A','B'],config=self.config)
        table.start_round()
        class Backend(ScriptedPokerLLM):
            count=0
            feedback=[]
            async def decide(self,observation,research,force_bet=False,feedback=''):
                self.count+=1
                self.feedback.append(feedback)
                if self.count==1:
                    return AgentDecision(round_id=observation.round_id,basis_event=observation.event_id,
                                         speech='silent')
                return await super().decide(observation,research,force_bet,feedback)
        backend=Backend(table,None,'A',None)
        result=await build_graph(backend).ainvoke({'observation':table.observe('A').model_dump(),
                                                 'event':{'type':'RoundStarted'},'force_bet':False})
        self.assertEqual(backend.count,2)
        self.assertIn('required',backend.feedback[1])
        self.assertEqual(result['decision']['speech'],'speak')
        class AlwaysInvalid(Backend):
            async def decide(self,observation,research,force_bet=False,feedback=''):
                return AgentDecision(round_id=observation.round_id,basis_event=observation.event_id,speech='silent')
        with self.assertRaisesRegex(ValueError,'after 3 attempts'):
            await build_graph(AlwaysInvalid(table,None,'A',None)).ainvoke({
                'observation':table.observe('A').model_dump(),'event':{'type':'RoundStarted'},'force_bet':False})

    async def test_human_chat_uses_chat_branch_without_research(self):
        table=Table(['A','B'],'human',config=self.config)
        table.start_round()
        table.submit('human',PlayerCommand(request_id='weather',round_id=1,basis_event=table.event_id,
                     kind='speak',channel='chat',target='B',text='오늘 날씨 어때?'))
        event=table.events[-1]
        class LLM:
            async def complete(self,system,user,schema=None):
                return '날씨를 확인할 도구가 없어.',{}
        class Backend(PokerLLM):
            async def research(self,observation,event):
                raise AssertionError('chat must not research')
        backend=Backend(table,None,'B',LLM())
        result=await build_graph(backend).ainvoke({'observation':table.observe('B').model_dump(),
                                                 'event':event,'force_bet':False})
        decision=AgentDecision.model_validate(result['decision'])
        applied=table.apply_decision('B',decision,'chat-result')
        self.assertEqual(applied[0].status,'accepted')
        self.assertEqual(decision.reply_to,'weather')
        self.assertEqual(table.round.conversation.counts['B'],0)
        self.assertEqual(table.round.conversation.counts['human'],1)

    async def test_model_schema_requires_real_speech_and_valid_turn_action(self):
        table=Table(['A','B'],config=self.config)
        table.start_round()
        class LLM:
            calls=[]
            content='{"speech":"speak","text":"지켜보겠어","bet":null,"wait_for_dialogue":false}'
            async def complete(self,system,user,schema=None):
                self.calls.append(schema)
                return self.content,{}
        llm=LLM()
        backend=PokerLLM(table,None,'A',llm)
        decision=await backend.decide(table.observe('A'),{})
        schema=llm.calls[-1]
        self.assertEqual(set(schema['required']),{'speech','text','bet','wait_for_dialogue','memory_updates'})
        self.assertEqual(schema['properties']['speech']['enum'],['speak'])
        self.assertEqual(schema['properties']['bet'],{'type':'null'})
        self.assertTrue(decision.text)
        # 실제 Ollama에서 큰 maxLength의 문법 초기화 오류가 재발하지 않게 한다.
        import json
        self.assertNotIn('maxLength',json.dumps(schema))
        for pid in ['A','B']:
            table.submit(pid,PlayerCommand(request_id=pid+'-speak',round_id=1,
                basis_event=table.event_id,kind='speak',text='게임 발언'))
        llm.content='{"speech":"silent","text":"","bet":{"action":"check","raise_size":null},"wait_for_dialogue":false}'
        decision=await backend.decide(table.observe('A'),{},force_bet=True)
        self.assertEqual(decision.bet.action,'check')
        variants=llm.calls[-1].get('anyOf',[llm.calls[-1]])
        self.assertTrue(all(v['properties']['bet']=={'$ref':'#/$defs/Bet'} for v in variants))
        self.assertTrue(all(v['properties']['wait_for_dialogue']['enum']==[False] for v in variants))
        bet_schema=llm.calls[-1]['$defs']['Bet']['anyOf']
        self.assertEqual([v['properties']['action']['enum'][0] for v in bet_schema],['check','raise','fold'])
        self.assertEqual(bet_schema[1]['properties']['raise_size']['enum'],['maximum','large','medium','small'])

    async def test_repeated_maximum_raises_finish_without_loans(self):
        table=Table(['A','B','C'],config=self.config)
        class Raising(ScriptedPokerLLM):
            async def decide(self,observation,research,force_bet=False,feedback=''):
                result=await super().decide(observation,research,force_bet,feedback)
                if 'raise' in observation.allowed_bets:
                    return result.model_copy(update={'bet':Bet(action='raise',raise_size='maximum')})
                return result
        graphs={pid:build_graph(Raising(table,None,pid,None)) for pid in table.players}
        runner=RoundRunner(table,graphs)
        await asyncio.wait_for(runner.run_round(),5)
        self.assertEqual(table.round.phase,'revealed')
        self.assertEqual(runner.errors,{})
        raises=[e for e in table.events if e['type']=='BetPlaced' and e['action']=='raise']
        self.assertEqual(len(raises),1)
        self.assertEqual(raises[0]['amount'],28)
        self.assertEqual(raises[0]['raise_size'],'maximum')
        self.assertEqual(sum(p.chips for p in table.players.values())+table.carry_pot,90)

    def test_reused_transport_keeps_defaults_and_can_bound_thinking(self):
        from agent.ollama_client import OllamaLLM
        old=OllamaLLM('test',0)._payload('s','u',stream=False)
        self.assertNotIn('think',old)
        self.assertNotIn('num_predict',old['options'])
        bounded=OllamaLLM('test',0,think=False,num_predict=512)._payload('s','u',stream=False)
        self.assertFalse(bounded['think'])
        self.assertEqual(bounded['options']['num_predict'],512)

    async def test_graph_failure_marks_player_and_keeps_round_running(self):
        table=Table(['A','B','C'],config=self.config)
        class Broken(ScriptedPokerLLM):
            async def decide(self,*args,**kwargs):
                raise RuntimeError('model offline')
        graphs={pid:build_graph((Broken if pid=='A' else ScriptedPokerLLM)(table,None,pid,None))
                for pid in table.players}
        runner=RoundRunner(table,graphs)
        await asyncio.wait_for(runner.run_round(),5)
        self.assertTrue(table.players['A'].failed)
        self.assertIn('model offline',runner.errors['A'])
        self.assertEqual(table.round.phase,'revealed')
        self.assertEqual(len([event for event in table.events if event['type']=='AgentFailed']),1)

    async def test_runaway_betting_stops_at_debug_budget_without_faking_a_result(self):
        table=Table(['A','B'],config=replace(self.config,runner_max_decisions=20))
        class Raising(ScriptedPokerLLM):
            async def decide(self,observation,research,force_bet=False,feedback=''):
                result=await super().decide(observation,research,force_bet,feedback)
                return result.model_copy(update={'bet':Bet(action='raise',raise_size='small')}) if 'raise' in observation.allowed_bets else result
        graphs={pid:build_graph(Raising(table,None,pid,None)) for pid in table.players}
        runner=RoundRunner(table,graphs)
        with self.assertRaisesRegex(RuntimeError,'runner_max_decisions'):
            await runner.run_round()
        self.assertEqual(table.round.phase,'dialogue')
        self.assertIsNone(table.round.result)
        self.assertFalse(runner._tasks)


if __name__=='__main__':
    unittest.main()
