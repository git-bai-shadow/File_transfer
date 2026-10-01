# -*- coding: utf-8 -*-
"""局域网文件共享系统测试集。运行: pytest tests/ -v"""
import io
import json
import time

import pytest
from PIL import Image

import app as m

TOKEN = 'test-csrf-token'


@pytest.fixture()
def client(tmp_path, monkeypatch):
    shared = tmp_path / 'shared'
    shared.mkdir()
    m.app.config['UPLOAD_FOLDER'] = str(shared)
    m.app.config['TESTING'] = True
    m.SHARE_FILE = str(tmp_path / 'shares.json')
    m.THUMB_CACHE = str(tmp_path / 'thumbs')

    (shared / 'hello.txt').write_text('你好世界', encoding='utf-8')
    (shared / 'ascii.txt').write_text('ABCDEFGH', encoding='utf-8')
    (shared / '报告.docx').write_bytes(b'docx-bytes')
    sub = shared / '子目录'
    sub.mkdir()
    (sub / 'inner.txt').write_text('inner', encoding='utf-8')
    Image.new('RGB', (100, 80), (200, 30, 30)).save(shared / 'pic.png')

    with m.app.test_client() as c:
        yield c, shared


def login(client, username='1', role='admin'):
    with client.session_transaction() as s:
        s['logged_in'] = True
        s['username'] = username
        s['role'] = role
        s['csrf_token'] = TOKEN
    return {'X-CSRF-Token': TOKEN, 'Accept': 'application/json'}


def os_trash(recorder, path):
    recorder.append(path)
    import os
    import shutil
    if os.path.isdir(path):
        shutil.rmtree(path)
    else:
        os.remove(path)


# ---------------- 登录/登出 ----------------
def test_login_real_passwords(client):
    c, _ = client
    r = c.post('/login', data={'username': '1', 'password': '1'})
    assert r.status_code == 302
    with c.session_transaction() as s:
        assert s['role'] == 'admin'
        assert s['csrf_token']

    c2 = m.app.test_client()
    r = c2.post('/login', data={'username': 'guest', 'password': 'guest'})
    assert r.status_code == 302
    with c2.session_transaction() as s:
        assert s['role'] == 'guest'


def test_login_wrong(client):
    c, _ = client
    r = c.post('/login', data={'username': '1', 'password': 'bad'})
    assert r.status_code == 200
    assert '错误的用户名或密码' in r.get_data(as_text=True)


def test_index_requires_login(client):
    c, _ = client
    r = c.get('/')
    assert r.status_code == 302
    assert '/login' in r.headers['Location']


def test_logout(client):
    c, _ = client
    login(c)
    assert c.get('/logout').status_code == 302
    assert c.get('/').status_code == 302


# ---------------- 浏览 ----------------
def test_index_renders_new_components(client):
    c, _ = client
    login(c)
    html = c.get('/').get_data(as_text=True)
    for token in ['items-data', 'search-box', 'view-grid', 'upload-btn',
                  'mkdir-btn', 'share-modal', 'drag-overlay', 'csrf-token']:
        assert token in html, token


def test_guest_ui_hides_write_actions(client):
    c, _ = client
    login(c, username='guest', role='guest')
    html = c.get('/').get_data(as_text=True)
    assert 'id="upload-btn"' not in html
    assert 'id="mkdir-btn"' not in html


def test_traversal_blocked(client):
    c, _ = client
    login(c)
    assert c.get('/details/..%2Fconfig.py').status_code == 400
    assert c.get('/preview/..%2Fconfig.py').status_code == 404
    assert c.get('/..%2F..').status_code == 400


def test_pagination(client, monkeypatch):
    c, shared = client
    monkeypatch.setattr(m, 'PAGE_SIZE', 5)
    for i in range(12):
        (shared / f'f{i:02d}.dat').write_text('x')
    login(c)
    page1 = c.get('/').get_data(as_text=True)
    assert '第 1 / 4 页' in page1
    assert '共 17 项' in page1
    page2 = c.get('/?page=2').get_data(as_text=True)
    assert '第 2 / 4 页' in page2
    assert '上一页' in page2 and '下一页' in page2


