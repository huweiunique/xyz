"""可信上游封面登记、受限下载与解密；仅使用标准库和系统 OpenSSL。"""
import http.client
import ipaddress
import json
import logging
import os
import re
import shutil
import socket
import ssl
import subprocess
import threading
import time
import urllib.parse
from collections import OrderedDict

REGISTRY_TTL = 3600
REGISTRY_LIMIT = 10000
MAX_BYTES = 8 * 1024 * 1024
_registry = OrderedDict()
_cache = OrderedDict()
_lock = threading.Lock()
log = logging.getLogger('hghub-cover')
_content_path = re.compile(r'/api/(?:videos(?:/\d+)?|search|ranks/[^/]+|posts(?:/\d+)?)\Z')


def reset():
    """清空当前进程的登记与图片缓存，供隔离测试使用。"""
    with _lock:
        _registry.clear()
        _cache.clear()


def _parse(url):
    if not isinstance(url, str) or len(url) > 8192 or any(ord(c) <= 32 or ord(c) == 127 for c in url):
        raise ValueError('invalid image URL')
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.port not in (None, 443)
            or parsed.username is not None or parsed.password is not None or parsed.fragment):
        raise ValueError('image URL must use HTTPS on port 443 without credentials or fragment')
    return parsed


def observe(url, status, body, upstream, data=None):
    """只采纳固定上游的内容 GET 响应，不从客户端图片请求学习。"""
    source = urllib.parse.urlsplit(url)
    origin = urllib.parse.urlsplit(upstream)
    if (data is not None or status != 200 or source.scheme != origin.scheme
            or source.netloc != origin.netloc or not _content_path.fullmatch(source.path)):
        return
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeError):
        return
    if not isinstance(payload, dict) or payload.get('status', 1) != 1:
        return
    pending = [payload.get('data')]
    urls = []
    # 限制遍历数量，避免异常上游响应占用无限内存或递归栈。
    for _ in range(50000):
        if not pending:
            break
        node = pending.pop()
        if isinstance(node, dict):
            cover = node.get('cover')
            try:
                _parse(cover)
                urls.append(cover)
            except ValueError:
                pass
            pending.extend(value for value in node.values() if isinstance(value, (dict, list)))
        elif isinstance(node, list):
            pending.extend(node)
    expiry = time.monotonic() + REGISTRY_TTL
    with _lock:
        for cover in urls:
            _registry[cover] = expiry
            _registry.move_to_end(cover)
        while len(_registry) > REGISTRY_LIMIT:
            _registry.popitem(last=False)


def allowed(url, legacy_hosts):
    try:
        host = _parse(url).hostname.lower()
    except ValueError:
        return False
    extra = {h.strip().lower() for h in os.environ.get('HGHUB_IMAGE_HOSTS', '').split(',') if h.strip()}
    if host in set(legacy_hosts) | extra | {'pic.qnlycc.cn'}:
        return True
    with _lock:
        expiry = _registry.get(url, 0)
        if expiry > time.monotonic():
            return True
        _registry.pop(url, None)
    return False


def _public_addresses(host):
    answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not answers:
        raise ValueError('image host has no public address')
    addresses = []
    for family, _, _, _, address in answers:
        ip = ipaddress.ip_address(address[0])
        if not ip.is_global or ip.is_multicast or ip.is_reserved:
            raise ValueError('image host resolved to a non-public address')
        addresses.append((family, address))
    return addresses


class _PinnedHTTPS(http.client.HTTPSConnection):
    def connect(self):
        # DNS 只查一次，连接直接使用经过校验的 sockaddr；TLS 仍校验证书与原域名。
        for family, address in _public_addresses(self.host):
            sock = socket.socket(family, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            try:
                sock.connect(address)
                self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
                return
            except OSError:
                sock.close()
        raise OSError('cannot connect to image host')


def _download(url):
    parsed = _parse(url)
    connection = _PinnedHTTPS(parsed.hostname, timeout=20, context=ssl.create_default_context())
    try:
        path = urllib.parse.urlunsplit(('', '', parsed.path or '/', parsed.query, ''))
        connection.request('GET', path, headers={'User-Agent': 'HGhub-cover/1.0', 'Accept-Encoding': 'identity'})
        response = connection.getresponse()
        # 图片代理不跟随重定向，防止允许的 URL 跳转到任意服务。
        if response.status != 200:
            raise ValueError('upstream image HTTP ' + str(response.status))
        length = response.getheader('Content-Length')
        if length and int(length) > MAX_BYTES:
            raise ValueError('image too large')
        body = response.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise ValueError('image too large')
        return body
    finally:
        connection.close()


def decrypt(data, key, iv):
    if not data or len(data) % 16:
        return None
    executable = os.environ.get('HGHUB_OPENSSL') or shutil.which('openssl')
    if not executable and os.name == 'nt':
        for root in (os.environ.get('ProgramFiles', r'C:\Program Files'),
                     os.environ.get('LOCALAPPDATA', '') + r'\Programs'):
            candidate = os.path.join(root, 'Git', 'usr', 'bin', 'openssl.exe')
            if os.path.isfile(candidate):
                executable = candidate
                break
    if not executable:
        log.warning('cover decryption unavailable: OpenSSL not found')
        return None
    try:
        result = subprocess.run([executable, 'enc', '-d', '-aes-128-cbc', '-nopad',
                                 '-K', key.hex(), '-iv', iv.hex()],
                                input=data, capture_output=True, timeout=20)
        return result.stdout if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        log.warning('cover decryption unavailable: OpenSSL execution failed')
        return None


def serve(handler, url, legacy_hosts, decrypt_image, sniff):
    try:
        _parse(url)
    except ValueError:
        handler._json(400, {'status': 0, 'msg': 'invalid image URL: HTTPS required'})
        return
    if not allowed(url, legacy_hosts):
        handler._json(403, {'status': 0, 'msg': 'image URL not allowed; reload content or configure HGHUB_IMAGE_HOSTS'})
        return
    with _lock:
        hit = _cache.get(url)
        if hit is not None:
            _cache.move_to_end(url)
    if hit is not None:
        handler._send(200, hit[0], content_type=hit[1])
        return
    try:
        body = _download(url)
    except (OSError, ValueError, http.client.HTTPException):
        log.warning('cover fetch failed for host %s', _parse(url).hostname)
        handler._json(502, {'status': 0, 'msg': 'image fetch failed'})
        return
    ctype = sniff(body)
    if ctype == 'application/octet-stream':
        body = decrypt_image(body)
        ctype = sniff(body) if body is not None else 'application/octet-stream'
    if ctype == 'application/octet-stream':
        log.warning('cover decode failed for host %s', _parse(url).hostname)
        handler._json(502, {'status': 0, 'msg': 'image decode failed; check OpenSSL and upstream image protocol'})
        return
    with _lock:
        _cache[url] = (body, ctype)
        _cache.move_to_end(url)
        while len(_cache) > 200:
            _cache.popitem(last=False)
    handler._send(200, body, content_type=ctype)
