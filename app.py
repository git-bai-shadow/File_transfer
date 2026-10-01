# -*- coding: utf-8 -*-
"""局域网文件共享系统

浏览/上传/下载/预览/管理/临时分享,配置见 config.py。
"""
import hashlib
import io
import json
import logging
import mimetypes
import os
import re
import secrets
import socket
import subprocess
import tempfile
import threading
import time
import webbrowser
import zipfile
from logging.handlers import RotatingFileHandler

from flask import (Flask, abort, flash, jsonify, redirect, render_template,
                   request, send_file, send_from_directory, session, url_for)
import qrcode
from qrcode.image.pil import PilImage
from send2trash import send2trash
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash
from werkzeug.utils import safe_join

import config

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- 日志
logger = logging.getLogger('lan-share')
_handler = RotatingFileHandler(os.path.join(BASE_DIR, 'app.log'),
                               maxBytes=1_000_000, backupCount=3, encoding='utf-8')
_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
logger.addHandler(_handler)
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------- 应用与配置
app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = config.SHARED_DIR
app.config['MAX_CONTENT_LENGTH'] = config.MAX_CONTENT_LENGTH
# 静态资源强缓存一年(改动 CSS/JS 后需浏览器强刷一次)
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 31536000

# SECRET_KEY 首次启动自动生成并保存到本地文件,避免使用公开占位符
_SECRET_FILE = os.path.join(BASE_DIR, '.secret_key')
try:
    with open(_SECRET_FILE, encoding='utf-8') as _f:
        _secret_key = _f.read().strip()
except FileNotFoundError:
    _secret_key = secrets.token_hex(32)
    with open(_SECRET_FILE, 'w', encoding='utf-8') as _f:
        _f.write(_secret_key)
app.config['SECRET_KEY'] = _secret_key

# 用户:{用户名: {hash, role}},密码为哈希,见 config.py 说明
USERS = {name: {'hash': pwd_hash, 'role': role} for name, pwd_hash, role in config.USERS}

PAGE_SIZE = config.PAGE_SIZE
SHARE_FILE = os.path.join(BASE_DIR, 'share_links.json')
THUMB_CACHE = os.path.join(BASE_DIR, 'thumb_cache')
THUMB_SIZE = config.THUMB_SIZE

