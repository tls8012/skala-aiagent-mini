"""게임 규칙·그래프 계약·MCP 저장의 회귀 검사. 외부 모델 불필요."""
import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import random
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from pydantic import ValidationError
from agents import GameConfig, Table
from agents.betting import Betting
from agents.cards import Card, Deck, find_winner
from agents.mcp_server import GameTools
from agents.models import AgentDecision, Bet, Player, PlayerCommand


class PokerFixture:
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = GameConfig(data_dir=Path(self.temp.name), target_chips=999)

    def table(self, human=False, **config):
        return Table(['A', 'B', 'C'], 'human' if human else None,
                     replace(self.config, **config))

    def command(self, t, pid, kind, **kwargs):
        return t.submit(pid, PlayerCommand(request_id=uuid4().hex,
            round_id=t.round.number, basis_event=t.event_id, kind=kind, **kwargs))

    def speak_all(self, t):
        for pid in t.round.order:
            if not t.players[pid].failed:
                self.assertEqual(self.command(t, pid, 'speak', text='한번 보자.').status, 'accepted')

    def finish_checks(self, t):
        while t.round.phase == 'dialogue':
            result = self.command(t, t.round.betting.turn, 'bet', bet=Bet(action='check'))
            self.assertEqual(result.status, 'accepted', result)

