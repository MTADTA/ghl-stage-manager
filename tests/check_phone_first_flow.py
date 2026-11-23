"""Offline queue/score checks against copied function definitions.

Never imports or starts server.py: it loads selected function definitions through
the AST and supplies fake state, songs, persistence, clock and automation.
"""
import ast
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
    'selected_index', 'remove_current', 'armed_for', 'arm_remove', 'disarm_remove', 'remove_hint', 'overlay_mode', 'results_card',
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
        runtime = dict(v=0, ready_v=0, tunnel_url=None, lan_url=None, skip_armed_at=0, mod_cmds=[], scene="Main Menu", mod_info={"home": "1"}, published_v=9999)
        ns = {'DIFFICULTIES': ('easy', 'medium', 'hard', 'expert'), 're': re, 'unicodedata': unicodedata, 'time': time, 'uuid': uuid, 'LOCK': threading.RLock(),
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

    def signup(self, name='Jordan', song='song1', device='phone1'):
        return self.ns['signup']({'name': name, 'songId': song, 'device': device})

    def test_duplicate_phone_and_song_rejected(self):
        self.assertTrue(self.signup()['ok'])
        self.assertIn('already in line', self.signup(song='song2')['error'])
        self.assertTrue(self.signup(device='phone2')['taken'])
        self.assertEqual(len(self.ns['STATE']['queue']), 1)

    def test_public_turn_ids_cover_queue_current_and_unscored_done(self):
        turn = self.signup()
        self.assertEqual(self.ns['public_view']()['queue'][0]['id'], turn['id'])
        self.ns['call_next']()
        self.assertEqual(self.ns['public_view']()['current']['id'], turn['id'])
        self.ns['go']()
        self.ns['admin_action']({'action': 'finish', 'score': ''})
        view = self.ns['public_view']()
        self.assertEqual(view['leaderboard'], [])
        self.assertEqual(view['completedIds'], [turn['id']])

    def test_skipped_phone_can_rejoin_backend(self):
        self.signup()
        self.assertTrue(self.ns['skip_next']())
        self.assertTrue(self.signup()['ok'])

    def test_removed_phone_can_rejoin_backend(self):
        entry = self.signup()
        self.ns['admin_action']({'action': 'remove', 'id': entry['id']})
        self.assertTrue(self.signup()['ok'])

    def test_turn_and_score_correction(self):
        self.signup()
        self.assertTrue(self.ns['call_next']())
        self.assertTrue(self.ns['go']())
        self.assertTrue(self.ns['admin_action']({'action': 'finish', 'score': '123'})['ok'])
        state = self.ns['STATE']
        self.assertIsNone(state['current'])
        self.assertEqual(state['done'][0]['score'], 123)
        self.ns['admin_action']({'action': 'set_score', 'id': state['done'][0]['id'], 'score': '321'})
        self.assertEqual(self.ns['leaderboard']()[0]['score'], 321)
        self.assertIn('already had your turn', self.signup(song='song2')['error'])

    def test_zero_from_phone_input_counts_as_score(self):
        self.signup()
        self.ns['call_next']()
        self.ns['go']()
        self.ns['admin_action']({'action': 'finish', 'score': '0'})
        self.assertEqual(self.ns['leaderboard']()[0]['score'], 0)

    def test_missing_score_can_be_repaired_later(self):
        self.signup()
        self.ns['call_next']()
        self.ns['go']()
        self.ns['admin_action']({'action': 'finish', 'score': ''})
        self.assertEqual(self.ns['leaderboard'](), [])
        entry = self.ns['STATE']['done'][0]
        self.ns['admin_action']({'action': 'set_score', 'id': entry['id'], 'score': '25'})
        self.assertEqual(self.ns['leaderboard']()[0]['score'], 25)

    def test_encore_reserved_and_not_ranked(self):
        self.assertTrue(self.signup(song='encore')['taken'])
        self.signup()
        self.ns['call_next']()
        self.ns['go']()
        self.ns['admin_action']({'action': 'finish', 'score': '100'})
        self.assertTrue(self.ns['admin_action']({'action': 'encore'})['ok'])
        self.assertIn('already lined up', self.ns['admin_action']({'action': 'encore'})['error'])
        self.ns['call_next']()
        self.ns['go']()
        self.ns['admin_action']({'action': 'finish', 'score': '1000'})
        self.assertEqual(len(self.ns['leaderboard']()), 1)
        self.assertEqual(self.ns['leaderboard']()[0]['score'], 100)

    def test_reset_makes_phone_eligible_again(self):
        self.signup()
        self.ns['admin_action']({'action': 'reset_night', 'confirm': 'RESET'})
        self.assertEqual(self.ns['STATE']['queue'], [])
        self.assertTrue(self.signup()['ok'])

    def test_signup_name_is_normalized(self):
        self.signup(name='Jordan  Rein')
        self.assertEqual(self.ns['public_view']()['queue'][0]['name'], 'Jordan Rein')

    def test_encore_waits_for_closed_signups_and_empty_queue(self):
        self.signup(); self.signup(name='Sam', song='song2', device='phone2')
        self.ns['call_next'](); self.ns['go']()
        self.ns['admin_action']({'action': 'finish', 'score': '100'})
        self.ns['admin_action']({'action': 'toggle_signups'})
        self.assertFalse(self.ns['STATE'].get('encore_started'))
        self.ns['call_next'](); self.ns['go']()
        self.ns['admin_action']({'action': 'finish', 'score': '50'})
        self.assertTrue(self.ns['STATE']['queue'][0]['encore'])
        self.assertEqual(self.ns['STATE']['queue'][0]['name'], 'Jordan')

    def test_encore_red_rotates_ready_player_and_song_stays_hidden_until_start(self):
        self.signup(); self.signup(name='Sam', song='song2', device='phone2')
        for score in ['100', '50']:
            self.ns['call_next'](); self.ns['go'](); self.ns['admin_action']({'action': 'finish', 'score': score})
        self.assertEqual(self.ns['STATE']['queue'], [])
        self.ns['admin_action']({'action': 'toggle_signups'})
        self.assertEqual(self.ns['press']('green')['action'], 'called')
        self.assertEqual(self.ns['public_view']()['current']['song'], 'Mystery encore')
        self.ns['RUNTIME']['last_press'] = 0
        self.assertEqual(self.ns['press']('red')['action'], 'encore_next')
        self.assertEqual(self.ns['STATE']['current']['name'], 'Sam')
        self.assertEqual(self.ns['STATE']['current']['phase'], 'ready')
        self.ns['RUNTIME']['last_press'] = 0
        self.assertEqual(self.ns['press']('green')['action'], 'start')
        self.assertEqual(self.ns['public_view']()['current']['song'], 'Jump')
        self.ns['admin_action']({'action': 'finish', 'score': '200'})
        self.assertEqual(self.ns['STATE']['queue'], [])
        self.assertEqual(len(self.ns['leaderboard']()), 2)

    def test_home_reset_requires_confirmation_and_stage_blue_is_ignored(self):
        self.signup()
        self.ns['RUNTIME']['mod_info']['home'] = '0'
        self.assertFalse(self.ns['press']('blue')['ok'])
        self.assertEqual(len(self.ns['STATE']['queue']), 1)
        self.ns['RUNTIME']['last_press'] = 0
        self.ns['RUNTIME']['mod_info']['home'] = '1'
        self.assertEqual(self.ns['press']('blue')['action'], 'reset_armed')
        self.assertEqual(len(self.ns['STATE']['queue']), 1)
        self.assertEqual(self.ns['public_view']()['hint'], '')
        self.ns['RUNTIME']['last_press'] = 0
        self.assertEqual(self.ns['press']('blue')['action'], 'reset')
        self.assertEqual(self.ns['STATE']['queue'], [])
        self.assertTrue(self.ns['STATE']['signups_open'])

    def test_home_reset_can_clear_interrupted_turn_but_not_gameplay(self):
        self.signup(); self.ns['call_next'](); self.ns['go']()
        self.ns['RUNTIME']['scene'] = 'Gameplay'
        self.assertFalse(self.ns['press']('blue')['ok'])
        self.assertIsNotNone(self.ns['STATE']['current'])
        self.ns['RUNTIME']['scene'] = 'Main Menu'
        self.ns['RUNTIME']['last_press'] = 0
        self.assertEqual(self.ns['press']('blue')['action'], 'reset_armed')
        self.ns['RUNTIME']['last_press'] = 0
        self.ns['press']('blue')
        self.assertIsNone(self.ns['STATE']['current'])

    def test_midi_reads_only_guitar_notes_and_handles_running_status(self):
        def chunk(kind, data): return kind + len(data).to_bytes(4, "big") + data
        def track(name, events):
            title = name.encode()
            return chunk(b"MTrk", b"\x00\xff\x03" + bytes([len(title)]) + title + events + b"\x00\xff\x2f\x00")
        header = chunk(b"MThd", b"\x00\x01\x00\x02\x01\xe0")
        midi = header + track("PART DRUMS", b"\x00\x90\x60\x40") + track("PART GUITAR", b"\x00\x90\x3c\x40\x00\x54\x40\x00\x5f\x40")
        chart = Path(self.tmp.name) / "notes.mid"; chart.write_bytes(midi)
        self.assertEqual(self.ns['chart_difficulties'](chart), ['easy', 'hard', 'expert'])
        chart.write_bytes(midi[:-5])
        self.ns['chart_difficulties'](chart)  # malformed tracks never crash startup

    def test_chart_reads_single_guitar_levels_not_drums_or_marker_only(self):
        chart = Path(self.tmp.name) / "notes.chart"
        chart.write_text('[EasySingle]\n{\n 0 = N 5 0\n}\n[MediumSingle]\n{\n 0 = N 0 0\n}\n[ExpertDrums]\n{\n 0 = N 0 0\n}\n[HardSingle]\n{\n 0 = N 7 0\n}')
        self.assertEqual(self.ns['chart_difficulties'](chart), ['medium', 'hard'])

    def test_signup_stores_supported_difficulty_and_expert_only_is_honest(self):
        self.ns['SONGS']['song1']['difficulties'] = ['easy', 'hard']
        result = self.ns['signup']({'name': 'Sam', 'songId': 'song1', 'device': 'p', 'difficulty': 'medium'})
        self.assertEqual(result['difficulty'], 'hard')
        self.assertEqual(self.ns['public_view']()['songs'][0]['difficulties'], ['easy', 'hard'])
        self.ns['SONGS']['song2']['difficulties'] = ['expert']
        result = self.ns['signup']({'name': 'Jo', 'songId': 'song2', 'device': 'q', 'difficulty': 'easy'})
        self.assertEqual(result['difficulty'], 'expert')

    def test_phone_choice_is_used_and_start_buttons_do_not_trigger_renders(self):
        self.ns['SONGS']['song1']['difficulties'] = ['easy', 'hard', 'expert']
        result = self.signup(); self.ns['call_next']()
        self.ns['RUNTIME']['mod_info']['home'] = '0'
        version = self.ns['RUNTIME']['v']; ready_version = self.ns['RUNTIME']['ready_v']
        self.assertFalse(self.ns['press']('blue')['ok'])
        self.ns['RUNTIME']['last_press'] = 0
        self.assertFalse(self.ns['press']('yellow')['ok'])
        self.assertEqual(self.ns['RUNTIME']['v'], version)
        self.assertEqual(self.ns['STATE']['current']['difficulty'], 'easy')
        body = {'action': 'set_difficulty', 'id': result['id'], 'difficulty': 'hard'}
        self.assertEqual(self.ns['admin_action'](body)['difficulty'], 'hard')
        self.assertEqual(self.ns['RUNTIME']['ready_v'], ready_version)
        self.ns['RUNTIME']['last_press'] = 0
        self.assertEqual(self.ns['press']('green')['difficulty'], 'hard')
        self.ns['RUNTIME']['last_press'] = 0
        self.ns['RUNTIME']['scene'] = 'Gameplay'
        self.assertFalse(self.ns['press']('blue')['ok'])
        self.assertEqual(self.ns['STATE']['current']['difficulty'], 'hard')

    def test_phone_cannot_change_difficulty_after_start(self):
        result = self.signup(); self.ns['call_next']()
        body = {'action': 'set_difficulty', 'id': result['id'], 'difficulty': 'expert'}
        self.assertEqual(self.ns['admin_action'](body)['difficulty'], 'expert')
        self.ns['go']()
        self.assertIn('before', self.ns['admin_action'](body)['error'])
        self.assertEqual(self.ns['STATE']['current']['difficulty'], 'expert')

    def test_red_twice_skips_no_show_and_reopens_song(self):
        first = self.signup(); second = self.signup(name='Sam', song='song2', device='phone2')
        self.ns['RUNTIME']['mod_info']['home'] = '0'
        self.assertEqual(self.ns['press']('red')['action'], 'skip_armed')
        self.ns['RUNTIME']['last_press'] = 0
        self.assertEqual(self.ns['press']('red')['action'], 'skipped')
        view = self.ns['public_view']()
        self.assertEqual(view['queue'][0]['id'], second['id'])
        self.assertFalse(next(s for s in view['songs'] if s['id'] == 'song1')['taken'])
        self.assertEqual(self.ns['STATE']['skipped'][0]['id'], first['id'])

    def test_name_filter_rejects_common_bypasses_without_enqueuing(self):
        for name in ['Fuck', 'F.U.C.K', 'f u c k', 'f*ck', 'f**k', 'ＦＵＣＫ', 'f\u200buck',
                     'fück', 'fucking', 'fuckyou', 'SH1T', 'sh!t', 'sh*t', 'a$$hole',
                     'b1tch', 'b*tch', 'Poop', 'n1gger', 'n i g g a', 'faggot']:
            with self.subTest(name=name):
                self.assertIn('stage-friendly', self.signup(name=name)['error'])
                self.assertIn('stage-friendly', self.ns['admin_action'](
                    {'action': 'add', 'name': name, 'songId': 'song1', 'force': True})['error'])
                self.assertEqual(self.ns['STATE']['queue'], [])
                self.assertEqual(self.saved, [])

    def test_filter_keeps_legitimate_names_and_rejects_handles(self):
        for name in ["Cassidy", "Bass", "Scunthorpe", "Dick", "Dick Van Dyke", "Cox",
                     "Ann-Marie", "O'Connor", "D'Amico", "José", "Shital", "Bishit", "Jordan Rein"]:
            with self.subTest(name=name):
                self.assertFalse(self.ns['inappropriate_name'](name))
                self.assertEqual(self.ns['public_name'](name), name)
        body = {'name': 'Jordan', 'ig': 'shit_head', 'songId': 'song1'}
        self.assertIn('Instagram', self.ns['signup'](body)['error'])
        self.assertIn('Instagram', self.ns['admin_action']({'action': 'add', **body})['error'])
        self.assertEqual(self.ns['STATE']['queue'], [])

    def test_existing_offensive_names_are_masked_without_changing_scores_or_ids(self):
        self.signup(); self.signup(name='Sam', song='song2', device='p2')
        entry = self.ns['STATE']['queue'][0]
        entry['name'] = 'Fuck'; entry['ig'] = 'shit_head'
        self.ns['RUNTIME']['skip_armed_at'] = time.time()
        view = self.ns['public_view']()
        self.assertEqual(view['queue'][0]['name'], 'Rock Star')
        self.assertEqual(view['queue'][0]['ig'], '')
        self.assertEqual(view['queue'][0]['id'], entry['id'])
        self.assertNotIn('Fuck', view['hint'])
        self.ns['call_next']()
        self.assertEqual(self.ns['public_view']()['current']['name'], 'Rock Star')
        self.ns['go'](); self.ns['admin_action']({'action': 'finish', 'score': '123'})
        board = self.ns['public_view']()['leaderboard']
        self.assertEqual((board[0]['id'], board[0]['score'], board[0]['name']), (entry['id'], 123, 'Rock Star'))
        self.assertEqual(self.ns['STATE']['done'][0]['name'], 'Fuck')
        self.assertEqual(self.ns['STATE']['done'][0]['ig'], 'shit_head')

    def test_thanks_waits_for_completed_encore_and_reset_clears_it(self):
        self.assertFalse(self.ns['public_view']()['showFinished'])
        self.signup(); self.ns['call_next'](); self.ns['go']()
        self.ns['admin_action']({'action': 'finish', 'score': '100'})
        self.ns['admin_action']({'action': 'toggle_signups'})
        self.assertFalse(self.ns['public_view']()['showFinished'])  # encore in line
        self.ns['call_next']()
        self.assertFalse(self.ns['public_view']()['showFinished'])  # reveal winner
        self.ns['go']()
        self.assertFalse(self.ns['public_view']()['showFinished'])  # encore playing
        self.ns['admin_action']({'action': 'finish', 'score': '200'})
        self.assertTrue(self.ns['public_view']()['showFinished'])
        self.assertEqual(len(self.ns['leaderboard']()), 1)
        self.ns['admin_action']({'action': 'toggle_signups'})
        self.assertFalse(self.ns['public_view']()['showFinished'])
        self.ns['admin_action']({'action': 'reset_night', 'confirm': 'RESET'})
        self.assertFalse(self.ns['public_view']()['showFinished'])
        self.assertEqual(self.ns['STATE']['done'], [])

    def test_thanks_without_encore_requires_a_finished_turn(self):
        self.ns['ENCORE_ID'] = None
        self.ns['STATE']['signups_open'] = False
        self.assertFalse(self.ns['public_view']()['showFinished'])
        self.ns['STATE']['signups_open'] = True
        self.signup(); self.ns['call_next'](); self.ns['go']()
        self.ns['admin_action']({'action': 'toggle_signups'})
        self.assertFalse(self.ns['public_view']()['showFinished'])
        self.ns['admin_action']({'action': 'finish', 'score': ''})
        self.assertTrue(self.ns['public_view']()['showFinished'])

if __name__ == '__main__':
    unittest.main(verbosity=2)