# ---------------------------------------------------------------- 工具函数
ICON_MAP = [
    (('mp4', 'mkv', 'avi', 'mov', 'wmv', 'flv', 'webm', 'm4v', 'ts', 'rmvb'), '🎬'),
    (('mp3', 'wav', 'flac', 'aac', 'm4a', 'ogg', 'wma'), '🎵'),
    (('jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'svg', 'ico', 'heic'), '🖼️'),
    (('zip', 'rar', '7z', 'tar', 'gz', 'bz2', 'xz'), '📦'),
    (('doc', 'docx', 'pdf', 'txt', 'md', 'rtf', 'xls', 'xlsx', 'ppt', 'pptx', 'csv'), '📝'),
    (('py', 'js', 'html', 'css', 'c', 'cpp', 'h', 'java', 'json', 'xml', 'yml', 'yaml', 'sh', 'bat'), '📜'),
    (('exe', 'msi', 'apk', 'cmd'), '⚙️'),
    (('ttf', 'otf', 'woff', 'woff2'), '🔤'),
    (('iso', 'img', 'vhd'), '💿'),
]


def file_icon(name):
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    for exts, icon in ICON_MAP:
        if ext in exts:
            return icon
    return '📄'


# 浏览器可内嵌预览的类型;MIME_OVERRIDES 补上常见格式的猜测缺失
PREVIEW_EXTS = {
    'video': ('mp4', 'webm', 'mkv', 'mov', 'm4v', 'ogv'),
    'audio': ('mp3', 'wav', 'ogg', 'm4a', 'flac', 'aac'),
    'image': ('jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'svg', 'ico'),
    'pdf': ('pdf',),
    'text': ('txt', 'md', 'csv', 'log', 'json', 'xml', 'yml', 'yaml', 'py', 'js',
             'html', 'css', 'c', 'cpp', 'h', 'java', 'bat', 'sh', 'ini', 'conf'),
}
MIME_OVERRIDES = {
    'mkv': 'video/x-matroska',
    'm4v': 'video/mp4',
    'flac': 'audio/flac',
    'm4a': 'audio/mp4',
}


def preview_kind(name):
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    for kind, exts in PREVIEW_EXTS.items():
        if ext in exts:
            return kind
    return None


def preview_mimetype(name):
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    if ext in MIME_OVERRIDES:
        return MIME_OVERRIDES[ext]
    return mimetypes.guess_type(name)[0] or 'application/octet-stream'


def get_safe_path(subpath):
    # 解析子路径并确保结果位于共享目录内,防止 .. 等形式路径穿越
    return safe_join(app.config['UPLOAD_FOLDER'], subpath)


def sanitize_filename(filename):
    # 清理上传文件名:保留中文等 Unicode 字符,只处理路径分隔符和 Windows 非法字符
    name = os.path.basename(filename).strip()
    name = re.sub('[<>:"|?*' + chr(0) + '-' + chr(31) + ']', '_', name)
    name = name.rstrip('. ')
    stem = name.split('.')[0].upper()
    if stem in {'CON', 'PRN', 'AUX', 'NUL'} | {f'COM{i}' for i in range(1, 10)} | {f'LPT{i}' for i in range(1, 10)}:
        name = '_' + name
    return name or 'unnamed_file'


def unique_path(directory, name):
    # 同名文件自动追加序号,避免静默覆盖
    base, ext = os.path.splitext(name)
    candidate = name
    i = 1
    while os.path.exists(os.path.join(directory, candidate)):
        candidate = f'{base} ({i}){ext}'
        i += 1
    return os.path.join(directory, candidate)


def human_size(size):
    if size < 1024:
        return f'{size} B'
    elif size < 1024**2:
        return f'{size/1024:.2f} KB'
    elif size < 1024**3:
        return f'{size/1024**2:.2f} MB'
    else:
        return f'{size/1024**3:.2f} GB'


def get_file_size(path):
    return human_size(os.path.getsize(path))


_folder_details_cache = {}  # path -> (缓存时间, 结果);大文件夹统计走 60 秒缓存


def get_folder_details(path):
    cached = _folder_details_cache.get(path)
    if cached and time.time() - cached[0] < 60:
        return cached[1]
    total_size = 0
    file_count = 0
    folder_count = 0
    for dirpath, dirnames, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            if not os.path.islink(fp):
                try:
                    total_size += os.path.getsize(fp)
                except OSError:
                    pass
        file_count += len(filenames)
        folder_count += len(dirnames)
    result = (human_size(total_size), file_count, folder_count)
    _folder_details_cache[path] = (time.time(), result)
    return result


def list_dir_items(base_path, subpath):
    """列出目录内容,文件夹在前,按名称排序。"""
    items = []
    for item in sorted(os.listdir(base_path), key=str.lower):
        item_path = os.path.join(base_path, item)
        is_folder = os.path.isdir(item_path)
        try:
            mtime_ts = os.path.getmtime(item_path)
            size_bytes = 0 if is_folder else os.path.getsize(item_path)
        except OSError:
            mtime_ts, size_bytes = 0, -1
        url_path = os.path.join(subpath, item).replace(os.sep, '/')
        items.append({
            'name': item,
            'is_folder': is_folder,
            'path': url_path,
            'icon': '📁' if is_folder else file_icon(item),
            'preview': None if is_folder else preview_kind(item),
            'mtime': time.strftime('%Y-%m-%d %H:%M', time.localtime(mtime_ts)) if mtime_ts else 'N/A',
            'mtime_ts': int(mtime_ts),
            'size': '' if is_folder else ('N/A' if size_bytes < 0 else human_size(size_bytes)),
            'size_bytes': size_bytes,
        })
    items.sort(key=lambda x: (not x['is_folder'], x['name'].lower()))
    return items


def is_admin():
    return session.get('role') == 'admin'


def wants_json():
    return (request.headers.get('X-Requested-With') == 'XMLHttpRequest'
            or 'application/json' in request.headers.get('Accept', ''))


# ---------------------------------------------------------------- 静态资源版本号
@app.template_global()
def asset_url(filename):
    # 以文件修改时间作为版本参数,文件一更新浏览器缓存自动失效
    try:
        v = int(os.stat(os.path.join(app.static_folder, filename)).st_mtime)
    except OSError:
        v = 0
    return url_for('static', filename=filename, v=v)


# ---------------------------------------------------------------- CSRF
@app.before_request
def csrf_protect():
    # 已登录用户的所有 POST 都必须携带正确 token;登录与登出除外(登出是 GET,
    # 登录页可能被已登录用户再次提交,如换账号场景)
    if request.method == 'POST' and session.get('logged_in') and request.endpoint not in ('login', 'logout'):
        token = request.headers.get('X-CSRF-Token') or request.form.get('csrf_token')
        if not token or token != session.get('csrf_token'):
            logger.warning('CSRF 校验失败: %s %s', session.get('username'), request.path)
            abort(403, description='CSRF 校验失败,请刷新页面重试')


# ---------------------------------------------------------------- 登录/登出
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET' and session.get('logged_in'):
        return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        user = USERS.get(username)
        if user and check_password_hash(user['hash'], password):
            session['logged_in'] = True
            session['username'] = username
            session['role'] = user['role']
            session['csrf_token'] = secrets.token_hex(16)
            flash('登录成功!', 'success')
            logger.info('登录成功: %s (%s) 来自 %s', username, user['role'], request.remote_addr)
            return redirect(url_for('index'))
        flash('错误的用户名或密码', 'error')
        logger.warning('登录失败: %r 来自 %s', username, request.remote_addr)
    return render_template('login.html')


@app.route('/logout')
def logout():
    logger.info('登出: %s', session.get('username'))
    session.pop('logged_in', None)
    session.pop('username', None)
    session.pop('role', None)
    flash('您已成功登出', 'success')
    return redirect(url_for('login'))


# ---------------------------------------------------------------- 浏览
@app.route('/')
@app.route('/<path:subpath>')
def index(subpath=''):
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    base_path = get_safe_path(subpath)
    if base_path is None:
        abort(400, description='非法路径')
    if not os.path.exists(base_path):
        abort(404, description='路径不存在')
    if os.path.isfile(base_path):
        return redirect(url_for('index', subpath=os.path.dirname(subpath)))

    all_items = list_dir_items(base_path, subpath)
    total = len(all_items)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    try:
        page = int(request.args.get('page', 1))
    except ValueError:
        page = 1
    page = min(max(1, page), pages)
    items = all_items[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]

    return render_template(
        'index.html',
        items=items, current_path=subpath,
        page=page, pages=pages, total=total,
        share_hours=config.SHARE_HOURS_OPTIONS,
        share_default=config.SHARE_DEFAULT_HOURS,
    )


# ---------------------------------------------------------------- 详情/二维码
@app.route('/details/<path:filepath>')
def get_details(filepath):
    if not session.get('logged_in'):
        return jsonify({'error': 'Unauthorized'}), 401

    full_path = get_safe_path(filepath)
    if full_path is None:
        return jsonify({'error': '非法路径'}), 400
    if not os.path.exists(full_path):
        return jsonify({'error': 'File not found'}), 404

    is_folder = os.path.isdir(full_path)
    name = os.path.basename(filepath)
    mtime = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(os.path.getmtime(full_path)))
    details = {
        'name': name,
        'icon': '📁' if is_folder else file_icon(name),
        'is_folder': is_folder,
        'mtime': mtime,
    }
    if is_folder:
        size, file_count, folder_count = get_folder_details(full_path)
        details.update({
            'size': size,
            'file_count': file_count,
            'folder_count': folder_count,
            'qr_url': url_for('qr_code', url=url_for('index', subpath=filepath, _external=True), _external=True),
        })
    else:
        details.update({
            'size': get_file_size(full_path),
            'preview': preview_kind(name),
            'qr_url': url_for('qr_code', url=url_for('download_file_route', filepath=filepath, _external=True), _external=True),
        })
    return jsonify(details)


@app.route('/qr')
def qr_code():
    if not session.get('logged_in'):
        return 'Unauthorized', 401
    url = request.args.get('url', '')
    if not url:
        return 'No URL provided', 400
    img_buf = io.BytesIO()
    img = qrcode.make(url, image_factory=PilImage)
    img.save(img_buf, 'PNG')
    img_buf.seek(0)
    return send_file(img_buf, mimetype='image/png')


# ---------------------------------------------------------------- 下载/预览/缩略图
@app.route('/download/<path:filepath>')
def download_file_route(filepath):
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    logger.info('下载: %s (%s)', filepath, session.get('username'))
    return send_from_directory(app.config['UPLOAD_FOLDER'], filepath, as_attachment=True)


@app.route('/preview/<path:filepath>')
def preview(filepath):
    if not session.get('logged_in'):
        return 'Unauthorized', 401
    full_path = get_safe_path(filepath)
    if full_path is None or not os.path.isfile(full_path):
        abort(404)
    # conditional=True 让 werkzeug 处理 Range 请求,视频/音频可拖进度条
    return send_file(full_path, mimetype=preview_mimetype(filepath), conditional=True)


_IMAGE_EXTS = ('jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp')


@app.route('/thumb/<path:filepath>')
def thumb(filepath):
    if not session.get('logged_in'):
        return 'Unauthorized', 401
    from PIL import Image
    full_path = get_safe_path(filepath)
    ext = filepath.rsplit('.', 1)[-1].lower() if '.' in filepath else ''
    if full_path is None or not os.path.isfile(full_path) or ext not in _IMAGE_EXTS:
        abort(404)

    os.makedirs(THUMB_CACHE, exist_ok=True)
    key = hashlib.md5(
        (os.path.abspath(full_path) + '|' + str(os.path.getmtime(full_path))).encode('utf-8')
    ).hexdigest() + '.png'
    cache_path = os.path.join(THUMB_CACHE, key)
    if not os.path.exists(cache_path):
        img = Image.open(full_path)
        img.thumbnail((THUMB_SIZE, THUMB_SIZE))
        if img.mode not in ('RGB', 'RGBA'):
            img = img.convert('RGB')
        tmp_path = cache_path + '.' + str(os.getpid()) + '-' + str(threading.get_ident()) + '.tmp'
        img.save(tmp_path, 'PNG')
        os.replace(tmp_path, cache_path)
    return send_file(cache_path, mimetype='image/png', conditional=True)


# ---------------------------------------------------------------- 上传
@app.route('/upload', methods=['POST'])
def upload_file():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    if not is_admin():
        if wants_json():
            return jsonify({'error': '访客账号不能上传文件'}), 403
        flash('访客账号不能上传文件', 'error')
        return redirect(url_for('index', subpath=request.args.get('subpath', '')))

    subpath = request.args.get('subpath', '')
    upload_path = get_safe_path(subpath)
    if upload_path is None:
        if wants_json():
            return jsonify({'error': '非法路径'}), 400
        flash('非法路径,上传失败', 'error')
        return redirect(url_for('index'))
    if not os.path.isdir(upload_path):
        if wants_json():
            return jsonify({'error': '目标目录不存在'}), 400
        flash('目标目录不存在,上传失败', 'error')
        return redirect(url_for('index', subpath=subpath))

    files = request.files.getlist('file')
    if not files:
        if wants_json():
            return jsonify({'error': '未选择文件'}), 400
        flash('未选择文件,上传失败', 'error')
        return redirect(url_for('index', subpath=subpath))

    saved, failed = 0, []
    for f in files:
        name = sanitize_filename(f.filename)
        try:
            f.save(unique_path(upload_path, name))
            saved += 1
        except OSError:
            failed.append(name)
    logger.info('上传: %d 个文件到 /%s (%s), 失败 %d', saved, subpath, session.get('username'), len(failed))

    if saved:
        flash(f'成功上传 {saved} 个文件到 /{subpath}', 'success')
    if failed:
        flash('以下文件保存失败: ' + ', '.join(failed), 'error')
    if wants_json():
        return jsonify({'saved': saved, 'failed': failed})
    return redirect(url_for('index', subpath=subpath))


# ---------------------------------------------------------------- 管理 API
def _api_error(message, status):
    return jsonify({'error': message}), status


@app.route('/api/mkdir', methods=['POST'])
def api_mkdir():
    if not session.get('logged_in'):
        return _api_error('Unauthorized', 401)
    if not is_admin():
        return _api_error('访客账号不能执行此操作', 403)
    data = request.get_json(silent=True) or {}
    parent = data.get('parent', '')
    parent_path = get_safe_path(parent)
    if parent_path is None or not os.path.isdir(parent_path):
        return _api_error('目标目录不存在', 404)
    name = sanitize_filename(data.get('name', ''))
    target = os.path.join(parent_path, name)
    if os.path.exists(target):
        return _api_error('同名文件或文件夹已存在', 409)
    try:
        os.mkdir(target)
    except OSError as e:
        return _api_error(f'创建失败: {e}', 500)
    logger.info('新建文件夹: /%s/%s (%s)', parent, name, session.get('username'))
    return jsonify({'ok': True})


@app.route('/api/rename', methods=['POST'])
def api_rename():
    if not session.get('logged_in'):
        return _api_error('Unauthorized', 401)
    if not is_admin():
        return _api_error('访客账号不能执行此操作', 403)
    data = request.get_json(silent=True) or {}
    path = get_safe_path(data.get('path', ''))
    if path is None or not os.path.exists(path):
        return _api_error('路径不存在', 404)
    if os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(app.config['UPLOAD_FOLDER'])):
        return _api_error('不能重命名共享根目录', 400)
    name = sanitize_filename(data.get('new_name', ''))
    target = os.path.join(os.path.dirname(path), name)
    if os.path.exists(target):
        return _api_error('同名文件或文件夹已存在', 409)
    try:
        os.rename(path, target)
    except OSError as e:
        return _api_error(f'重命名失败: {e}', 500)
    logger.info('重命名: %s -> %s (%s)', data.get('path'), name, session.get('username'))
    return jsonify({'ok': True})


@app.route('/api/delete', methods=['POST'])
def api_delete():
    if not session.get('logged_in'):
        return _api_error('Unauthorized', 401)
    if not is_admin():
        return _api_error('访客账号不能执行此操作', 403)
    data = request.get_json(silent=True) or {}
    path = get_safe_path(data.get('path', ''))
    if path is None or not os.path.exists(path):
        return _api_error('路径不存在', 404)
    if os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(app.config['UPLOAD_FOLDER'])):
        return _api_error('不能删除共享根目录', 400)
    try:
        # send2trash 走系统 Shell 接口,不接受 safe_join 生成的正斜杠路径,必须先规范化
        send2trash(os.path.normpath(path))  # 移入回收站,误删可恢复
    except Exception as e:
        return _api_error(f'删除失败: {e}', 500)
    logger.info('删除(回收站): %s (%s)', data.get('path'), session.get('username'))
    return jsonify({'ok': True})


