"""脚本版本随内容更新，兼容已打补丁页面及跨平台换行。"""
import hashlib
import importlib.util
import re
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]


class FrontendAssetTests(unittest.TestCase):
    def test_every_injected_script_has_its_content_version(self):
        html = (ROOT / 'web/index.html').read_text(encoding='utf-8')
        urls = re.findall(r'<script\b[^>]*src="(/local_ext/[^"]+)"', html)
        self.assertEqual(len(urls), 8)
        for url in urls:
            parsed = urlsplit(url)
            body = (ROOT / parsed.path.lstrip('/')).read_bytes().replace(b'\r\n', b'\n')
            self.assertEqual(parse_qs(parsed.query).get('v'), [hashlib.sha256(body).hexdigest()[:12]], url)

    def test_existing_asset_block_is_updated_in_place(self):
        spec = importlib.util.spec_from_file_location('asset_patch_test', ROOT / 'tools/apply_local_patches.py')
        patches = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(patches)
        rel = 'web/index.html'
        html = (ROOT / rel).read_text(encoding='utf-8')
        item = next(p for p in patches.PATCHES[rel] if p['name'] == 'unified player controls')
        original = patches._strip_local(html)
        old = dict(item, insert='<script src="/local_ext/player_controls.js"></script>\n')
        previous, _ = patches.apply_one(original, old, rel)
        updated, state = patches.apply_one(previous, item, rel)
        self.assertEqual(state, 'updated')
        self.assertEqual(updated.count('src="/local_ext/player_controls.js'), 1)
        self.assertEqual(patches._strip_local(updated), original)
        again, state = patches.apply_one(updated, item, rel)
        self.assertEqual((again, state), (updated, 'already'))
        newer = dict(item, insert=re.sub(r'\?v=[0-9a-f]+', '?v=0123456789ab', item['insert']))
        refreshed, state = patches.apply_one(updated, newer, rel)
        self.assertEqual(state, 'updated')
        self.assertEqual(refreshed.count('src="/local_ext/player_controls.js'), 1)
        self.assertIn('?v=0123456789ab', refreshed)

    def test_cache_policy_and_line_endings(self):
        from local_ext.frontend_assets import cache_headers, version_token
        body = b'const version = 1;\n'
        token = version_token(body)
        self.assertEqual(token, version_token(body.replace(b'\n', b'\r\n')))
        for path in ['/', '/index.html?key=not-a-real-key', '/local_ext/player_controls.js', '/local_ext/player_controls.js?v=old']:
            self.assertEqual(dict(cache_headers(path, body, 200))['Cache-Control'], 'no-store', path)
        path = '/local_ext/player_controls.js?v=' + token
        self.assertIn('immutable', dict(cache_headers(path, body, 200))['Cache-Control'])
        self.assertEqual(dict(cache_headers(path, body, 404))['Cache-Control'], 'no-store')
        self.assertEqual(cache_headers('/proxy/img?url=fixture', body, 200), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