def test_details_fields(client):
    c, _ = client
    login(c)
    data = c.get('/details/hello.txt').get_json()
    assert data['name'] == 'hello.txt'
    assert data['preview'] == 'text'
    assert data['icon'] == '📝'
    data = c.get('/details/pic.png').get_json()
    assert data['preview'] == 'image'
    assert data['icon'] == '🖼️'


# ---------------- 上传 ----------------
def test_upload_batch_chinese_and_duplicate(client):
    c, shared = client
    h = login(c)
    r = c.post('/upload?subpath=', headers=h,
               data={'file': [(io.BytesIO('甲'.encode()), '测试 甲.txt'),
                              (io.BytesIO('乙'.encode()), '测试 乙.txt')]})
    assert r.status_code == 200
    assert r.get_json()['saved'] == 2
    assert (shared / '测试 甲.txt').read_text(encoding='utf-8') == '甲'

    r = c.post('/upload?subpath=', headers=h,
               data={'file': [(io.BytesIO('again'.encode()), '测试 甲.txt')]})
    assert (shared / '测试 甲 (1).txt').exists()
    assert not (shared / '测试 甲 (2).txt').exists()


def test_upload_requires_login(client):
    c, _ = client
    r = c.post('/upload?subpath=', data={'file': [(io.BytesIO(b'x'), 'a.txt')]})
    assert r.status_code == 302


def test_upload_guest_denied(client):
    c, _ = client
    h = login(c, username='guest', role='guest')
    r = c.post('/upload?subpath=', headers=h,
               data={'file': [(io.BytesIO(b'x'), 'a.txt')]})
    assert r.status_code == 403


def test_csrf_required(client):
    c, _ = client
    login(c)
    r = c.post('/api/mkdir', headers={'Accept': 'application/json'},
               data=json.dumps({'parent': '', 'name': 'x'}),
               content_type='application/json')
    assert r.status_code == 403


# ---------------- 管理 API ----------------
def test_mkdir_rename_delete(client, monkeypatch):
    c, shared = client
    h = login(c)
    trashed = []
    monkeypatch.setattr(m, 'send2trash', lambda p: os_trash(trashed, p))

    assert c.post('/api/mkdir', headers=h, data=json.dumps({'parent': '', 'name': '新建目录'}),
                  content_type='application/json').get_json() == {'ok': True}
    assert (shared / '新建目录').is_dir()

    assert c.post('/api/rename', headers=h,
                  data=json.dumps({'path': 'hello.txt', 'new_name': 'renamed.txt'}),
                  content_type='application/json').get_json() == {'ok': True}
    assert (shared / 'renamed.txt').exists()

    r = c.post('/api/delete', headers=h, data=json.dumps({'path': 'renamed.txt'}),
               content_type='application/json')
    assert r.get_json() == {'ok': True}
    assert not (shared / 'renamed.txt').exists()
    assert len(trashed) == 1  # 删除走的是回收站逻辑
    import os
    # 回归:send2trash 收到的必须是规范化的 Windows 路径(正斜杠会导致 E_INVALIDARG)
    assert '/' not in trashed[0]
    assert trashed[0] == os.path.normpath(trashed[0])


def test_mkdir_conflict(client):
    c, _ = client
    h = login(c)
    r = c.post('/api/mkdir', headers=h, data=json.dumps({'parent': '', 'name': 'hello.txt'}),
               content_type='application/json')
    assert r.status_code == 409


def test_delete_root_refused(client):
    c, _ = client
    h = login(c)
    r = c.post('/api/delete', headers=h, data=json.dumps({'path': ''}),
               content_type='application/json')
    assert r.status_code == 400


def test_rename_sanitizes(client):
    c, shared = client
    h = login(c)
    r = c.post('/api/rename', headers=h,
               data=json.dumps({'path': 'hello.txt', 'new_name': 'a<..b?.txt'}),
               content_type='application/json')
    assert r.status_code == 200
    names = [p.name for p in shared.iterdir()]
    assert 'a<..b?.txt' not in names  # Windows 非法字符已被替换


def test_guest_api_denied(client):
    c, _ = client
    h = login(c, username='guest', role='guest')
    for endpoint, payload in [('/api/mkdir', {'parent': '', 'name': 'x'}),
                              ('/api/delete', {'path': 'hello.txt'}),
                              ('/api/rename', {'path': 'hello.txt', 'new_name': 'y'}),
                              ('/share/create', {'path': 'hello.txt', 'hours': 1})]:
        r = c.post(endpoint, headers=h, data=json.dumps(payload), content_type='application/json')
        assert r.status_code == 403, endpoint