# ---------------------------------------------------------------- 多选打包下载
def _dedup_name(name, used):
    # zip 包内重名自动加序号
    if name not in used:
        used.add(name)
        return name
    base, ext = os.path.splitext(name)
    i = 1
    while True:
        candidate = f'{base} ({i}){ext}'
        if candidate not in used:
            used.add(candidate)
            return candidate
        i += 1


_STORED_EXTS = ('mp4', 'mkv', 'avi', 'mov', 'wmv', 'flv', 'webm', 'm4v', 'ts', 'rmvb',
                'mp3', 'wav', 'flac', 'aac', 'm4a', 'ogg', 'wma',
                'jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'heic', 'svg', 'ico',
                'zip', 'rar', '7z', 'gz', 'bz2', 'xz', 'iso', 'img', 'vhd',
                'apk', 'exe', 'msi', 'ttf', 'otf', 'woff', 'woff2')


def _zip_compression(name):
    # 媒体/已压缩内容直接存储(省 CPU 且更快),文档等文本类才 deflate
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    return zipfile.ZIP_STORED if ext in _STORED_EXTS else zipfile.ZIP_DEFLATED


def _remove_later(path, delay=900):
    # 打包临时文件延迟删除(守护线程,下载完成后回收)
    def _remove():
        try:
            os.remove(path)
        except OSError:
            pass
    timer = threading.Timer(delay, _remove)
    timer.daemon = True
    timer.start()


