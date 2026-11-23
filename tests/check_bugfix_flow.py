"""Offline queue/score checks against copied function definitions.

Never imports or starts server.py: it loads selected function definitions through
the AST and supplies fake state, songs, persistence, clock and automation.
"""
import ast, os
import copy
import json
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
import types
import unicodedata
import unittest
import uuid

ROOT = Path(tempfile.gettempdir())  # scratch folder for the offline fixtures
SOURCE = Path(sys.argv.pop(1)) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / 'stage-manager/server.py'
TREE = ast.parse(SOURCE.read_text(), str(SOURCE))
NAMES = {
    'fresh_state', 'mod_driver', 'taken_song_ids', 'clean_name', 'make_entry',
    'signup', 'call_next', 'go', 'skip_next', 'back_to_queue', 'finalize_current',
    'admin_action', 'leaderboard', 'public_view', 'press', 'maybe_encore', 'rotate_encore',
    'song_difficulties', 'chosen_difficulty', 'chart_difficulties',
    'inappropriate_name', 'public_name',
    'selected_index', 'remove_current', 'armed_for', 'arm_remove', 'disarm_remove', 'remove_hint',
    'mod_result', 'same_folder', 'refresh_turn_score', 'finish_turn_now', 'gameplay_bg_info', 'health', 'overlay_mode', 'results_card', 'gameplay_bg_key',
}
FILTER_CONSTANTS = {'_NAME_TERMS', '_NAME_PATTERN', '_NAME_LEET', 'REMOVE_CONFIRM_S'}
FUNCTIONS = ast.Module(body=[n for n in TREE.body
                            if (isinstance(n, ast.FunctionDef) and n.name in NAMES) or
                            (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in FILTER_CONSTANTS
                                                             for t in n.targets))], type_ignores=[])
DEFAULT = ast.literal_eval(next(n.value for n in TREE.body if isinstance(n, ast.Assign)
                               and any(isinstance(t, ast.Name) and t.id == 'DEFAULT_CONFIG' for t in n.targets)))

def forbid_external(event, args):
    if event.startswith(('socket.', 'subprocess.', 'ctypes.')) or event in ('os.system', 'os.fork', 'os.posix_spawn'):
        raise AssertionError('Blocked external operation: ' + event)

sys.addaudithook(forbid_external)

class FakeThread:
    def __init__(self, *args, **kwargs):
        self.target = kwargs.get('target')
    def start(self):
        # Automation is deliberately never executed.
        pass