class PokerTest(PokerFixture, unittest.TestCase):
    def test_deck_and_cyclic_strength(self):
        self.assertEqual(len(set(Deck(random.Random(1)).deal(52))), 52)
        self.assertTrue(Card(2, 'C').beats(Card(14, 'S')))
        self.assertTrue(Card(14, 'C').beats(Card(13, 'S')))
        self.assertTrue(Card(13, 'C').beats(Card(2, 'S')))
        self.assertTrue(Card(7, 'S').beats(Card(7, 'D')))
        self.assertTrue(Card(7, 'D').beats(Card(7, 'H')))
        self.assertTrue(Card(7, 'H').beats(Card(7, 'C')))
        self.assertIsNone(find_winner({'A': Card(2,'C'), 'B': Card(14,'H'), 'C': Card(13,'S')}))
        self.assertEqual(find_winner({'A': Card(2,'C'), 'B': Card(14,'H')}), 'A')

    def test_minimum_speech_opens_betting(self):
        t = self.table()
        t.start_round()
        self.assertFalse(t.observe('A').betting_open)
        self.assertEqual(self.command(t, 'A', 'bet', bet=Bet(action='check')).status, 'rejected')
        self.assertEqual(self.command(t, 'A', 'silent').status, 'rejected')
        self.speak_all(t)
        self.assertTrue(t.observe('A').betting_open)
        self.finish_checks(t)
        self.assertEqual(t.round.phase, 'revealed')

    def test_raise_reopens_prior_players_and_duplicate_is_idempotent(self):
        t = self.table()
        t.start_round()
        self.speak_all(t)
        self.command(t, 'A', 'bet', bet=Bet(action='check'))
        self.command(t, 'B', 'bet', bet=Bet(action='check'))
        cmd = PlayerCommand(request_id='raise-once', round_id=1, basis_event=t.event_id,
                            kind='bet', bet=Bet(action='raise', raise_size='large'))
        first = t.submit('C', cmd)
        pot = t.round.pot
        self.assertEqual(t.submit('C', cmd), first)
        self.assertEqual(t.round.pot, pot)
        self.assertEqual(t.round.betting.turn, 'A')
        self.command(t, 'A', 'bet', bet=Bet(action='call'))
        self.command(t, 'B', 'bet', bet=Bet(action='call'))
        self.assertEqual(t.round.result['pot'], 18)

    def test_out_of_turn_stale_and_invalid_check_do_not_mutate(self):
        t = self.table()
        t.start_round()
        self.speak_all(t)
        self.assertEqual(self.command(t,'B','bet',bet=Bet(action='raise',raise_size='medium')).status,'rejected')
        old = t.event_id
        self.command(t,'A','bet',bet=Bet(action='raise',raise_size='medium'))
        stale = t.submit('B', PlayerCommand(request_id='old',round_id=1,basis_event=old,
                            kind='bet',bet=Bet(action='call')))
        self.assertTrue(stale.rejudge)
        pot = t.round.pot
        self.assertEqual(self.command(t,'B','bet',bet=Bet(action='check')).status,'rejected')
        self.assertEqual(t.round.pot,pot)

    def test_draw_carries_pot_rotation_and_history(self):
        t = self.table()
        t.start_round({'A':Card(2,'C'),'B':Card(14,'H'),'C':Card(13,'S')})
        self.speak_all(t)
        self.finish_checks(t)
        self.assertEqual(t.carry_pot,6)
        archive = json.loads(next(t.archive_dir.glob('*.json')).read_text())
        self.assertEqual(archive['result']['winner'],None)
        self.assertEqual(archive['events'][-1]['type'],'RoundEnded')
        t.start_round({'A':Card(3,'C'),'B':Card(12,'H'),'C':Card(8,'S')})
        self.assertEqual(t.round.order,['B','C','A'])
        self.assertEqual(t.round.pot,12)
        self.assertEqual(t.observe('A').messages,[])
        self.speak_all(t)
        self.finish_checks(t)
        self.assertEqual(t.players['B'].chips,38)
        self.assertEqual(t.carry_pot,0)

    def test_no_loans_elimination_and_config_target(self):
        t = self.table(initial_chips=2, target_chips=5)
        t.start_round({'A':Card(13,'S'),'B':Card(8,'H'),'C':Card(3,'C')})
        self.speak_all(t)
        self.assertEqual(t.observe('A').raise_options, {})
        self.assertNotIn('raise', t.observe('A').allowed_bets)
        before = (t.event_id, t.round.pot)
        self.assertEqual(self.command(t,'A','bet',bet=Bet(action='raise',raise_size='small')).status,'rejected')
        self.assertEqual((t.event_id,t.round.pot), before)
        self.finish_checks(t)
        self.assertEqual(t.players['A'].chips,6)
        self.assertTrue(t.players['B'].eliminated)
        self.assertEqual(t.game_winner,'A')
        self.assertTrue(t.finished)
        self.assertEqual(sum(p.chips for p in t.players.values())+t.carry_pot,6)

    def test_raise_cap_reserves_outstanding_calls_and_rejects_sizes_without_mutation(self):
        players = {pid:Player(pid,'ai',chips) for pid,chips in [('A',20),('B',8),('C',12)]}
        betting = Betting(list(players),players,ante=2,min_raise=1)
        self.assertEqual(betting.raise_options('A'), {'maximum':6,'large':4,'medium':2,'small':1})
        self.assertEqual(betting.act('A',Bet(action='raise',raise_size='large')),4)
        # B has 6 chips, but owes 4. Only 2 remain for a further raise.
        self.assertEqual(betting.raise_options('B'), {'maximum':2,'medium':2,'small':1})
        before = (betting.current_bet, betting.pot, dict(betting.contributions),
                  betting.turn, set(betting.pending), [p.chips for p in players.values()])
        with self.assertRaises(ValueError):
            betting.act('B',Bet(action='raise',raise_size='large'))
        self.assertEqual(before, (betting.current_bet, betting.pot, dict(betting.contributions),
                  betting.turn, set(betting.pending), [p.chips for p in players.values()]))
        self.assertEqual(betting.act('B',Bet(action='raise',raise_size='maximum')),2)
        self.assertEqual(players['B'].chips,0)
        self.assertEqual(betting.allowed('C'), ['call','fold'])
        betting.act('C',Bet(action='call'))
        betting.act('A',Bet(action='call'))
        self.assertTrue(betting.complete)
        self.assertEqual(sum(p.chips for p in players.values())+betting.pot,40)
        self.assertTrue(all(p.chips>=0 for p in players.values()))

    def test_folding_poorest_player_increases_cap(self):
        players = {pid:Player(pid,'ai',chips) for pid,chips in [('A',4),('B',20),('C',12)]}
        betting = Betting(list(players),players,ante=2,min_raise=1)
        self.assertEqual(betting.raise_options('A')['maximum'],2)
        betting.act('A',Bet(action='fold'))
        self.assertEqual(betting.raise_options('B')['maximum'],10)

    def test_insufficient_next_ante_eliminates_without_removing_remaining_chip(self):
        t=self.table(initial_chips=4)
        t.start_round({'A':Card(13,'S'),'B':Card(8,'H'),'C':Card(3,'C')})
        self.speak_all(t)
        self.command(t,'A','bet',bet=Bet(action='raise',raise_size='small'))
        for pid in ['B','C']:
            self.command(t,pid,'bet',bet=Bet(action='call'))
        self.assertEqual(t.players['B'].chips,1)
        self.assertTrue(t.players['B'].eliminated)
        self.assertEqual(sum(p.chips for p in t.players.values()),12)
        players={'A':Player('A','ai',3),'B':Player('B','ai',1)}
        with self.assertRaises(ValueError):
            Betting(list(players),players,ante=2,min_raise=1)
        self.assertEqual(players['A'].chips,3)

    def test_folded_cards_and_self_card_never_leak(self):
        t = self.table()
        t.start_round({'A':Card(13,'S'),'B':Card(8,'H'),'C':Card(3,'C')})
        self.assertNotIn('A', t.observe('A').visible_cards)
        self.assertNotIn('cards', str(t.events_since(0)))
        self.speak_all(t)
        self.command(t,'A','bet',bet=Bet(action='fold'))
        self.command(t,'B','bet',bet=Bet(action='fold'))
        self.assertEqual(set(t.observe('C').visible_cards),{'C'})
        archive = json.loads(next(t.archive_dir.glob('*.json')).read_text())
        self.assertEqual(set(archive['result']['revealed_cards']),{'C'})
        self.assertEqual(self.command(t,'C','speak',text='끝?').status,'rejected')

    def test_folded_player_can_speak_defer_and_exhaust_quota_but_not_bet(self):
        t = self.table()
        t.start_round()
        self.speak_all(t)
        self.assertEqual(self.command(t, 'A', 'bet', bet=Bet(action='fold')).status, 'accepted')
        observation = t.observe('A')
        self.assertTrue(observation.can_speak)
        self.assertEqual(observation.folded_players, ['A'])
        self.assertEqual(observation.allowed_bets, [])
        self.assertEqual(observation.raise_options, {})
        self.assertNotIn('A', t.observe('B').visible_cards)
        before = (t.event_id, t.round.pot, t.players['A'].chips, t.round.betting.turn)
        for bet in [Bet(action='check'), Bet(action='call'), Bet(action='fold'),
                    Bet(action='raise', raise_size='small')]:
            self.assertEqual(self.command(t, 'A', 'bet', bet=bet).status, 'rejected')
        self.assertEqual(before, (t.event_id, t.round.pot, t.players['A'].chips, t.round.betting.turn))

        self.command(t, 'B', 'silent')
        self.command(t, 'C', 'silent')
        self.assertFalse(t.observe('B').dialogue_quiet)  # 폴드한 A도 대화 참가자다.
        self.assertEqual(self.command(t, 'A', 'silent').status, 'accepted')
        self.assertTrue(t.observe('B').dialogue_quiet)
        self.assertEqual(self.command(t, 'A', 'speak', text='폴드했어도 B는 수상해').status, 'accepted')
        self.assertFalse(t.observe('B').dialogue_quiet)
        self.assertEqual(self.command(t, 'A', 'speak', text='이 말은 보류').status, 'deferred')
        self.command(t, 'B', 'speak', text='왜 그렇게 생각해?')
        self.assertEqual(t.release_deferred('A', t.event_id).status, 'accepted')
        for _ in range(2):
            self.command(t, 'B', 'speak', text='다른 이유도 있어?')
            self.assertEqual(self.command(t, 'A', 'speak', text='너의 반응이 수상해').status, 'accepted')
        self.assertEqual(t.round.conversation.counts['A'], t.config.max_utterances)
        self.command(t, 'C', 'speak', text='그만 들을게')
        self.assertFalse(t.observe('A').can_speak)
        self.assertEqual(self.command(t, 'A', 'speak', text='한도 초과').status, 'rejected')
        self.finish_checks(t)
        self.assertFalse(t.observe('A').can_speak)
        self.assertEqual(self.command(t, 'A', 'speak', text='공개 후 발언').status, 'rejected')
        self.assertNotIn('A', t.round.result['revealed_cards'])

    def test_failed_agent_remains_unable_to_speak_after_folding(self):
        t = self.table()
        t.start_round()
        self.speak_all(t)
        self.command(t, 'A', 'bet', bet=Bet(action='fold'))
        t.fail_agent('A')
        self.assertFalse(t.observe('A').can_speak)
        self.assertEqual(self.command(t, 'A', 'speak', text='오류 난 모델').status, 'rejected')

    def test_deferred_speech_requires_explicit_revalidation(self):
        t=self.table()
        t.start_round()
        self.command(t,'A','speak',text='첫 발언')
        result=self.command(t,'A','speak',text='보류 발언')
        self.assertEqual(result.status,'deferred')
        self.assertEqual(len(t.round.conversation.messages),1)
        self.command(t,'B','speak',text='응답')
        self.assertEqual(len(t.round.conversation.messages),2)
        self.assertEqual(t.release_deferred('A',t.event_id-1).status,'rejected')
        self.assertEqual(t.release_deferred('A',t.event_id).status,'accepted')
        self.assertEqual(t.round.conversation.counts['A'],2)

    def test_chat_counts_human_only_and_requires_actual_question(self):
        t=self.table(human=True, max_utterances=2)
        t.start_round()
        self.speak_all(t)
        question=PlayerCommand(request_id='weather',round_id=1,basis_event=t.event_id,
                              kind='speak',channel='chat',target='B',text='날씨 어때?')
        # 마지막 발언자가 인간이므로 다른 참가자의 발언 후 질문.
        self.command(t,'A','speak',text='다음 행동을 보자')
        self.assertEqual(t.submit('human',question).status,'accepted')
        self.assertEqual(t.round.conversation.counts['human'],2)
        self.assertEqual(self.command(t,'B','speak',channel='chat',reply_to='weather',
                                     text='날씨 도구가 없어.').status,'accepted')
        self.assertEqual(t.round.conversation.counts['B'],1)
        self.assertEqual(self.command(t,'C','speak',channel='chat',text='나도 잡담').status,'rejected')
        self.assertEqual(self.command(t,'B','speak',channel='chat',reply_to='weather',text='또').status,'rejected')
        self.assertEqual(self.command(t,'human','speak',text='한도 초과').status,'rejected')
        self.assertEqual(self.command(t,'A','bet',bet=Bet(action='check')).status,'accepted')

    def test_silence_is_condition_not_round_end_and_new_game_speech_resets(self):
        t=self.table(human=True)
        t.start_round()
        self.speak_all(t)
        for pid in t.round.order:
            self.assertEqual(self.command(t,pid,'silent').status,'accepted')
        self.assertTrue(t.observe('A').dialogue_quiet)
        self.assertEqual(t.round.phase,'dialogue')
        self.command(t,'A','speak',text='잠깐만')
        self.assertFalse(t.observe('A').dialogue_quiet)

    def test_failure_automatic_check_or_fold_and_minimum_exemption(self):
        t=self.table()
        t.start_round()
        t.fail_agent('B')
        self.command(t,'A','speak',text='시작')
        self.command(t,'C','speak',text='응')
        self.command(t,'A','bet',bet=Bet(action='raise',raise_size='small'))
        self.assertIn('B',t.round.betting.folded)
        self.command(t,'C','bet',bet=Bet(action='call'))
        self.assertEqual(t.round.phase,'revealed')
        t2=self.table()
        t2.start_round()
        for pid in ['A','B','C']:
            t2.fail_agent(pid)
        self.assertEqual(t2.round.phase,'revealed')
        self.assertEqual(len([e for e in t2.events if e['type']=='AgentFailed']),3)

    def test_context_bounded_and_observation_is_copy(self):
        t=self.table(context_message_limit=2)
        t.start_round()
        self.speak_all(t)
        obs=t.observe('A')
        self.assertEqual(len(obs.messages),2)
        self.assertEqual(len(t.round.conversation.messages),3)
        self.assertEqual(obs.pot,6)
        obs.visible_cards['B']['rank']=100
        self.assertNotEqual(t.round.cards['B'].rank,100)
        self.finish_checks(t)
        obs=t.observe('A')
        obs.result['revealed_cards'].clear()
        self.assertTrue(t.round.result['revealed_cards'])

    def test_contract_and_graph_decision_connection(self):
        with self.assertRaises(ValidationError):
            Bet(action='raise')
        with self.assertRaises(ValidationError):
            Bet(action='call',raise_size='medium')
        with self.assertRaises(ValidationError):
            AgentDecision(round_id=1,basis_event=0,speech='silent',text='말')
        t=self.table()
        t.start_round()
        self.speak_all(t)
        decision=AgentDecision(round_id=1,basis_event=t.event_id,speech='speak',
                               text='체크하겠어',bet=Bet(action='check'))
        results=t.apply_decision('A',decision,'decision-1')
        self.assertTrue(all(r.status=='accepted' for r in results))
        before=t.event_id
        self.assertEqual(t.apply_decision('A',decision,'decision-1'),results)
        self.assertEqual(t.event_id,before)

    def test_game_ids_and_reserving_human_minimum(self):
        with self.assertRaises(ValueError):
            Table(['../A','B'])
        t=self.table(human=True,max_utterances=1)
        t.start_round()
        self.assertEqual(self.command(t,'human','speak',channel='chat',target='A',text='안녕').status,'rejected')
        self.assertEqual(self.command(t,'human','speak',text='게임 발언').status,'accepted')