# ---------------- 预览/缩略图 ----------------
def test_preview_inline_and_range(client):
    c, _ = client
    login(c)
    r = c.get('/preview/ascii.txt')
    assert r.status_code == 200
    assert 'attachment' not in (r.headers.get('Content-Disposition') or '')

    r = c.get('/preview/ascii.txt', headers={'Range': 'bytes=2-4'})
    assert r.status_code == 206
    assert r.get_data() == b'CDE'


def test_thumb_generates_and_caches(client):
    c, _ = client
    login(c)
    import os
    r = c.get('/thumb/pic.png')
    assert r.status_code == 200
    assert r.headers['Content-Type'].startswith('image/png')
    assert len(os.listdir(m.THUMB_CACHE)) == 1
    assert c.get('/thumb/pic.png').status_code == 200
    assert len(os.listdir(m.THUMB_CACHE)) == 1  # 命中缓存
    assert c.get('/thumb/hello.txt').status_code == 404  # 非图片


# ---------------- 分享链接 ----------------
def test_share_flow_file(client):
    c, _ = client
    h = login(c)
    r = c.post('/share/create', headers=h,
               data=json.dumps({'path': 'hello.txt', 'hours': 1}),
               content_type='application/json')
    assert r.status_code == 200
    body = r.get_json()
    assert '/s/' in body['url']
    token = body['url'].rsplit('/', 1)[-1]

    anon = m.app.test_client()
    page = anon.get(f'/s/{token}')
    assert page.status_code == 200
    assert 'hello.txt' in page.get_data(as_text=True)

    dl = anon.get(f'/s/{token}/download')
    assert dl.status_code == 200
    assert dl.get_data() == '你好世界'.encode()
    assert 'attachment' in dl.headers['Content-Disposition']

    inline = anon.get(f'/s/{token}/download?inline=1')
    assert 'attachment' not in inline.headers['Content-Disposition']


def test_share_flow_folder_and_escape(client):
    c, _ = client
    h = login(c)
    r = c.post('/share/create', headers=h,
               data=json.dumps({'path': '子目录', 'hours': 24}),
               content_type='application/json')
    token = r.get_json()['url'].rsplit('/', 1)[-1]

    anon = m.app.test_client()
    page = anon.get(f'/s/{token}')
    assert page.status_code == 200
    assert 'inner.txt' in page.get_data(as_text=True)

    dl = anon.get(f'/s/{token}/download/inner.txt')
    assert dl.status_code == 200
    assert dl.get_data() == b'inner'

    # 分享子树越界被拦截
    assert anon.get(f'/s/{token}/download/..%2Fhello.txt').status_code == 404
    assert anon.get(f'/s/{token}/download/..%2F..%2Fconfig.py').status_code == 404


def test_share_expired(client):
    c, _ = client
    h = login(c)
    r = c.post('/share/create', headers=h,
               data=json.dumps({'path': 'hello.txt', 'hours': 1}),
               content_type='application/json')
    token = r.get_json()['url'].rsplit('/', 1)[-1]

    shares = json.load(open(m.SHARE_FILE, encoding='utf-8'))
    shares[token]['expires'] = time.time() - 10
    json.dump(shares, open(m.SHARE_FILE, 'w', encoding='utf-8'))

    assert m.app.test_client().get(f'/s/{token}').status_code == 404


def test_share_invalid_token(client):
    anon = m.app.test_client()
    assert anon.get('/s/not-exists').status_code == 404


# ---------------- 二维码 ----------------
def test_qr_requires_login(client):
    c, _ = client
    assert c.get('/qr?url=x').status_code == 401
    login(c)
    assert c.get('/qr?url=http://x/y').status_code == 200


# ---------------- Tailscale IP 检测 ----------------
class _FakeResult:
    def __init__(self, stdout):
        self.stdout = stdout


def test_tailscale_ip_via_cli(client, monkeypatch):
    """tailscale CLI 可用时直接返回其输出。"""
    monkeypatch.setattr(m.subprocess, 'run', lambda cmd, **kw: _FakeResult(b'100.101.22.33'))
    assert m._tailscale_ip() == '100.101.22.33'


