"""执行真实榜单、首页暂停与存储接缝；无需浏览器和上游网络。"""
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), '未安装 Node.js')
class RankAudioTests(unittest.TestCase):
    def run_case(self, case):
        result = subprocess.run(
            ['node', str(ROOT / 'tests/rank_audio_harness.js'), case],
            cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_rank_ids_tabs_metrics_and_request_races(self):
        self.run_case('rank')

    def test_session_mute_and_pause_never_enable_sound(self):
        self.run_case('audio')

    def test_extensions_initialize_before_home_and_deep_link(self):
        self.run_case('startup')


if __name__ == '__main__':
    unittest.main(verbosity=2)
