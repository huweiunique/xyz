"""播放响应到清单、密钥、分片的完整代理路由回归。"""
import importlib
import json
import os
import re
import sys
import subprocess
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proxy import server as proxy
from web import server as web


class MediaProxyTests(unittest.TestCase):
    def setUp(self):
        self.media = importlib.import_module('local_ext.media_proxy')
        self.media.reset()
        self.proxy = ThreadingHTTPServer(('127.0.0.1', 0), proxy.Handler)
        self.web = ThreadingHTTPServer(('127.0.0.1', 0), web.Handler)
        self.threads = []
        for server in (self.proxy, self.web):
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.threads.append(thread)
        self.base = 'http://127.0.0.1:' + str(self.web.server_port)
        self.proxy_base = 'http://127.0.0.1:' + str(self.proxy.server_port)
        self.proxy_setting = patch.object(web, 'PROXY_BASE', self.proxy_base)
        self.proxy_setting.start()

    def tearDown(self):
        self.proxy_setting.stop()
        for server in (self.proxy, self.web):
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join()

    def request(self, path, payload=None, base=None, headers=None):
        body = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request((base or self.base) + path, data=body,
                                     headers=headers or {'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status, response.read(), response.headers
        except urllib.error.HTTPError as error:
            return error.code, error.read(), error.headers

    def play(self, url='https://future-cdn.example/master.m3u8', auth='server-token'):
        response = {'status': 1, 'data': {'streams': {'main': url},
                    'cdn': {'authKey': 'x-cdn-auth', 'authValue': auth}}}
        with patch.object(proxy, '_fetch', return_value=(200, json.dumps(response).encode(), 'application/json')):
            self.assertEqual(self.request('/api/play', {'id': 12, 'ep': 1})[0], 200)
        return '/proxy/m3u8?url=' + urllib.parse.quote(url, safe='')

    def test_full_web_chain_registers_new_domains_and_inherits_server_auth(self):
        master = b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000\nsub/child.m3u8\n'
        child = (b'#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="https://new-keys.example/key?id=1"\n'
                 b'#EXTINF:10,\nhttps://new-segments.example/first.ts?sig=1\n#EXT-X-ENDLIST\n')
        calls = []
        def download(url, **kwargs):
            calls.append((url, kwargs))
            return { 'https://future-cdn.example/master.m3u8': master,
                     'https://future-cdn.example/sub/child.m3u8': child,
                     'https://new-keys.example/key?id=1': b'k' * 16,
                     'https://new-segments.example/first.ts?sig=1': b'segment' * 100 }[url]
        path = self.play()
        with patch.object(self.media, 'download_resource', side_effect=download):
            status, body, _ = self.request(path, headers={'x-cdn-auth': 'client-forged'})
            self.assertEqual(status, 200)
            child_path = body.decode().splitlines()[-1]
            self.assertTrue(child_path.startswith('/proxy/m3u8?url='))
            status, body, _ = self.request(child_path)
            self.assertEqual(status, 200)
            key_path = re.search(r'URI="([^"]+)"', body.decode()).group(1)
            segment_path = next(line for line in body.decode().splitlines() if line and not line.startswith('#'))
            self.assertEqual(self.request(key_path)[:2], (200, b'k' * 16))
            self.assertEqual(self.request(segment_path)[:2], (200, b'segment' * 100))
        for _, options in calls:
            self.assertEqual(options['headers'], {'x-cdn-auth': 'server-token'})

    def test_unknown_url_and_same_host_other_path_are_not_learned(self):
        self.play()
        for url in ('https://future-cdn.example/arbitrary.m3u8', 'https://another.example/a.ts'):
            with patch.object(self.media, 'download_resource') as download:
                status, body, _ = self.request('/proxy/ts?url=' + urllib.parse.quote(url, safe=''))
                self.assertEqual(status, 403)
                self.assertEqual(json.loads(body)['source'], 'local_policy')
                download.assert_not_called()

    def test_all_four_proxy_entries_share_the_same_registration_policy(self):
        self.play()
        q = urllib.parse.quote('https://future-cdn.example/master.m3u8', safe='')
        with patch.object(self.media, 'download_resource', return_value=b'#EXTM3U\n#EXT-X-ENDLIST\n'):
            for route in ('/proxy', '/proxy/m3u8', '/proxy/key', '/proxy/ts'):
                status, body, headers = self.request(route + '?url=' + q, base=self.proxy_base)
                self.assertEqual(status, 200)
                self.assertIn('mpegurl', headers['Content-Type'])

    def test_registration_precedes_playback_response(self):
        recorded = []
        send_json = proxy.Handler._json
        def inspect_response(handler, status, obj):
            if handler.command == 'POST' and status == 200:
                url = obj['data']['streams']['main']
                recorded.append(url in self.media._registry)
            return send_json(handler, status, obj)
        with patch.object(proxy.Handler, '_json', inspect_response):
            self.play()
        self.assertEqual(recorded, [True])

    def test_upstream_403_is_distinguished_from_local_policy(self):
        path = self.play()
        with patch.object(self.media, 'download_resource', side_effect=self.media.UpstreamError(403)):
            status, body, _ = self.request(path)
            self.assertEqual(status, 403)
            self.assertEqual(json.loads(body)['source'], 'upstream')

    def test_expiry_capacity_and_failed_playback_do_not_grant_access(self):
        with patch.object(self.media.time, 'monotonic', return_value=100):
            path = self.play()
        with patch.object(self.media.time, 'monotonic', return_value=100 + self.media.REGISTRY_TTL + 1):
            self.assertEqual(self.request(path)[0], 403)
        self.media.reset()
        with patch.object(self.media, 'REGISTRY_LIMIT', 2):
            paths = [self.play('https://future.example/' + str(i) + '.m3u8') for i in range(3)]
            self.assertEqual(self.request(paths[0])[0], 403)
        self.media.reset()
        self.media.observe({'status': 0, 'data': {'streams': {'main': 'https://future.example/no.m3u8'}}})
        self.assertEqual(self.request('/proxy/m3u8?url=https%3A%2F%2Ffuture.example%2Fno.m3u8')[0], 403)

    def test_invalid_manifest_does_not_register_embedded_links(self):
        path = self.play()
        with patch.object(self.media, 'download_resource', return_value=b'<html>https://evil.example/a.ts</html>'):
            self.assertEqual(self.request(path)[0], 502)

    def test_uri_attributes_relative_paths_and_extensionless_playlists(self):
        path = self.play('https://future.example/master')
        body = (b'#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,URI="audio?id=1"\n'
                b'#EXT-X-I-FRAME-STREAM-INF:BANDWIDTH=1,URI="iframes"\n'
                b'#EXT-X-SESSION-KEY:METHOD=AES-128,URI="key"\n'
                b'#EXT-X-MAP:URI="init.mp4"\n#EXT-X-STREAM-INF:BANDWIDTH=2\nvideo?id=2\n')
        with patch.object(self.media, 'download_resource', return_value=body):
            status, rewritten, _ = self.request(path)
        self.assertEqual(status, 200)
        self.assertEqual(rewritten.count(b'/proxy/m3u8?url='), 3)
        self.assertEqual(rewritten.count(b'/proxy/key?url='), 1)
        self.assertEqual(rewritten.count(b'/proxy/ts?url='), 1)

    def test_credentials_and_private_resource_urls_are_rejected(self):
        for url in ('http://future.example/master.m3u8', 'https://user:pass@future.example/a.m3u8'):
            path = self.play(url)
            self.assertIn(self.request(path)[0], (400, 403))
        path = self.play()
        with patch.object(self.media, 'download_resource', side_effect=ValueError('non-public address')):
            self.assertEqual(self.request(path)[0], 502)

    def test_api_upstream_configuration_is_shared_by_both_services(self):
        result = subprocess.run([sys.executable, '-c',
            'from proxy import server as p;from web import server as w;print(p.UPSTREAM);print(w.UPSTREAM)'],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            env=dict(os.environ, UPSTREAM='https://next-api.example/'), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ['https://next-api.example'] * 2)


if __name__ == '__main__':
    unittest.main()
