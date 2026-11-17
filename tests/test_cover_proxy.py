"""封面自动适配与图片代理边界的离线回归。"""
import importlib
import json
import os
import socket
import sys
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from web import server as web


class CoverProxyTests(unittest.TestCase):
    def setUp(self):
        self.cover = importlib.import_module('local_ext.cover_proxy')
        self.cover.reset()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), web.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path) as response:
                return response.status, response.read(), response.headers
        except urllib.error.HTTPError as error:
            return error.code, error.read(), error.headers

    def image(self, url):
        return self.get('/proxy/img?url=' + urllib.parse.quote(url, safe=''))

    def register(self, url, path='/api/videos?page=1'):
        body = json.dumps({'status': 1, 'data': {'items': [{'cover': url}]}}).encode()
        with patch.object(web, '_fetch', return_value=(200, body, 'application/json')):
            self.assertEqual(self.get(path)[0], 200)

    def test_new_domain_from_list_is_accepted_but_other_urls_are_not(self):
        url = 'https://next-covers.example/a.jpg?auth_key=sample'
        self.register(url)
        jpeg = b'\xff\xd8\xff' + b'image'
        with patch.object(self.cover, '_download', return_value=jpeg) as download:
            status, body, headers = self.image(url)
            self.assertEqual((status, body, headers['Content-Type']), (200, jpeg, 'image/jpeg'))
            self.assertEqual(self.image(url.replace('a.jpg', 'other.jpg'))[0], 403)
            self.assertEqual(download.call_count, 1)

    def test_alias_search_rank_and_detail_register_covers(self):
        for path in ('/api/up/videos?page=2', '/api/search?q=a', '/api/ranks/hot', '/api/videos/12'):
            url = 'https://future.example/' + urllib.parse.quote(path, safe='')
            self.register(url, path)
            with patch.object(self.cover, '_download', return_value=b'GIF89aimage'):
                self.assertEqual(self.image(url)[0], 200, path)

    def test_unknown_and_untrusted_sources_are_rejected(self):
        url = 'https://future.example/a.jpg'
        self.register(url, '/api/videos/12/comments')
        with patch.object(self.cover, '_download') as download:
            self.assertEqual(self.image(url)[0], 403)
            download.assert_not_called()
        body = json.dumps({'data': {'cover': url}}).encode()
        self.cover.observe('https://evil.example/api/videos', 200, body, web.UPSTREAM)
        self.assertFalse(self.cover.allowed(url, web.IMG_HOSTS))

    def test_registration_expires_and_has_a_size_limit(self):
        url = 'https://future.example/a.jpg'
        with patch.object(self.cover.time, 'monotonic', return_value=100):
            self.register(url)
        with patch.object(self.cover.time, 'monotonic', return_value=100 + self.cover.REGISTRY_TTL + 1):
            self.assertFalse(self.cover.allowed(url, set()))
        with patch.object(self.cover, 'REGISTRY_LIMIT', 2):
            for index in range(3):
                self.register(url + str(index))
            self.assertFalse(self.cover.allowed(url + '0', set()))
            self.assertTrue(self.cover.allowed(url + '2', set()))

    def test_current_domain_and_explicit_config_work_without_registration(self):
        with patch.dict('os.environ', {'HGHUB_IMAGE_HOSTS': 'manual.example'}):
            for url in ('https://pic.qnlycc.cn/a.jpg', 'https://manual.example/a.jpg'):
                with patch.object(self.cover, '_download', return_value=b'GIF89aimage'):
                    self.assertEqual(self.image(url)[0], 200)

    def test_ciphertext_failure_is_502_and_is_not_cached(self):
        with patch.object(self.cover, '_download', return_value=b'ciphertext' * 16) as download:
            with patch.object(web, '_aes_decrypt_cbc_nopad', return_value=None):
                for _ in range(2):
                    status, body, headers = self.image('https://pic.qnlycc.cn/a.jpg')
                    self.assertEqual(status, 502)
                    self.assertIn('application/json', headers['Content-Type'])
                    self.assertIn(b'image decode failed', body)
            self.assertEqual(download.call_count, 2)

    def test_successfully_decrypted_image_is_cached(self):
        jpeg = b'\xff\xd8\xff' + b'decoded'
        with patch.object(self.cover, '_download', return_value=b'x' * 16) as download:
            with patch.object(web, '_aes_decrypt_cbc_nopad', return_value=jpeg) as decrypt:
                for _ in range(2):
                    self.assertEqual(self.image('https://pic.qnlycc.cn/encrypted.jpg')[:2], (200, jpeg))
                self.assertEqual(download.call_count, 1)
                self.assertEqual(decrypt.call_count, 1)

    def test_pinned_connection_uses_validated_address_and_original_tls_name(self):
        from unittest.mock import MagicMock
        context = MagicMock()
        context.post_handshake_auth = False
        addresses = [(socket.AF_INET, ('8.8.8.8', 443))]
        with patch.object(self.cover, '_public_addresses', return_value=addresses) as resolve:
            with patch.object(self.cover.socket, 'socket') as socket_factory:
                connection = self.cover._PinnedHTTPS('future.example', context=context)
                connection.connect()
                socket_factory.return_value.connect.assert_called_once_with(('8.8.8.8', 443))
                context.wrap_socket.assert_called_once_with(socket_factory.return_value, server_hostname='future.example')
                resolve.assert_called_once_with('future.example')

    def test_openssl_can_be_selected_without_path_changes(self):
        from unittest.mock import MagicMock
        completed = MagicMock(returncode=0, stdout=b'\xff\xd8\xffdecoded')
        with patch.dict('os.environ', {'HGHUB_OPENSSL': 'custom-openssl'}):
            with patch.object(self.cover.subprocess, 'run', return_value=completed) as run:
                self.assertEqual(self.cover.decrypt(b'x' * 16, web.IMG_KEY, web.IMG_IV), completed.stdout)
                self.assertEqual(run.call_args.args[0][0], 'custom-openssl')

    def test_unsafe_schemes_ports_and_credentials_are_rejected(self):
        for url in ('http://pic.qnlycc.cn/a', 'https://pic.qnlycc.cn:444/a',
                    'https://user:pass@pic.qnlycc.cn/a', 'https://pic.qnlycc.cn/a#fragment'):
            with patch.object(self.cover, '_download') as download:
                self.assertIn(self.image(url)[0], (400, 403))
                download.assert_not_called()

    def test_all_dns_answers_must_be_public(self):
        for ip in ('127.0.0.1', '10.0.0.1', '169.254.169.254', '::1', 'fc00::1', '::ffff:127.0.0.1'):
            answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443)),
                       (socket.AF_INET6 if ':' in ip else socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, 443))]
            with patch.object(self.cover.socket, 'getaddrinfo', return_value=answers):
                with self.assertRaises(ValueError):
                    self.cover._public_addresses('pic.qnlycc.cn')

    def test_redirects_and_oversized_responses_are_rejected(self):
        from unittest.mock import MagicMock
        for status, length, data in ((302, '0', b''), (200, str(self.cover.MAX_BYTES + 1), b''),
                                     (200, '', b'x' * (self.cover.MAX_BYTES + 1))):
            connection = MagicMock()
            response = connection.getresponse.return_value
            response.status = status
            response.getheader.return_value = length
            response.read.return_value = data
            with patch.object(self.cover, '_PinnedHTTPS', return_value=connection):
                with self.assertRaises(ValueError):
                    self.cover._download('https://pic.qnlycc.cn/a.jpg')
            connection.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
