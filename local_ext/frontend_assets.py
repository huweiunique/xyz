"""前端入口及时更新，带内容版本的公开脚本可安全缓存。"""
import hashlib
import urllib.parse


def version_token(body):
    """统一 Git/Windows 换行，使构建机与运行机生成相同的脚本版本。"""
    return hashlib.sha256(body.replace(b'\r\n', b'\n')).hexdigest()[:12]


def cache_headers(path, body, status):
    parsed = urllib.parse.urlsplit(path)
    if parsed.path in ('/', '/index.html'):
        return [('Cache-Control', 'no-store')]
    if parsed.path.startswith('/local_ext/') and parsed.path.endswith('.js'):
        version = urllib.parse.parse_qs(parsed.query).get('v')
        if status == 200 and version == [version_token(body)]:
            return [('Cache-Control', 'public, max-age=31536000, immutable')]
        # 旧页面或旧安装包仍可使用无版本地址，但响应不再长期留在客户端缓存中。
        return [('Cache-Control', 'no-store')]
    return []