@app.route('/api/download-zip', methods=['POST'])
def api_download_zip():
    if not session.get('logged_in'):
        return _api_error('Unauthorized', 401)
    data = request.get_json(silent=True) or {}
    raw_paths = data.get('paths', [])
    if not isinstance(raw_paths, list) or not raw_paths:
        return _api_error('未选择要下载的文件', 400)
    if len(raw_paths) > 200:
        return _api_error('一次最多打包 200 项', 400)

    resolved = []
    for raw in raw_paths:
        path = get_safe_path(str(raw))
        if path and os.path.exists(path):
            resolved.append(path)
    if not resolved:
        return _api_error('所选文件不存在或不可访问', 404)

    fd, temp_path = tempfile.mkstemp(suffix='.zip', prefix='lan_share_')
    os.close(fd)
    try:
        with zipfile.ZipFile(temp_path, 'w', allowZip64=True) as zf:
            used = set()
            for path in resolved:
                base = os.path.basename(path) or '共享根目录'
                if os.path.isdir(path):
                    for dirpath, dirnames, filenames in os.walk(path):
                        rel = os.path.relpath(dirpath, path)
                        prefix = base if rel == '.' else base + '/' + rel.replace(os.sep, '/')
                        if rel != '.' and not filenames and not dirnames:
                            zf.writestr(_dedup_name(prefix + '/', used), '')
                        for fn in filenames:
                            arcname = _dedup_name(prefix + '/' + fn, used)
                            try:
                                zf.write(os.path.join(dirpath, fn), arcname,
                                         compress_type=_zip_compression(fn))
                            except OSError:
                                continue
                else:
                    arcname = _dedup_name(base, used)
                    try:
                        zf.write(path, arcname, compress_type=_zip_compression(base))
                    except OSError:
                        continue
    except Exception:
        _remove_later(temp_path, 5)
        raise
    logger.info('打包下载: %d 项 (%s)', len(resolved), session.get('username'))
    _remove_later(temp_path)
    return send_file(temp_path, mimetype='application/zip', as_attachment=True,
                     download_name='共享文件_' + time.strftime('%Y%m%d-%H%M%S') + '.zip')


