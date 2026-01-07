import os
import io
import time
from flask import Flask, render_template, request, send_from_directory, redirect, url_for, session, flash, jsonify, send_file
from werkzeug.utils import secure_filename
import qrcode
from qrcode.image.pil import PilImage

app = Flask(__name__)
UPLOAD_FOLDER = 'uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['SECRET_KEY'] = 'your_secure_secret_key_here_change_in_production' # 生产环境请务必修改为更安全的密钥

# 用于演示的硬编码用户
USERS = {
    'admin': 'password'
}

def get_file_size(path):
    size = os.path.getsize(path)
    if size < 1024:
        return f"{size} B"
    elif size < 1024**2:
        return f"{size/1024:.2f} KB"
    elif size < 1024**3:
        return f"{size/1024**2:.2f} MB"
    else:
        return f"{size/1024**3:.2f} GB"

def get_folder_details(path):
    total_size = 0
    file_count = 0
    folder_count = 0
    for dirpath, dirnames, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            if not os.path.islink(fp):
                total_size += os.path.getsize(fp)
        file_count += len(filenames)
        folder_count += len(dirnames)
    
    if total_size < 1024:
        size_str = f"{total_size} B"
    elif total_size < 1024**2:
        size_str = f"{total_size/1024:.2f} KB"
    elif total_size < 1024**3:
        size_str = f"{total_size/1024**2:.2f} MB"
    else:
        size_str = f"{total_size/1024**3:.2f} GB"

    return size_str, file_count, folder_count

def get_directory_tree(path, root_path):
    tree = []
    try:
        for item in sorted(os.listdir(path)):
            item_path = os.path.join(path, item)
            if os.path.isdir(item_path):
                rel_path = os.path.relpath(item_path, root_path).replace('\\', '/')
                node = {
                    'name': item,
                    'path': rel_path,
                    'children': get_directory_tree(item_path, root_path)
                }
                tree.append(node)
    except OSError:
        pass # Ignore permission errors
    return tree


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if USERS.get(username) == password:
            session['logged_in'] = True
            session['username'] = username
            flash('登录成功!')
            return redirect(url_for('index'))
        else:
            flash('错误的用户名或密码')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    session.pop('username', None)
    flash('您已成功登出')
    return redirect(url_for('login'))


@app.route('/')
@app.route('/<path:subpath>')
def index(subpath=''):
    if not session.get('logged_in'):
        return redirect(url_for('login'))
        
    upload_root = app.config['UPLOAD_FOLDER']
    base_path = os.path.join(upload_root, subpath)

    if not os.path.exists(base_path):
        return "路径不存在", 404

    if os.path.isfile(base_path):
        return redirect(url_for('index', subpath=os.path.dirname(subpath)))

    # Generate file list for the current directory
    items = []
    for item in sorted(os.listdir(base_path)):
        item_path = os.path.join(base_path, item)
        is_folder = os.path.isdir(item_path)
        
        try:
            mtime = os.path.getmtime(item_path)
            size = get_file_size(item_path) if not is_folder else ''
        except OSError:
            mtime = 0
            size = 'N/A'

        url_path = os.path.join(subpath, item).replace('\\', '/')
        items.append({
            'name': item, 
            'is_folder': is_folder, 
            'path': url_path,
            'mtime': time.strftime('%Y-%m-%d %H:%M', time.localtime(mtime)),
            'size': size
        })
    
    # Generate the full directory tree for the sidebar
    dir_tree = get_directory_tree(upload_root, upload_root)

    return render_template('index.html', items=items, current_path=subpath, dir_tree=dir_tree)

@app.route('/details/<path:filepath>')
def get_details(filepath):
    if not session.get('logged_in'):
        return jsonify({'error': 'Unauthorized'}), 401

    full_path = os.path.join(app.config['UPLOAD_FOLDER'], filepath)
    if not os.path.exists(full_path):
        return jsonify({'error': 'File not found'}), 404

    is_folder = os.path.isdir(full_path)
    mtime = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(os.path.getmtime(full_path)))
    
    if is_folder:
        size, file_count, folder_count = get_folder_details(full_path)
        details = {
            'name': os.path.basename(filepath),
            'is_folder': True,
            'mtime': mtime,
            'size': size,
            'file_count': file_count,
            'folder_count': folder_count,
            'qr_url': url_for('qr_code', url=request.host_url + filepath, _external=True)
        }
    else:
        details = {
            'name': os.path.basename(filepath),
            'is_folder': False,
            'mtime': mtime,
            'size': get_file_size(full_path),
            'qr_url': url_for('qr_code', url=url_for('download_file_route', filepath=filepath, _external=True))
        }
    return jsonify(details)

@app.route('/qr')
def qr_code():
    url = request.args.get('url', '')
    if not url:
        return "No URL provided", 400
    
    img_buf = io.BytesIO()
    img = qrcode.make(url, image_factory=PilImage)
    img.save(img_buf, 'PNG')
    img_buf.seek(0)
    
    return send_file(img_buf, mimetype='image/png')


@app.route('/download/<path:filepath>')
def download_file_route(filepath):
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    return send_from_directory(app.config['UPLOAD_FOLDER'], filepath, as_attachment=True)

@app.route('/upload', methods=['POST'])
def upload_file():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    subpath = request.args.get('subpath', '')
    if 'file' not in request.files:
        return redirect(request.url)
    file = request.files['file']
    if file.filename == '':
        return redirect(request.url)
    if file:
        filename = secure_filename(file.filename)
        upload_path = os.path.join(app.config['UPLOAD_FOLDER'], subpath)
        file.save(os.path.join(upload_path, filename))
        return redirect(url_for('index', subpath=subpath))
        
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False) 