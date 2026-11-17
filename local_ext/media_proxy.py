"""只信任播放接口及其 HLS 引用的完整资源 URL，动态适配 CDN。"""
import http.client
import logging
import re
import threading
import time
import urllib.parse
from collections import OrderedDict

from local_ext.cover_proxy import UpstreamError, download_resource, parse_https_url

REGISTRY_TTL = 3600
REGISTRY_LIMIT = 50000
LIMITS = {'m3u8': 2 * 1024 * 1024, 'key': 65536, 'ts': 64 * 1024 * 1024}
_registry = OrderedDict()
_lock = threading.Lock()
_uri = re.compile(r'(?<![\w-])URI="([^"\r\n]+)"')
log = logging.getLogger('hghub-media')


def reset():
    with _lock:
        _registry.clear()


def _register(entries, headers, expiry):
    with _lock:
        for url, kind in entries:
            _registry[url] = (kind, headers, expiry)
            _registry.move_to_end(url)
        while len(_registry) > REGISTRY_LIMIT:
            _registry.popitem(last=False)


def observe(response):
    """由固定上游播放接口成功返回后调用，不读取客户端提供的鉴权头。"""
    if not isinstance(response, dict) or response.get('status', 1) != 1:
        return
    data = response.get('data') or response
    if not isinstance(data, dict) or not isinstance(data.get('streams'), dict):
        return
    cdn = data.get('cdn') or {}
    headers = ()
    if isinstance(cdn, dict) and str(cdn.get('authKey', '')).lower() == 'x-cdn-auth':
        value = cdn.get('authValue')
        if isinstance(value, str) and len(value) <= 4096 and all(32 <= ord(c) < 127 for c in value):
            headers = (('x-cdn-auth', value),)
    entries = []
    for url in data['streams'].values():
        try:
            parse_https_url(url)
            entries.append((url, 'm3u8'))
        except ValueError:
            continue
    _register(entries, headers, time.monotonic() + REGISTRY_TTL)


def _rewrite(body, parent, headers, expiry):
    text = body.decode('utf-8-sig')
    lines = text.splitlines()
    if not lines or lines[0].strip() != '#EXTM3U':
        raise ValueError('invalid HLS manifest')
    entries = []

    def reference(value, kind):
        url = urllib.parse.urljoin(parent, value)
        parse_https_url(url)
        entries.append((url, kind))
        if len(entries) > 10000:
            raise ValueError('too many HLS references')
        return '/proxy/' + kind + '?url=' + urllib.parse.quote(url, safe='')

    rewritten = []
    next_playlist = False
    for line in lines:
        line = line.strip()
        if line.startswith('#'):
            tag = line.split(':', 1)[0]
            if tag in ('#EXT-X-KEY', '#EXT-X-SESSION-KEY'):
                kind = 'key'
            elif tag in ('#EXT-X-MEDIA', '#EXT-X-I-FRAME-STREAM-INF', '#EXT-X-RENDITION-REPORT'):
                kind = 'm3u8'
            else:
                kind = 'ts'
            line = _uri.sub(lambda match: 'URI="' + reference(match.group(1), kind) + '"', line)
            if tag == '#EXT-X-STREAM-INF':
                next_playlist = True
        elif line:
            kind = 'm3u8' if next_playlist or urllib.parse.urlsplit(line).path.lower().endswith('.m3u8') else 'ts'
            line = reference(line, kind)
            next_playlist = False
        rewritten.append(line)
    # 全部解析成功后一次性登记，且必须先登记再把重写清单发给播放器。
    _register(entries, headers, expiry)
    return ('\n'.join(rewritten) + '\n').encode('utf-8')


def serve(handler, url):
    try:
        parse_https_url(url)
    except ValueError:
        # 保持旧入口对陌生 http 地址返回 403，对空 URL/非 HTTP 返回 400。
        status = 403 if isinstance(url, str) and url.startswith('http://') else 400
        handler._json(status, {'status': 0, 'source': 'local_policy', 'msg': 'host not allowed or invalid HTTPS URL'})
        return
    with _lock:
        entry = _registry.get(url)
        if entry is not None and entry[2] <= time.monotonic():
            _registry.pop(url, None)
            entry = None
    if entry is None:
        handler._json(403, {'status': 0, 'source': 'local_policy', 'msg': 'host not allowed: resource URL not registered; request playback again'})
        return
    kind, headers, expiry = entry
    try:
        body = download_resource(url, headers=dict(headers), max_bytes=LIMITS[kind])
        if kind == 'm3u8':
            body = _rewrite(body, url, headers, expiry)
            ctype = 'application/vnd.apple.mpegurl'
        elif kind == 'key':
            ctype = 'application/octet-stream'
        else:
            ctype = 'video/MP2T' if urllib.parse.urlsplit(url).path.lower().endswith('.ts') else 'application/octet-stream'
    except UpstreamError as error:
        # 3xx 不向浏览器透传，避免跳过代理策略；真正的上游 403 则保留并注明来源。
        status = error.status if 400 <= error.status <= 599 else 502
        handler._json(status, {'status': 0, 'source': 'upstream', 'msg': 'upstream media HTTP ' + str(error.status)})
        return
    except (OSError, ValueError, UnicodeError, http.client.HTTPException):
        log.warning('media fetch or manifest parse failed for host %s', urllib.parse.urlsplit(url).hostname)
        handler._json(502, {'status': 0, 'source': 'resource_validation', 'msg': 'media fetch or manifest parse failed'})
        return
    handler._send(200, body, content_type=ctype)