# ---------------------------------------------------------------- 临时分享
def load_shares():
    try:
        with open(SHARE_FILE, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def save_shares(shares):
    tmp = SHARE_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(shares, f, ensure_ascii=False, indent=1)
    os.replace(tmp, SHARE_FILE)


def purge_expired_shares(shares):
    now = time.time()
    expired = [t for t, e in shares.items() if e.get('expires', 0) < now]
    for t in expired:
        logger.info('分享链接过期: %s', t)
        del shares[t]
    return expired


@app.route('/share/create', methods=['POST'])
def share_create():
    if not session.get('logged_in'):
        return _api_error('Unauthorized', 401)
    if not is_admin():
        return _api_error('访客账号不能创建分享', 403)
    data = request.get_json(silent=True) or {}
    path = get_safe_path(data.get('path', ''))
    if path is None or not os.path.exists(path):
        return _api_error('路径不存在', 404)
    try:
        hours = int(data.get('hours', config.SHARE_DEFAULT_HOURS))
    except (TypeError, ValueError):
        hours = config.SHARE_DEFAULT_HOURS
    if hours not in config.SHARE_HOURS_OPTIONS:
        hours = config.SHARE_DEFAULT_HOURS

    shares = load_shares()
    purge_expired_shares(shares)
    token = secrets.token_urlsafe(16)
    expires = time.time() + hours * 3600
    shares[token] = {
        'path': os.path.abspath(path),
        'expires': expires,
        'created_by': session.get('username'),
    }
    save_shares(shares)
    logger.info('创建分享: %s (%d 小时, %s)', data.get('path'), hours, session.get('username'))
    return jsonify({
        'url': url_for('shared_view', token=token, _external=True),
        'expires_at': time.strftime('%Y-%m-%d %H:%M', time.localtime(expires)),
    })


def get_share_entry(token):
    shares = load_shares()
    if purge_expired_shares(shares):
        save_shares(shares)  # 有过期清理才落盘
    entry = shares.get(token)
    if not entry or entry.get('expires', 0) < time.time():
        return None
    return entry


@app.route('/s/<token>')
def shared_view(token):
    entry = get_share_entry(token)
    if entry is None:
        return render_template('share.html', error='分享链接不存在或已过期'), 404
    shared_path = entry['path']
    if os.path.isfile(shared_path):
        name = os.path.basename(shared_path)
        return render_template(
            'share.html', token=token, is_folder=False,
            name=name, icon=file_icon(name), kind=preview_kind(name),
            size=human_size(os.path.getsize(shared_path)),
            mtime=time.strftime('%Y-%m-%d %H:%M', time.localtime(os.path.getmtime(shared_path))),
        )
    # 文件夹分享:支持在分享目录子树内浏览
    sub = request.args.get('sub', '')
    base = safe_join(shared_path, sub)
    if base is None or not os.path.isdir(base):
        return render_template('share.html', error='路径不存在'), 404
    items = []
    for item in sorted(os.listdir(base), key=str.lower):
        p = os.path.join(base, item)
        is_folder = os.path.isdir(p)
        sub_url = (sub + '/' + item).replace(os.sep, '/') if sub else item
        items.append({
            'name': item,
            'icon': '📁' if is_folder else file_icon(item),
            'is_folder': is_folder,
            'sub': sub_url,
            'size': '' if is_folder else get_file_size(p),
            'mtime': time.strftime('%Y-%m-%d %H:%M', time.localtime(os.path.getmtime(p))),
        })
    items.sort(key=lambda x: (not x['is_folder'], x['name'].lower()))
    crumbs = []
    acc = []
    for c in [c for c in sub.split('/') if c]:
        acc.append(c)
        crumbs.append((c, '/'.join(acc)))
    return render_template('share.html', token=token, is_folder=True,
                           name=os.path.basename(shared_path) or '共享文件夹',
                           items=items, crumbs=crumbs, current_sub=sub)


@app.route('/s/<token>/download', defaults={'rel': ''})
@app.route('/s/<token>/download/<path:rel>')
def shared_download(token, rel):
    entry = get_share_entry(token)
    if entry is None:
        abort(404)
    # rel 为空时直接取分享的文件本身;safe_join(base, '') 会产生尾部斜杠导致 isfile 失败
    target = safe_join(entry['path'], rel) if rel else entry['path']
    if target is None or not os.path.isfile(target):
        abort(404)
    logger.info('分享下载: %s (%s)', rel or entry['path'], request.remote_addr)
    inline = request.args.get('inline') == '1'
    return send_file(target, mimetype=preview_mimetype(target),
                     as_attachment=not inline,
                     download_name=os.path.basename(target),
                     conditional=True)


# ---------------------------------------------------------------- 错误处理
@app.errorhandler(Exception)
def handle_exception(e):
    if isinstance(e, HTTPException):
        if e.code == 413 and wants_json():
            return jsonify({'error': '上传内容超过大小限制'}), 413
        # 浏览器导航的 400/404 渲染友好错误页;API/fetch 保持原响应
        if (e.code in (400, 404) and 'text/html' in request.headers.get('Accept', '')
                and not wants_json()):
            titles = {400: '请求无效', 404: '页面不存在'}
            desc = e.description if isinstance(e.description, str) else ''
            message = desc if 0 < len(desc) <= 30 else titles.get(e.code, '出错了')
            return render_template('error.html', code=e.code, message=message), e.code
        return e
    logger.exception('未处理异常: %s %s', request.method, request.path)
    if wants_json():
        return jsonify({'error': '服务器内部错误'}), 500
    return '服务器内部错误', 500


# ---------------------------------------------------------------- 启动
def _tailscale_ip():
    # 检测 Tailscale 虚拟 IP(100.64.0.0/10 段);未安装时静默返回 None
    for cmd in (['tailscale', 'ip', '-4'],
                ['C:/Program Files/Tailscale/tailscale.exe', 'ip', '-4']):
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=2)
            stdout = result.stdout.decode('utf-8', errors='ignore')
            ip = stdout.strip().splitlines()[0].strip() if stdout.strip() else ''
            if ip.startswith('100.'):
                return ip
        except (OSError, subprocess.TimeoutExpired, IndexError):
            continue
    try:
        # ipconfig 在中文 Windows 输出 GBK,手动容错解码(IP 本身是 ASCII)
        proc = subprocess.run(['ipconfig'], capture_output=True, timeout=2)
        output = proc.stdout.decode('utf-8', errors='ignore')
        for token in output.replace('(', ' ').replace(')', ' ').split():
            parts = token.strip('.').split('.')
            if (len(parts) == 4 and parts[0] == '100'
                    and all(p.isdigit() for p in parts)
                    and 64 <= int(parts[1]) <= 127):
                return token.strip('.')
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def lan_addresses(port):
    """枚举本机局域网 IPv4 地址与 Tailscale 虚拟 IP,返回 (地址, 标注) 列表。"""
    ips = {'127.0.0.1'}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith('169.254.'):
                ips.add(ip)
    except OSError:
        pass
    ts_ip = _tailscale_ip()
    if ts_ip:
        ips.add(ts_ip)
    result = []
    for ip in sorted(ips):
        note = '  <- Tailscale,任何网络可访问' if ip == ts_ip else ''
        result.append((f'http://{ip}:{port}', note))
    return result


if __name__ == '__main__':
    port = config.PORT
    print('=' * 46)
    print('  局域网文件共享系统已启动')
    for addr, note in lan_addresses(port):
        print(f'  访问地址: {addr}{note}')
    print('  按 Ctrl+C 停止服务')
    print('=' * 46)
    threading.Timer(1.0, webbrowser.open, (f'http://127.0.0.1:{port}',)).start()
    from waitress import serve
    serve(app, host='0.0.0.0', port=port, threads=8)