class MemoryTests(PokerFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.tools=GameTools(self.config)
        self.addCleanup(self.tools.close)

    def test_personal_text_files_and_lifetimes(self):
        tools=self.tools
        tools.write_memo('game','A',1,'round','이번 라운드')
        tools.write_memo('game','A',1,'game','이번 게임')
        tools.write_memo('game','A',1,'long','B의 블러핑')
        self.assertEqual(tools.read_memo('game','B',1,'long')['text'],'')
        self.assertEqual(tools.read_memo('game','A',2,'round')['text'],'')
        self.assertEqual(tools.read_memo('other','A',1,'game')['text'],'')
        self.assertEqual(tools.read_memo('other','A',1,'long')['text'],'B의 블러핑')
        tools.write_memo('game','A',1,'game','추가',append=True)
        self.assertEqual(tools.read_memo('game','A',1,'game')['text'],'이번 게임\n추가')
        with self.assertRaises(ValueError):
            tools.read_memo('game','../B',1,'long')

    def test_seed_fifo_idempotency_and_closed_round_validation(self):
        with patch.object(self.tools,'_queue',return_value=True):
            self.assertEqual(self.tools.memory_status('game','A',1)['records'],20)
            t=Table(['A','B','C'],config=self.config,game_id='game')
            t.start_round()
            self.speak_all(t)
            self.finish_checks(t)
            record=json.loads(next(t.archive_dir.glob('*.json')).read_text())
            self.tools.store_experience(record)
            self.tools.store_experience(record)
            rows=self.tools._load('game')
            self.assertEqual(len(rows),20)
            self.assertEqual(rows[0]['id'],'seed-02')
            self.assertEqual(rows[-1]['source'],'played')
            bad=dict(record,cards={'A':{'rank':14,'suit':'S'}})
            with self.assertRaises(ValueError):
                self.tools.store_experience(bad)
            bad=json.loads(json.dumps(record))
            bad['events'].pop()
            with self.assertRaises(ValueError):
                self.tools.store_experience(bad)

    def test_vector_index_skips_existing_and_evicts(self):
        class Collection:
            def __init__(self):
                self.rows={}
            def get(self,include):
                return {'ids':list(self.rows), 'metadatas':[row[2] for row in self.rows.values()]}
            def delete(self,ids):
                for key in ids:
                    del self.rows[key]
            def upsert(self,ids,documents,embeddings,metadatas):
                for key,doc,vector,meta in zip(ids,documents,embeddings,metadatas):
                    self.rows[key]=(doc,vector,meta)
            def query(self,query_embeddings,n_results,where,include):
                keys=[key for key in self.rows if self.rows[key][2]['experience_id'] in where['experience_id']['$in']][:n_results]
                return {'ids':[keys], 'documents':[[self.rows[k][0] for k in keys]],
                        'metadatas':[[self.rows[k][2] for k in keys]],'distances':[[0.1]*len(keys)]}
        collection=Collection()
        rows=self.tools._load('game')
        with patch.object(self.tools,'_collection',return_value=collection), \
             patch.object(self.tools,'_embed',side_effect=lambda texts:[[1.0,0.0] for _ in texts]) as embed, \
             patch.object(self.tools,'_queue',return_value=True):
            self.tools.index_records('game',rows)
            initial_calls=embed.call_count
            self.assertGreater(initial_calls,0)
            self.tools.index_records('game',rows)
            self.assertEqual(embed.call_count,initial_calls)
            self.tools.index_records('game',rows[1:])
            self.assertFalse(any(row[2]['experience_id']=='seed-01' for row in collection.rows.values()))
            result=self.tools.search_experiences('game','A',1,'블러핑',2)
            self.assertEqual(result['status'],'ready')
            self.assertEqual(len(result['matches']),2)
            self.assertEqual(result['matches'][0]['metadata']['source'],'seed')

    def test_unavailable_index_does_not_break_memos(self):
        with patch.object(self.tools,'_collection',side_effect=RuntimeError('offline')), \
             patch.object(self.tools,'_queue',return_value=True):
            self.assertEqual(self.tools.search_experiences('game','A',1,'상대')['status'],'unavailable')
            self.assertTrue(self.tools.write_memo('game','A',1,'round','정상')['written'])

    def test_twenty_seed_records_are_completed_and_fold_cards_are_private(self):
        rows=self.tools._load('game')
        self.assertEqual(len({row['id'] for row in rows}),20)
        for row in rows:
            self.assertEqual(row['events'][-1]['type'],'RoundEnded')
            order=row['events'][0]['order']
            betting=Betting(order,{pid:Player(pid,'ai',30) for pid in order},ante=2,min_raise=1)
            for event in row['events']:
                if event['type']=='BetPlaced':
                    increment=betting.act(event['player_id'],Bet(action=event['action'],
                                                               raise_size=event.get('raise_size')))
                    self.assertEqual(increment,event['amount'])
                    self.assertEqual(betting.pot,event['pot'])
                    self.assertEqual(betting.contributions,event['contributions'])
            self.assertTrue(betting.complete)
            folded={event['player_id'] for event in row['events']
                    if event['type']=='BetPlaced' and event['action']=='fold'}
            self.assertFalse(folded & row['result']['revealed_cards'].keys())


class MCPIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_client_stdio_and_bound_identity(self):
        from agent.mcp_client import MCPClient
        with TemporaryDirectory() as folder:
            with patch.dict(os.environ,{'POKER_DATA_DIR':folder,
                       'LANGSMITH_TRACING':'false','LANGCHAIN_TRACING_V2':'false'}):
                client=MCPClient(flaky_first_tool_call=False,server_scripts={
                    'poker':Path(__file__).resolve().parents[1]/'agents'/'mcp_server.py'})
                await client.start()
                try:
                    table=Table(['A','B'],config=GameConfig(data_dir=Path(folder)))
                    table.start_round()
                    saved=await table.call_player_tool(client,'A','write_memo',
                                                       {'scope':'round','text':'내 개인 판단'})
                    self.assertTrue(saved['written'])
                    other=await table.call_player_tool(client,'B','read_memo',{'scope':'round'})
                    self.assertEqual(other['text'],'')
                    mine=await table.call_player_tool(client,'A','read_memo',{'scope':'round'})
                    self.assertEqual(mine['text'],'내 개인 판단')
                    bundle=await table.call_player_tool(client,'A','read_memories')
                    self.assertEqual(bundle,{'round':'내 개인 판단','game':'','long':''})
                    for pid in ['A','B']:
                        table.submit(pid,PlayerCommand(request_id=pid+'-speech',round_id=1,
                            basis_event=table.event_id,kind='speak',text='공개까지 가보자'))
                    for pid in ['A','B']:
                        table.submit(pid,PlayerCommand(request_id=pid+'-check',round_id=1,
                            basis_event=table.event_id,kind='bet',bet=Bet(action='check')))
                    published=await table.publish_memories(client)
                    self.assertTrue(published[0]['stored'])
                    self.assertEqual(await table.publish_memories(client),[])
                    status=await table.call_player_tool(client,'A','memory_status')
                    self.assertEqual(status['records'],20)
                    with self.assertRaises(ValueError):
                        await table.call_player_tool(client,'A','read_memo',{'scope':'round','agent_id':'B'})
                finally:
                    await client.stop()


if __name__=='__main__':
    unittest.main()