class Logic(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='offline-fixture-', dir=ROOT)
        self.addCleanup(self.tmp.cleanup)
        self.saved = []
        cfg = copy.deepcopy(DEFAULT)
        cfg.update(driver='mod', max_queue=40)
        runtime = dict(v=0, ready_v=0, tunnel_url=None, lan_url=None, skip_armed_at=0, mod_cmds=[], scene="Main Menu", mod_info={"home": "1"}, published_v=9999, skip_armed_id=None, sel_id=None)
        ns = {'DIFFICULTIES': ('easy', 'medium', 'hard', 'expert'), 're': re, 'os': os, 'json': json, 'unicodedata': unicodedata, 'time': time, 'uuid': uuid, 'LOCK': threading.RLock(),
              'CONFIG': cfg, 'RUNTIME': runtime, 'DATA': Path(self.tmp.name),
              'threading': types.SimpleNamespace(Thread=FakeThread),
              'read_scoredata': lambda: {}, 'activate_display': lambda: None,
              'game_running': lambda: False, 'LIGHTS': types.SimpleNamespace(called_up=lambda: None, song_over=lambda *a: None, status=lambda: 'off'),
              'save_json': lambda path, data: self.saved.append(copy.deepcopy(data)),
              'print': lambda *args: None,
              'ENCORE_ID': 'encore', 'SONGS': {sid: {'id': sid, 'name': title, 'artist': 'Band',
                  'folder': '/offline-fixture/' + sid, 'art': None}
                  for sid, title in [('song1', 'Song One'), ('song2', 'Song Two'), ('encore', 'Jump')]}}
        exec(compile(FUNCTIONS, str(SOURCE), 'exec'), ns)
        ns['STATE'] = ns['fresh_state']()
        def save_state():
            if "maybe_encore" in ns: ns["maybe_encore"]()
            self.saved.append(copy.deepcopy(ns['STATE']))
            ns['RUNTIME']['v'] += 1
        ns['save_state'] = save_state
        self.ns = ns


    def add(self, *names):
        for k, n in enumerate(names):
            self.ns['STATE']['queue'].append(self.ns['make_entry'](n, 'song%d' % (k % 2 + 1)))
        return [e['id'] for e in self.ns['STATE']['queue']]

    def press(self, b):
        self.ns['RUNTIME']['last_press'] = 0  # (outside the double-press debounce)
        return self.ns['press'](b)

    def lobby(self):
        self.ns['RUNTIME']['mod_info']['home'] = '0'

    def test_browse_moves_highlight_and_green_calls_highlighted(self):
        self.lobby(); ids = self.add('Ann', 'Bob', 'Cat')
        self.assertEqual(self.ns['public_view']()['selected'], 0)
        self.assertEqual(self.ns['press']('up')['action'], 'none')           # top of the line
        self.assertEqual(self.ns['press']('down')['action'], 'moved')
        self.assertEqual(self.ns['press']('down')['action'], 'moved')         # no debounce between strums
        self.assertEqual(self.ns['press']('down')['action'], 'none')         # end of the line
        self.assertEqual(self.ns['public_view']()['selected'], 2)
        self.assertEqual(self.press('green')['action'], 'called')
        self.assertEqual(self.ns['STATE']['current']['name'], 'Cat')
        self.assertEqual([e['name'] for e in self.ns['STATE']['queue']], ['Ann', 'Bob'])  # the others keep their places
        self.assertIsNone(self.ns['RUNTIME']['sel_id'])

    def test_highlight_follows_person_when_line_changes(self):
        self.lobby(); ids = self.add('Ann', 'Bob', 'Cat')
        self.ns['press']('down'); self.ns['press']('down')                   # Cat
        self.ns['admin_action']({'action': 'remove', 'id': ids[0]})          # Ann removed from the phone
        self.assertEqual(self.ns['public_view']()['selected'], 1)            # still Cat
        self.ns['admin_action']({'action': 'remove', 'id': ids[2]})          # Cat removed: back to the front
        self.assertEqual(self.ns['public_view']()['selected'], 0)

    def test_red_twice_removes_highlighted_and_reopens_song(self):
        self.lobby(); ids = self.add('Ann', 'Bob')
        self.ns['press']('down')
        self.assertEqual(self.press('red')['action'], 'skip_armed')
        self.assertEqual(self.ns['public_view']()['hint'], 'Remove Bob? Press [red] again')
        self.assertEqual(self.press('red')['action'], 'skipped')
        self.assertEqual([e['name'] for e in self.ns['STATE']['queue']], ['Ann'])
        self.assertNotIn('song2', self.ns['taken_song_ids']())               # Bob's song is up for grabs again
        self.assertEqual(self.ns['public_view']()['hint'], '')

    def test_red_confirm_is_tied_to_the_person(self):
        self.lobby(); self.add('Ann', 'Bob')
        self.assertEqual(self.press('red')['action'], 'skip_armed')          # armed on Ann
        self.ns['press']('down')                                             # moved to Bob: warning cleared
        self.assertEqual(self.ns['public_view']()['hint'], '')
        self.assertEqual(self.press('red')['action'], 'skip_armed')          # Bob is warned first, not removed
        self.assertEqual(len(self.ns['STATE']['queue']), 2)

    def test_red_confirm_expires(self):
        self.lobby(); self.add('Ann')
        self.press('red')
        self.ns['RUNTIME']['skip_armed_at'] -= 9
        self.assertEqual(self.press('red')['action'], 'skip_armed')
        self.assertEqual(len(self.ns['STATE']['queue']), 1)

    def test_start_screen_red_twice_removes_player(self):
        self.lobby(); self.add('Ann', 'Bob')
        self.press('green')
        self.assertEqual(self.ns['STATE']['current']['phase'], 'ready')
        self.assertEqual(self.press('red')['action'], 'remove_armed')
        self.assertEqual(self.ns['STATE']['current']['name'], 'Ann')         # one red does nothing yet
        self.assertEqual(self.ns['public_view']()['hint'], 'Remove Ann? Press [red] again')
        self.assertEqual(self.press('red')['action'], 'removed')
        self.assertIsNone(self.ns['STATE']['current'])
        self.assertNotIn('song1', self.ns['taken_song_ids']())
        self.assertEqual([e['name'] for e in self.ns['STATE']['skipped']], ['Ann'])
        self.assertEqual(self.ns['public_view']()['queue'][0]['name'], 'Bob')

    def test_start_screen_green_after_red_starts_and_clears_warning(self):
        self.lobby(); self.add('Ann')
        self.press('green'); self.press('red')
        self.assertEqual(self.press('green')['action'], 'start')
        self.assertEqual(self.ns['STATE']['current']['phase'], 'playing')
        self.assertEqual(self.ns['public_view']()['hint'], '')

    def test_browse_ignored_on_home_screen_and_during_turn(self):
        self.add('Ann', 'Bob')
        self.assertEqual(self.ns['press']('down')['action'], 'none')         # home screen (blue = reset there)
        self.lobby(); self.press('green')
        self.assertEqual(self.ns['press']('down')['action'], 'none')         # start screen

    def test_encore_red_rotates_highlighted_encore(self):
        self.lobby()
        self.add('Ann'); self.press('green'); self.press('green')
        self.ns['admin_action']({'action': 'finish', 'score': '500'})
        self.add('Bob'); self.press('green'); self.press('green')
        self.ns['admin_action']({'action': 'finish', 'score': '900'})
        self.assertEqual(self.ns['admin_action']({'action': 'encore'})['name'], 'Bob')
        self.assertEqual(self.press('red')['action'], 'encore_next')
        self.assertEqual(self.ns['STATE']['queue'][0]['name'], 'Ann')

    def test_native_score_wins_for_the_turn_on_stage(self):
        self.lobby(); self.add('Ann'); self.press('green'); self.press('green')
        cur = self.ns['STATE']['current']
        self.assertEqual(self.ns['mod_result']({'score': 1234, 'folder': '/elsewhere', 'stars': 3})['reason'], 'different song')
        r = self.ns['mod_result']({'score': 51234, 'folder': '/offline-fixture/song1/', 'stars': 4, 'hit': 90, 'total': 100})
        self.assertTrue(r['ok'])
        self.assertEqual(cur['phase'], 'finished')
        # a score-file read afterwards (no new best written) must not wipe it
        self.ns['refresh_turn_score'](cur, {'song1': {'plays': 5, 'best': {'score': 99999}}})
        self.assertEqual(cur['result']['score'], 51234)
        self.ns['read_scoredata'] = lambda: {'song1': {'plays': 5, 'best': {'score': 99999}}}
        self.ns['finish_turn_now'](cur)
        done = self.ns['STATE']['done'][-1]
        self.assertEqual((done['score'], done['stars'], done['percent']), (51234, 4, 90))
        self.assertEqual(self.ns['public_view']()['leaderboard'][0]['score'], 51234)

    def test_native_score_fills_a_turn_finished_moments_ago(self):
        self.lobby(); self.add('Ann'); self.press('green'); self.press('green')
        self.ns['finish_turn_now'](self.ns['STATE']['current'])               # song_ended got there first: no score
        self.assertIsNone(self.ns['STATE']['done'][-1]['score'])
        self.assertTrue(self.ns['mod_result']({'score': 777, 'folder': '/offline-fixture/song1'})['ok'])
        self.assertEqual(self.ns['STATE']['done'][-1]['score'], 777)
        self.assertFalse(self.ns['mod_result']({'score': 778, 'folder': '/offline-fixture/song1'})['ok'])  # once only

    def test_native_score_never_overrides_phone_correction(self):
        self.lobby(); self.add('Ann'); self.press('green'); self.press('green')
        self.ns['admin_action']({'action': 'finish', 'score': '4000'})
        self.assertFalse(self.ns['mod_result']({'score': 777, 'folder': '/offline-fixture/song1'})['ok'])
        self.assertEqual(self.ns['STATE']['done'][-1]['score'], 4000)

    def test_native_score_without_a_show_turn_is_ignored(self):
        self.assertFalse(self.ns['mod_result']({'score': 777, 'folder': '/offline-fixture/song1'})['ok'])
        self.assertFalse(self.ns['mod_result']({'folder': '/offline-fixture/song1'})['ok'])

    def test_in_song_panel_shows_leader_and_next_with_public_names(self):
        self.lobby()
        self.assertEqual(self.ns['gameplay_bg_info'](), {'top': None, 'next': None})
        self.add('Ann', 'shithead'); self.press('green'); self.press('green')
        self.ns['admin_action']({'action': 'finish', 'score': '9000'})
        info = self.ns['gameplay_bg_info']()
        self.assertEqual(info['top'], {'name': 'Ann', 'score': 9000})
        self.assertEqual(info['next'], 'Rock Star')                          # filtered names stay filtered on screen
        self.ns['STATE']['queue'][0]['encore'] = True
        self.assertEqual(self.ns['gameplay_bg_info']()['next'], "Tonight's #1 (encore)")

    def test_health_summary(self):
        rt = self.ns['RUNTIME']
        rt.update(mod_seen=time.time(), mod_info={'joined': '1', 'pads': '1', 'padname': 'Revival Kit v1'},
                  tunnel_url=None, lan_url='http://10.0.0.2:8765', bg_stamped=True, bg_url='http://10.0.0.2:8765')
        h = self.ns['health']()
        self.assertEqual((h['game'], h['guitar']['count'], h['guitar']['joined'], h['link'], h['art'], h['lights']),
                         (True, 1, True, 'wifi', True, 'off'))
        rt['tunnel_url'] = 'https://x.trycloudflare.com'                     # the public link arrived: art is stale
        h = self.ns['health']()
        self.assertEqual((h['link'], h['art']), ('public', False))
        rt.update(mod_seen=0, mod_info={})
        self.assertEqual((self.ns['health']()['game'], self.ns['health']()['guitar']['count']), (False, -1))

    def end_song(self):
        cur = self.ns['STATE']['current']
        self.ns['RUNTIME'].update(scene='EndOfSong', results_hold_until=time.time() + 15, results_for=cur['id'])
        return cur

    def test_results_card_after_a_show_song(self):
        self.lobby(); self.add('Ann', 'Bob'); self.press('green'); self.press('green')
        cur = self.end_song()
        self.assertEqual(self.ns['overlay_mode'](), 'results')
        r = self.ns['public_view']()['results']
        self.assertEqual((r['name'], r['score'], r['next']), ('Ann', None, 'Bob'))   # score still being tallied
        self.ns['mod_result']({'score': 5000, 'stars': 4, 'folder': '/offline-fixture/song1'})
        r = self.ns['public_view']()['results']
        self.assertEqual((r['score'], r['stars'], r['rank'], r['of']), (5000, 4, None, 0))  # (not finalized yet)
        self.ns['finish_turn_now'](cur)
        r = self.ns['public_view']()['results']
        self.assertEqual((r['score'], r['stars'], r['rank'], r['of']), (5000, 4, 1, 1))
        self.ns['RUNTIME']['results_hold_until'] = time.time() - 1                  # 15 s later: the lobby
        self.assertEqual(self.ns['overlay_mode'](), 'lobby')
        self.assertIsNone(self.ns['public_view']()['results'])

    def test_results_card_buttons(self):
        self.lobby(); self.add('Ann', 'Bob'); self.press('green'); self.press('green')
        self.end_song()
        self.assertEqual(self.press('red')['action'], 'none')                     # nothing to remove on the card
        self.assertEqual(self.ns['press']('down')['action'], 'none')
        self.assertEqual(self.press('green')['action'], 'called')                 # done: call the next player
        self.assertEqual(self.ns['STATE']['current']['name'], 'Bob')
        self.assertEqual(self.ns['STATE']['done'][-1]['name'], 'Ann')
        self.assertNotEqual(self.ns['overlay_mode'](), 'results')

    def test_no_results_card_when_disabled_or_paused(self):
        self.lobby(); self.add('Ann'); self.press('green'); self.press('green'); self.end_song()
        self.ns['CONFIG']['results_card'] = False
        self.assertEqual(self.ns['overlay_mode'](), 'none')                       # Clone Hero's own results, as before
        self.ns['CONFIG']['results_card'] = True
        self.ns['RUNTIME']['overlay_paused'] = True
        self.assertEqual(self.ns['overlay_mode'](), 'none')

    def test_lefty_signup_reaches_the_game(self):
        self.lobby()
        r = self.ns['signup']({'name': 'Off', 'songId': 'song2', 'device': 'p0', 'lefty': True})
        self.assertNotIn('lefty', self.ns['STATE']['queue'][0])                 # option off: the box is ignored
        self.ns['STATE']['queue'].clear()
        self.ns['CONFIG']['lefty_signup'] = True
        r = self.ns['signup']({'name': 'Lou', 'songId': 'song1', 'device': 'p1', 'lefty': True})
        self.ns['signup']({'name': 'Rae', 'songId': 'song2', 'device': 'p2', 'lefty': 'yes'})  # only a real true counts
        q = self.ns['STATE']['queue']
        self.assertEqual([e.get('lefty', False) for e in q], [True, False])
        self.press('green')
        self.assertTrue(self.ns['public_view']()['current']['lefty'])
        start = self.press('green')
        self.assertEqual((start['action'], start['lefty']), ('start', True))
        self.ns['admin_action']({'action': 'finish', 'score': '1'})
        self.press('green')
        self.assertFalse(self.press('green')['lefty'])

    def test_start_waits_for_in_song_picture_naming_the_right_next_player(self):
        self.lobby(); self.add('Ann', 'Bob')
        self.ns['RUNTIME'].update(bg_watching=True, lan_url='http://x', tunnel_url=None)
        self.ns['RUNTIME']['bg_done_key'] = self.ns['gameplay_bg_key']()[0]        # picture says: up next Ann
        self.press('green')                                                     # Ann called up: now it must say Bob
        self.assertEqual(self.press('green')['action'], 'wait')
        self.ns['RUNTIME']['bg_done_key'] = self.ns['gameplay_bg_key']()[0]        # the watcher stamped it
        self.assertEqual(self.press('green')['action'], 'start')

    def test_start_never_waits_long_for_the_picture(self):
        self.lobby(); self.add('Ann', 'Bob')
        self.ns['RUNTIME'].update(bg_watching=True, lan_url='http://x', tunnel_url=None, bg_done_key=None)
        self.press('green')
        self.ns['STATE']['current']['calledAt'] -= 5                              # stamping stuck: go anyway
        self.assertEqual(self.press('green')['action'], 'start')


if __name__ == '__main__':
    unittest.main(verbosity=1)