def test_tailscale_ip_via_ipconfig_fallback(client, monkeypatch):
    """CLI 都不可用时解析 ipconfig 中的 100.64.0.0/10 地址。"""
    def fake_run(cmd, **kw):
        if cmd[0] == 'ipconfig':
            return _FakeResult(b'   IPv4 Address . . . : 100.64.5.7')
        return _FakeResult(b'')
    monkeypatch.setattr(m.subprocess, 'run', fake_run)
    assert m._tailscale_ip() == '100.64.5.7'


def test_tailscale_ip_absent(client, monkeypatch):
    """没有 Tailscale 时静默返回 None,不抛异常。"""
    def fake_run(cmd, **kw):
        return _FakeResult('以太网适配器: 192.168.1.5'.encode('gbk'))
    monkeypatch.setattr(m.subprocess, 'run', fake_run)
    assert m._tailscale_ip() is None


def test_tailscale_ip_range_filter(client, monkeypatch):
    """只认 100.64-127 段,其他 100.x 忽略。"""
    def fake_run(cmd, **kw):
        if cmd[0] == 'ipconfig':
            return _FakeResult(b'100.10.1.1 100.200.1.1 100.100.7.8')
        return _FakeResult(b'')
    monkeypatch.setattr(m.subprocess, 'run', fake_run)
    assert m._tailscale_ip() == '100.100.7.8'


# ---------------- 多选打包下载 ----------------
def test_download_zip_mixed(client):
    c, shared = client
    h = login(c)
    r = c.post('/api/download-zip', headers=h,
               data=json.dumps({'paths': ['hello.txt', '子目录', 'pic.png']}),
               content_type='application/json')
    assert r.status_code == 200
    assert 'zip' in r.headers['Content-Type']
    import io
    import zipfile
    z = zipfile.ZipFile(io.BytesIO(r.get_data()))
    names = z.namelist()
    assert 'hello.txt' in names
    assert '子目录/inner.txt' in names
    assert 'pic.png' in names
    assert z.read('hello.txt') == '你好世界'.encode()
    assert z.read('子目录/inner.txt') == b'inner'


def test_download_zip_dedup(client):
    c, _ = client
    h = login(c)
    r = c.post('/api/download-zip', headers=h,
               data=json.dumps({'paths': ['hello.txt', 'hello.txt']}),
               content_type='application/json')
    import io
    import zipfile
    names = zipfile.ZipFile(io.BytesIO(r.get_data())).namelist()
    assert names.count('hello.txt') == 1
    assert 'hello (1).txt' in names


def test_download_zip_requires_selection(client):
    c, _ = client
    h = login(c)
    for payload in [{}, {'paths': []}]:
        r = c.post('/api/download-zip', headers=h, data=json.dumps(payload),
                   content_type='application/json')
        assert r.status_code == 400


def test_download_zip_traversal_skipped(client):
    c, _ = client
    h = login(c)
    r = c.post('/api/download-zip', headers=h,
               data=json.dumps({'paths': ['../config.py']}),
               content_type='application/json')
    assert r.status_code == 404


def test_download_zip_requires_login(client):
    c, _ = client
    r = c.post('/api/download-zip', data=json.dumps({'paths': ['hello.txt']}),
               content_type='application/json')
    assert r.status_code == 401


def test_download_zip_guest_allowed(client):
    c, _ = client
    h = login(c, username='guest', role='guest')
    r = c.post('/api/download-zip', headers=h,
               data=json.dumps({'paths': ['hello.txt']}),
               content_type='application/json')
    assert r.status_code == 200


# ---------------- 错误页 ----------------
def test_error_page_html(client):
    c, _ = client
    login(c)
    r = c.get('/不存在的目录xyz', headers={'Accept': 'text/html'})
    assert r.status_code == 404
    body = r.get_data(as_text=True)
    assert '返回主目录' in body
    assert '路径不存在' in body


def test_error_page_api_stays_json(client):
    c, _ = client
    login(c)
    r = c.get('/details/不存在.txt', headers={'Accept': 'application/json'})
    assert r.status_code == 404
    assert r.get_json()['error'] == 'File not found'


def test_error_page_traversal_still_400(client):
    c, _ = client
    login(c)
    r = c.get('/..%2F..', headers={'Accept': 'text/html'})
    assert r.status_code == 400
