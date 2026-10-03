/* 局域网文件共享系统 - 前端逻辑 */
document.addEventListener('DOMContentLoaded', function () {
    const CSRF = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
    const ROLE = (document.querySelector('meta[name="user-role"]') || {}).content || '';
    const CURRENT_PATH = (document.querySelector('meta[name="current-path"]') || {}).content || '';
    const IS_ADMIN = ROLE === 'admin';
    const IS_MOBILE = () => window.matchMedia('(max-width: 860px)').matches;

    const detailsContent = document.getElementById('details-content');
    const detailsPane = document.getElementById('details-pane');
    const fileItems = document.querySelectorAll('.file-item');

    /* ---------- 工具 ---------- */
    function encPath(path) {
        return path.split('/').map(encodeURIComponent).join('/');
    }

    function postJSON(url, data) {
        return fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'X-CSRF-Token': CSRF
            },
            body: JSON.stringify(data)
        }).then(function (r) {
            return r.json().then(function (j) { return { ok: r.ok, status: r.status, body: j }; });
        });
    }

    function escapeHTML(s) {
        return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    function refresh() { location.reload(); }

    /* ---------- toast 浮层 ---------- */
    let toastWrap = null;
    function ensureToastWrap() {
        if (!toastWrap) {
            toastWrap = document.createElement('div');
            toastWrap.className = 'toast-wrap';
            document.body.appendChild(toastWrap);
        }
        return toastWrap;
    }

    function showToast(message, kind) {
        const toast = document.createElement('div');
        toast.className = 'toast ' + (kind || 'success');
        toast.textContent = message;
        ensureToastWrap().appendChild(toast);
        setTimeout(function () {
            toast.classList.add('toast-out');
            setTimeout(function () { toast.remove(); }, 300);
        }, 3500);
    }

    /* 服务端 flash 转为 toast */
    (function convertFlashes() {
        const box = document.querySelector('.flashes');
        if (!box) return;
        box.querySelectorAll('p[data-kind]').forEach(function (p) {
            showToast(p.textContent, p.dataset.kind);
        });
        box.remove();
    })();

    /* ---------- 通用模态框助手 ---------- */
    function openModalEl(el) { el.style.display = 'flex'; document.body.style.overflow = 'hidden'; }
    function closeModalEl(el) { el.style.display = 'none'; document.body.style.overflow = 'auto'; }

    function askText(title, defaultValue) {
        return new Promise(function (resolve) {
            const modal = document.getElementById('input-modal');
            const input = document.getElementById('input-value');
            const ok = document.getElementById('input-ok');
            const cancel = document.getElementById('input-cancel');
            const close = document.getElementById('input-close');
            document.getElementById('input-title').textContent = title;
            input.value = defaultValue || '';
            openModalEl(modal);
            setTimeout(function () { input.focus(); input.select(); }, 60);
            function done(value) {
                closeModalEl(modal);
                ok.removeEventListener('click', onOk);
                cancel.removeEventListener('click', onCancel);
                close.removeEventListener('click', onCancel);
                input.removeEventListener('keydown', onKey);
                resolve(value);
            }
            function onOk() { done(input.value.trim()); }
            function onCancel() { done(null); }
            function onKey(e) { if (e.key === 'Enter') onOk(); }
            ok.addEventListener('click', onOk);
            cancel.addEventListener('click', onCancel);
            close.addEventListener('click', onCancel);
            input.addEventListener('keydown', onKey);
        });
    }

    function askConfirm(message, okLabel) {
        return new Promise(function (resolve) {
            const modal = document.getElementById('confirm-modal');
            const ok = document.getElementById('confirm-ok');
            const cancel = document.getElementById('confirm-cancel');
            const close = document.getElementById('confirm-close');
            document.getElementById('confirm-text').textContent = message;
            ok.textContent = okLabel || '确定';
            openModalEl(modal);
            function done(v) {
                closeModalEl(modal);
                ok.removeEventListener('click', onOk);
                cancel.removeEventListener('click', onCancel);
                close.removeEventListener('click', onCancel);
                resolve(v);
            }
            function onOk() { done(true); }
            function onCancel() { done(false); }
            ok.addEventListener('click', onOk);
            cancel.addEventListener('click', onCancel);
            close.addEventListener('click', onCancel);
        });
    }

    /* ---------- 媒体灯箱 ---------- */
    const lightbox = document.getElementById('lightbox');
    const lightboxStage = document.getElementById('lightbox-stage');

    function openLightbox(html) {
        lightboxStage.innerHTML = html;
        lightbox.style.display = 'flex';
        document.body.style.overflow = 'hidden';
    }

    function closeLightbox() {
        lightbox.style.display = 'none';
        lightboxStage.innerHTML = '';
        document.body.style.overflow = 'auto';
    }

    document.getElementById('lightbox-close').addEventListener('click', closeLightbox);
    lightbox.addEventListener('click', function (e) {
        if (e.target === lightbox) closeLightbox();
    });

    /* ---------- 详情面板 ---------- */
    function previewHTML(kind, encodedPath) {
        if (!kind) return '';
        const src = '/preview/' + encodedPath;
        if (kind === 'video') {
            return '<div class="preview-media">' +
                '<video controls preload="metadata" src="' + src + '"></video>' +
                '<button class="preview-expand" data-src="' + src + '">⛶ 全屏播放</button>' +
                '<p class="preview-fallback" hidden>此格式浏览器无法直接播放,请下载后观看</p></div>';
        }
        if (kind === 'audio') {
            return '<div class="preview-media"><audio controls preload="metadata" src="' + src + '"></audio></div>';
        }
        if (kind === 'image') {
            return '<div class="preview-media"><img class="preview-zoomable" src="' + src + '" alt="预览"></div>';
        }
        if (kind === 'pdf') {
            return '<div class="preview-media"><iframe src="' + src + '"></iframe></div>';
        }
        if (kind === 'text') {
            return '<div class="preview-media text-preview" data-src="' + src + '"><p class="preview-fallback">加载预览...</p></div>';
        }
        return '';
    }

    function bindPreviewFallbacks(root) {
        root.querySelectorAll('.preview-media video, .preview-media audio').forEach(function (el) {
            el.addEventListener('error', function () {
                const tip = root.querySelector('.preview-fallback');
                if (tip) tip.hidden = false;
            });
        });
        root.querySelectorAll('.text-preview').forEach(function (box) {
            fetch(box.dataset.src)
                .then(function (r) { return r.text(); })
                .then(function (text) {
                    const truncated = text.length > 20000;
                    const pre = document.createElement('pre');
                    pre.textContent = truncated ? text.slice(0, 20000) + '\n... (内容过长已截断)' : text;
                    box.innerHTML = '';
                    box.appendChild(pre);
                })
                .catch(function () { box.innerHTML = '<p class="preview-fallback">预览加载失败</p>'; });
        });
    }

    function loadDetails(path) {
        const encodedPath = encPath(path);
        detailsContent.innerHTML = '<div class="loading shimmer detail-skeleton"></div>';

        fetch('/details/' + encodedPath)
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.error) {
                    detailsContent.innerHTML = '<div class="placeholder"><span class="placeholder-icon">⚠️</span><p>' +
                        escapeHTML(data.error) + '</p></div>';
                    return;
                }

                let html =
                    '<div class="details-header">' +
                    '<span class="dh-icon">' + data.icon + '</span>' +
                    '<div class="dh-info">' +
                    '<h3 class="dh-name" title="' + escapeHTML(data.name) + '">' + escapeHTML(data.name) + '</h3>' +
                    '<p class="dh-meta">' + (data.preview ? KIND_NAMES[data.preview] + ' · ' : '') + escapeHTML(data.size) + ' · ' + escapeHTML(data.mtime) + '</p>' +
                    '</div></div>';

                if (data.is_folder) {
                    html += '<p class="dh-sub">' + data.file_count + ' 个文件 · ' + data.folder_count + ' 个文件夹</p>';
                }

                html += '<div class="preview-wrap">' + previewHTML(data.preview, encodedPath) + '</div>';

                html += '<div class="details-actions">';
                if (!data.is_folder) {
                    html += '<a href="/download/' + encodedPath + '" class="btn btn-primary download-btn"><i data-lucide="download"></i> 下载文件</a>';
                }
                if (IS_ADMIN) {
                    html +=
                        '<div class="details-btn-row">' +
                        '<button class="btn-ghost" id="act-rename"><i data-lucide="pencil"></i> 重命名</button>' +
                        '<button class="btn-ghost" id="act-share"><i data-lucide="share-2"></i> 分享</button>' +
                        '<button class="btn-ghost danger" id="act-delete"><i data-lucide="trash-2"></i> 删除</button>' +
                        '</div>';
                }
                html += '</div>';

                detailsContent.innerHTML = html;
                bindPreviewFallbacks(detailsContent);
                if (window.lucide) lucide.createIcons();

                const zoomImg = detailsContent.querySelector('.preview-media img.preview-zoomable');
                if (zoomImg) zoomImg.addEventListener('click', function () {
                    openLightbox('<img src="' + zoomImg.src + '" alt="">');
                });
                const expandBtn = detailsContent.querySelector('.preview-expand');
                if (expandBtn) expandBtn.addEventListener('click', function () {
                    openLightbox('<video controls autoplay src="' + expandBtn.dataset.src + '"></video>');
                });

                if (IS_ADMIN) {
                    detailsContent.querySelector('#act-rename').addEventListener('click', function () {
                        renameAction(path, data.name);
                    });
                    detailsContent.querySelector('#act-delete').addEventListener('click', function () {
                        deleteAction(path, data.name);
                    });
                    detailsContent.querySelector('#act-share').addEventListener('click', function () {
                        openShareModal(path);
                    });
                }
            })
            .catch(function () {
                detailsContent.innerHTML = '<div class="placeholder"><span class="placeholder-icon">⚠️</span><p>加载详情失败</p></div>';
            });
    }

    fileItems.forEach(function (item) {
        item.addEventListener('click', function (e) {
            if (multiSelect) {
                if (e.target.closest('.col-check')) return;
                const cb = this.querySelector('.row-check');
                cb.checked = !cb.checked;
                cb.dispatchEvent(new Event('change'));
                return;
            }
            fileItems.forEach(function (i) { i.classList.remove('selected'); });
            this.classList.add('selected');
            if (IS_MOBILE()) detailsPane.classList.add('mobile-open');
            loadDetails(this.dataset.path);
        });
    });

    document.getElementById('close-details').addEventListener('click', function () {
        detailsPane.classList.remove('mobile-open');
    });

    /* ---------- 管理操作 ---------- */
    function renameAction(path, oldName) {
        askText('重命名', oldName).then(function (newName) {
            if (!newName || newName === oldName) return;
            postJSON('/api/rename', { path: path, new_name: newName }).then(function (r) {
                if (r.ok) refresh();
                else showToast(r.body.error || '重命名失败', 'error');
            });
        });
    }

    function deleteAction(path, name) {
        askConfirm('确定要删除「' + name + '」吗?文件将移入回收站,误删可恢复。', '删除').then(function (ok) {
            if (!ok) return;
            postJSON('/api/delete', { path: path }).then(function (r) {
                if (r.ok) refresh();
                else showToast(r.body.error || '删除失败', 'error');
            });
        });
    }

    const mkdirBtn = document.getElementById('mkdir-btn');
    if (mkdirBtn) {
        mkdirBtn.addEventListener('click', function () {
            askText('新建文件夹', '').then(function (name) {
                if (!name) return;
                postJSON('/api/mkdir', { parent: CURRENT_PATH, name: name }).then(function (r) {
                    if (r.ok) refresh();
                    else showToast(r.body.error || '创建失败', 'error');
                });
            });
        });
    }

    /* ---------- 分享弹窗 ---------- */
    const shareModal = document.getElementById('share-modal');
    const shareHours = document.getElementById('share-hours');
    const shareResult = document.getElementById('share-result');

    function openShareModal(path) {
        shareResult.innerHTML = '';
        shareModal.style.display = 'flex';
        document.body.style.overflow = 'hidden';
        shareModal.dataset.path = path;
    }

    if (shareModal) {
        document.getElementById('share-close').addEventListener('click', function () {
            shareModal.style.display = 'none';
            document.body.style.overflow = 'auto';
        });
        shareModal.addEventListener('click', function (e) {
            if (e.target === shareModal) {
                shareModal.style.display = 'none';
                document.body.style.overflow = 'auto';
            }
        });
        document.getElementById('share-generate').addEventListener('click', function () {
            const hours = parseInt(shareHours.value, 10);
            shareResult.innerHTML = '<p class="preview-fallback">生成中...</p>';
            postJSON('/share/create', { path: shareModal.dataset.path, hours: hours }).then(function (r) {
                if (!r.ok) {
                    shareResult.innerHTML = '<p class="preview-fallback error-text">' + escapeHTML(r.body.error || '生成失败') + '</p>';
                    return;
                }
                const url = r.body.url;
                shareResult.innerHTML =
                    '<div class="share-link-box">' +
                    '<input readonly value="' + escapeHTML(url) + '" id="share-url-input">' +
                    '<button class="btn-ghost" id="copy-share">复制</button>' +
                    '</div>' +
                    '<p class="share-expire">有效期至 ' + escapeHTML(r.body.expires_at) + ',扫码或复制链接即可访问(无需登录)</p>' +
                    '<img class="share-qr" src="/qr?url=' + encodeURIComponent(url) + '" alt="二维码">';
                shareResult.querySelector('#copy-share').addEventListener('click', function () {
                    navigator.clipboard.writeText(url).then(function () {
                        this.textContent = '已复制';
                    }.bind(this));
                });
            });
        });
    }

    /* ---------- 上传 ---------- */
    const uploadBtn = document.getElementById('upload-btn');
    const modal = document.getElementById('upload-modal');
    const closeModal = document.getElementById('close-modal');
    const uploadForm = document.getElementById('upload-form');
    const fileInput = document.getElementById('file');
    const fileChosen = document.getElementById('file-chosen');
    const uploadPreview = document.getElementById('upload-preview');
    const progressWrap = document.getElementById('upload-progress-wrap');
    const progressFill = document.getElementById('upload-progress-fill');
    const progressText = document.getElementById('upload-progress-text');
    const submitBtn = document.getElementById('upload-submit');

    if (uploadBtn) {
        uploadBtn.addEventListener('click', function () {
            modal.style.display = 'flex';
            document.body.style.overflow = 'hidden';
        });
    }
    closeModal.addEventListener('click', closeUploadModal);
    window.addEventListener('click', function (event) {
        if (event.target === modal) closeUploadModal();
    });

    let uploading = false;

    function closeUploadModal() {
        if (uploading) {
            askConfirm('上传仍在后台进行,确定关闭窗口吗?', '关闭').then(function (ok) {
                if (!ok) return;
                uploading = false;
                modal.style.display = 'none';
                document.body.style.overflow = 'auto';
            });
            return;
        }
        modal.style.display = 'none';
        document.body.style.overflow = 'auto';
    }

    function humanSpeed(bytesPerSec) {
        if (bytesPerSec < 1024) return bytesPerSec.toFixed(0) + ' B/s';
        if (bytesPerSec < 1048576) return (bytesPerSec / 1024).toFixed(1) + ' KB/s';
        return (bytesPerSec / 1048576).toFixed(1) + ' MB/s';
    }

    function startUpload() {
        if (!fileInput.files.length) return;
        progressWrap.style.display = 'block';
        submitBtn.disabled = true;
        submitBtn.textContent = '上传中...';
        uploading = true;

        const fd = new FormData(uploadForm);
        const xhr = new XMLHttpRequest();
        xhr.open('POST', '/upload?subpath=' + encodeURIComponent(CURRENT_PATH));
        xhr.setRequestHeader('Accept', 'application/json');

        let lastLoaded = 0, lastTime = Date.now();
        xhr.upload.onprogress = function (e) {
            if (!e.lengthComputable) return;
            const now = Date.now();
            const pct = Math.round(e.loaded / e.total * 100);
            const speed = (e.loaded - lastLoaded) / Math.max(1, now - lastTime) * 1000;
            lastLoaded = e.loaded; lastTime = now;
            progressFill.style.width = pct + '%';
            progressText.textContent = pct + '% · ' + humanSpeed(speed);
        };
        xhr.onload = function () {
            uploading = false;
            if (xhr.status === 200) {
                progressFill.style.width = '100%';
                progressText.textContent = '完成,正在刷新...';
                setTimeout(refresh, 400);
            } else {
                let msg = '上传失败';
                try { msg = JSON.parse(xhr.responseText).error || msg; } catch (err) { /* 忽略 */ }
                progressText.textContent = msg;
                submitBtn.disabled = false;
                submitBtn.textContent = '确认上传';
            }
        };
        xhr.onerror = function () {
            uploading = false;
            progressText.textContent = '网络错误,上传失败';
            submitBtn.disabled = false;
            submitBtn.textContent = '确认上传';
        };
        xhr.send(fd);
    }

    uploadForm.addEventListener('submit', function (e) {
        e.preventDefault();
        startUpload();
    });

    fileInput.addEventListener('change', function () {
        if (this.files.length > 0) {
            fileChosen.textContent = '已选择 ' + this.files.length + ' 个文件';
            fileChosen.style.color = 'var(--success-color)';
            let html = '<ul>';
            Array.prototype.forEach.call(this.files, function (f) {
                const size = f.size < 1024 ? f.size + ' B' :
                    f.size < 1048576 ? (f.size / 1024).toFixed(1) + ' KB' :
                    (f.size / 1048576).toFixed(1) + ' MB';
                html += '<li><strong>' + escapeHTML(f.name) + '</strong> (' + size + ')</li>';
            });
            uploadPreview.innerHTML = html + '</ul>';
        } else {
            fileChosen.textContent = '未选择文件';
            fileChosen.style.color = 'var(--font-color-light)';
            uploadPreview.innerHTML = '';
        }
    });

    /* 拖入上传区域(弹窗内) */
    const fileLabel = document.querySelector('.file-input-label');
    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(function (name) {
        fileLabel.addEventListener(name, function (e) { e.preventDefault(); e.stopPropagation(); }, false);
    });
    fileLabel.addEventListener('dragover', function () {
        fileLabel.classList.add('drag-hover');
    });
    ['dragleave', 'drop'].forEach(function (name) {
        fileLabel.addEventListener(name, function () {
            fileLabel.classList.remove('drag-hover');
        });
    });
    fileLabel.addEventListener('drop', function (e) {
        if (e.dataTransfer.files.length) {
            fileInput.files = e.dataTransfer.files;
            fileInput.dispatchEvent(new Event('change', { bubbles: true }));
        }
    });

    /* 整页拖拽上传 */
    let dragDepth = 0;
    const dragOverlay = document.getElementById('drag-overlay');
    window.addEventListener('dragenter', function (e) {
        if (!uploadBtn || !e.dataTransfer || e.dataTransfer.types.indexOf('Files') === -1) return;
        dragDepth++;
        dragOverlay.style.display = 'flex';
    });
    window.addEventListener('dragleave', function (e) {
        dragDepth = Math.max(0, dragDepth - 1);
        if (dragDepth === 0) dragOverlay.style.display = 'none';
    });
    window.addEventListener('dragover', function (e) { e.preventDefault(); });
    window.addEventListener('drop', function (e) {
        e.preventDefault();
        dragDepth = 0;
        dragOverlay.style.display = 'none';
        if (!uploadBtn || !e.dataTransfer || !e.dataTransfer.files.length) return;
        fileInput.files = e.dataTransfer.files;
        fileInput.dispatchEvent(new Event('change', { bubbles: true }));
        modal.style.display = 'flex';
        document.body.style.overflow = 'hidden';
        startUpload();
    });

    /* ---------- 搜索 ---------- */
    const searchBox = document.getElementById('search-box');
    if (searchBox) {
        searchBox.addEventListener('input', function () {
            applyFilter(this.value.trim().toLowerCase());
        });
    }

    function applyFilter(q) {
        let visibleCount = 0;
        const rows = document.querySelectorAll('#file-table tbody .file-item');
        rows.forEach(function (row) {
            const hit = !q || row.dataset.name.toLowerCase().indexOf(q) !== -1;
            row.style.display = hit ? '' : 'none';
            if (hit) visibleCount++;
        });
        const noMatch = document.getElementById('no-match');
        if (noMatch) noMatch.hidden = !(q && visibleCount === 0 && rows.length > 0);
        renderGrid(q);
    }

    /* ---------- 排序 ---------- */
    let sortKey = null, sortAsc = true;
    document.querySelectorAll('#file-table th[data-sort]').forEach(function (th) {
        th.addEventListener('click', function () {
            const key = this.dataset.sort;
            if (sortKey === key) sortAsc = !sortAsc;
            else { sortKey = key; sortAsc = true; }
            document.querySelectorAll('#file-table th').forEach(function (h) { h.classList.remove('sorted-asc', 'sorted-desc'); });
            th.classList.add(sortAsc ? 'sorted-asc' : 'sorted-desc');
            sortRows();
        });
    });

    function sortRows() {
        if (!sortKey) return;
        const tbody = document.querySelector('#file-table tbody');
        const rows = Array.prototype.slice.call(tbody.querySelectorAll('.file-item'));
        const dir = sortAsc ? 1 : -1;
        rows.sort(function (a, b) {
            const fa = a.dataset.isFolder === 'true', fb = b.dataset.isFolder === 'true';
            if (fa !== fb) return fa ? -1 : 1;  // 文件夹始终在前
            let va, vb;
            if (sortKey === 'name') { va = a.dataset.name.toLowerCase(); vb = b.dataset.name.toLowerCase(); return va < vb ? -dir : va > vb ? dir : 0; }
            if (sortKey === 'mtime') { va = +a.dataset.mtime; vb = +b.dataset.mtime; return (va - vb) * dir; }
            va = +a.dataset.size; vb = +b.dataset.size; return (va - vb) * dir;
        });
        rows.forEach(function (r) { tbody.appendChild(r); });
    }

    /* ---------- 视图切换(列表/网格) ---------- */
    const fileBrowser = document.getElementById('file-browser');
    const gridView = document.getElementById('grid-view');
    const listBtn = document.getElementById('view-list');
    const gridBtn = document.getElementById('view-grid');
    const ITEMS = JSON.parse((document.getElementById('items-data') || {}).textContent || '[]');

    function renderGrid(q) {
        if (!gridView) return;
        const query = (q === undefined) ? (searchBox ? searchBox.value.trim().toLowerCase() : '') : q;
        let html = '';
        ITEMS.forEach(function (it) {
            if (query && it.name.toLowerCase().indexOf(query) === -1) return;
            let inner;
            if (!it.is_folder && it.preview === 'image') {
                inner = '<img loading="lazy" src="/thumb/' + encPath(it.path) + '" alt="">';
            } else {
                inner = '<span class="grid-icon">' + it.icon + '</span>';
            }
            const open = it.is_folder
                ? ' data-href="' + escapeHTML(it.path) + '"'
                : ' data-detail="' + escapeHTML(it.path) + '"';
            html += '<div class="grid-card"' + open + '>' + inner +
                '<span class="grid-name" title="' + escapeHTML(it.name) + '">' + escapeHTML(it.name) + '</span></div>';
        });
        gridView.innerHTML = html || '<p class="empty-folder">没有匹配的文件</p>';
        gridView.querySelectorAll('.grid-card[data-href]').forEach(function (card) {
            card.addEventListener('click', function () { location.href = this.dataset.href; });
        });
        gridView.querySelectorAll('.grid-card[data-detail]').forEach(function (card) {
            card.addEventListener('click', function () {
                if (IS_MOBILE()) detailsPane.classList.add('mobile-open');
                loadDetails(this.dataset.detail);
            });
        });
    }

    function setView(mode) {
        localStorage.setItem('view-mode', mode);
        if (mode === 'grid') {
            fileBrowser.querySelector('#file-table').style.display = 'none';
            gridView.style.display = 'grid';
            renderGrid();
            listBtn.classList.remove('active-toggle');
            gridBtn.classList.add('active-toggle');
        } else {
            fileBrowser.querySelector('#file-table').style.display = '';
            gridView.style.display = 'none';
            listBtn.classList.add('active-toggle');
            gridBtn.classList.remove('active-toggle');
        }
    }

    if (listBtn && gridBtn) {
        listBtn.addEventListener('click', function () { setView('list'); });
        gridBtn.addEventListener('click', function () { setView('grid'); });
        setView(localStorage.getItem('view-mode') || 'list');
    }

    /* ---------- 多选下载 ---------- */
    const multiselectBtn = document.getElementById('multiselect-btn');
    const selectionBar = document.getElementById('selection-bar');
    const selectionInfo = document.getElementById('selection-info');
    let multiSelect = false;
    const selected = new Map();  // path -> {isFolder, size}

    const KIND_NAMES = { video: '视频', audio: '音频', image: '图片', pdf: 'PDF 文档', text: '文本' };

    function humanBytes(n) {
        if (n < 1024) return n + ' B';
        if (n < 1048576) return (n / 1024).toFixed(1) + ' KB';
        if (n < 1073741824) return (n / 1048576).toFixed(1) + ' MB';
        return (n / 1073741824).toFixed(2) + ' GB';
    }

    function updateSelectionBar() {
        // 同步操作栏实际高度到 CSS 变量,滚动区据此预留底部空间(手机端操作栏折成两行也能避让)
        function syncBarHeight() {
            const visible = multiSelect && selected.size;
            document.body.style.setProperty('--selbar-h',
                visible ? selectionBar.offsetHeight + 'px' : '0px');
        }
        if (!multiSelect) {
            selectionBar.style.display = 'none';
            document.body.style.setProperty('--selbar-h', '0px');
            return;
        }
        let files = 0, folders = 0, bytes = 0;
        selected.forEach(function (meta) {
            if (meta.isFolder) folders++;
            else { files++; bytes += meta.size; }
        });
        const total = files + folders;
        selectionInfo.textContent = '已选 ' + total + ' 项' +
            (folders ? '(' + files + ' 文件 ' + folders + ' 文件夹)' : '') +
            (files ? ' · 约 ' + humanBytes(bytes) : '');
        selectionBar.style.display = total ? 'flex' : 'none';
        syncBarHeight();
    }

    function setRowChecked(row, checked) {
        const cb = row.querySelector('.row-check');
        if (cb) cb.checked = checked;
        const path = row.dataset.path;
        if (checked) {
            selected.set(path, { isFolder: row.dataset.isFolder === 'true', size: +row.dataset.size || 0 });
        } else {
            selected.delete(path);
        }
    }

    function clearSelection() {
        selected.clear();
        document.querySelectorAll('.row-check').forEach(function (cb) { cb.checked = false; });
        updateSelectionBar();
    }

    if (multiselectBtn) {
        multiselectBtn.addEventListener('click', function () {
            multiSelect = !multiSelect;
            document.body.classList.toggle('multi-select', multiSelect);
            multiselectBtn.classList.toggle('active-toggle', multiSelect);
            if (!multiSelect) clearSelection();
            updateSelectionBar();
        });
    }

    // 退出多选模式:清空勾选并收起操作栏(下载完成后自动调用)
    function exitMultiSelect() {
        multiSelect = false;
        document.body.classList.remove('multi-select');
        multiselectBtn.classList.remove('active-toggle');
        clearSelection();
    }

    // 窗口尺寸变化(如手机横竖屏切换)时操作栏折行数可能改变,重新测量
    window.addEventListener('resize', function () {
        if (multiSelect && selected.size) {
            document.body.style.setProperty('--selbar-h', selectionBar.offsetHeight + 'px');
        }
    });

    // 复选框自身点击不冒泡到行,避免双重切换
    document.querySelectorAll('.row-check').forEach(function (cb) {
        cb.addEventListener('click', function (e) { e.stopPropagation(); });
        cb.addEventListener('change', function () {
            const row = cb.closest('tr');
            if (cb.checked) {
                selected.set(row.dataset.path, { isFolder: row.dataset.isFolder === 'true', size: +row.dataset.size || 0 });
            } else {
                selected.delete(row.dataset.path);
            }
            updateSelectionBar();
        });
    });

    document.getElementById('select-all').addEventListener('click', function () {
        const visible = Array.prototype.filter.call(
            document.querySelectorAll('#file-table tbody .file-item'),
            function (r) { return r.style.display !== 'none'; });
        const allChecked = visible.length > 0 && visible.every(function (r) {
            return r.querySelector('.row-check').checked;
        });
        visible.forEach(function (r) { setRowChecked(r, !allChecked); });
        updateSelectionBar();
    });

    document.getElementById('clear-selection').addEventListener('click', clearSelection);

    // 直接下载:逐个触发浏览器下载(不打包)
    document.getElementById('download-direct').addEventListener('click', function () {
        const files = [];
        let folders = 0;
        selected.forEach(function (meta, path) {
            if (meta.isFolder) folders++;
            else files.push(path);
        });
        if (!files.length) {
            showToast('所选内容没有可直下的文件,文件夹请用打包下载', 'error');
            return;
        }
        if (folders) showToast(folders + ' 个文件夹无法直接下载,已跳过(可用打包下载)', 'error');
        showToast('开始下载 ' + files.length + ' 个文件', 'success');
        exitMultiSelect();
        files.forEach(function (path, i) {
            setTimeout(function () {
                const a = document.createElement('a');
                a.href = '/download/' + encPath(path);
                a.download = '';
                document.body.appendChild(a);
                a.click();
                a.remove();
            }, i * 400);
        });
    });

    // 打包下载:zip blob
    document.getElementById('download-zip').addEventListener('click', function () {
        if (!selected.size) return;
        const btn = this;
        const original = btn.textContent;
        btn.disabled = true;
        btn.textContent = '打包中...';
        fetch('/api/download-zip', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF },
            body: JSON.stringify({ paths: Array.from(selected.keys()) })
        }).then(function (r) {
            if (!r.ok) return r.json().then(function (j) { throw new Error(j.error || '打包失败'); });
            const cd = r.headers.get('Content-Disposition') || '';
            let name = '共享文件.zip';
            const m1 = /filename[*]=UTF-8''([^;]+)/.exec(cd);
            if (m1) { name = decodeURIComponent(m1[1]); }
            else {
                const m2 = /filename="?([^";]+)"?/.exec(cd);
                if (m2) name = m2[1];
            }
            return r.blob().then(function (blob) { return { blob: blob, name: name }; });
        }).then(function (res) {
            const a = document.createElement('a');
            a.href = URL.createObjectURL(res.blob);
            a.download = res.name;
            document.body.appendChild(a);
            a.click();
            setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
            showToast('已开始下载 ' + res.name, 'success');
            btn.disabled = false;
            btn.textContent = original;
            exitMultiSelect();
        }).catch(function (e) {
            showToast(e.message || '打包失败', 'error');
            btn.disabled = false;
            btn.textContent = original;
        });
    });

    /* ---------- 导航进度条/面包屑/键盘/深色/回到顶部 ---------- */
    const navProgress = document.getElementById('nav-progress');
    document.addEventListener('click', function (e) {
        const link = e.target.closest('a[href^="/"]');
        if (!link || link.target === '_blank' || link.hasAttribute('download')) return;
        navProgress.classList.add('active');
    });
    window.addEventListener('pageshow', function () {
        navProgress.classList.remove('active');
        // bfcache 前进/后退恢复时重置瞬态 UI,避免回到上一页时详情浮层/弹窗仍处于打开状态
        detailsPane.classList.remove('mobile-open');
        closeLightbox();
        ['input-modal', 'confirm-modal', 'share-modal', 'upload-modal'].forEach(function (id) {
            const el = document.getElementById(id);
            if (el) el.style.display = 'none';
        });
        document.body.style.overflow = '';
    });

    const crumbsEl = document.querySelector('.breadcrumbs');
    function scrollCrumbsToEnd() {
        if (crumbsEl) crumbsEl.scrollLeft = crumbsEl.scrollWidth;
    }
    scrollCrumbsToEnd();
    window.addEventListener('load', scrollCrumbsToEnd);
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(scrollCrumbsToEnd);

    document.getElementById('nav-back').addEventListener('click', function () { history.back(); });
    document.getElementById('nav-forward').addEventListener('click', function () { history.forward(); });

    // 手机端:回到顶部与后退/前进并入同一悬浮组(共用定位,保证对齐);桌面端各回原位
    const navHistory = document.querySelector('.nav-history');
    const backTopBtn = document.getElementById('back-top');
    function placeFloatingButtons() {
        const backTopBtn = document.getElementById('back-top');
        if (window.matchMedia('(max-width: 640px)').matches) {
            navHistory.insertBefore(backTopBtn, navHistory.firstChild);
        } else {
            document.querySelector('.content').appendChild(backTopBtn);
            crumbsEl.parentNode.insertBefore(navHistory, crumbsEl);
        }
    }
    placeFloatingButtons();
    const mq640 = window.matchMedia('(max-width: 640px)');
    if (mq640.addEventListener) mq640.addEventListener('change', placeFloatingButtons);

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
            if (lightbox.style.display === 'flex') { closeLightbox(); return; }
            const im = document.getElementById('input-modal');
            if (im.style.display === 'flex') { document.getElementById('input-cancel').click(); return; }
            const cm = document.getElementById('confirm-modal');
            if (cm.style.display === 'flex') { document.getElementById('confirm-cancel').click(); return; }
            if (typeof shareModal !== 'undefined' && shareModal.style.display === 'flex') { closeModalEl(shareModal); return; }
            if (typeof modal !== 'undefined' && modal.style.display === 'flex') { closeUploadModal(); return; }
            if (detailsPane.classList.contains('mobile-open')) detailsPane.classList.remove('mobile-open');
            return;
        }
        const tag = (document.activeElement || {}).tagName || '';
        const typing = /input|textarea|select/i.test(tag);
        if ((e.key === '/' || (e.key === 'k' && (e.ctrlKey || e.metaKey))) && !typing) {
            e.preventDefault();
            if (searchBox) searchBox.focus();
        }
    });

    const darkMQ = window.matchMedia('(prefers-color-scheme: dark)');
    function applyDarkScheme() {
        document.documentElement.classList.toggle('dark', darkMQ.matches);
    }
    applyDarkScheme();
    if (darkMQ.addEventListener) darkMQ.addEventListener('change', applyDarkScheme);

    if (window.lucide) lucide.createIcons();

    const backTop = document.getElementById('back-top');
    const browserEl = document.getElementById('file-browser');
    if (backTop && browserEl) {
        browserEl.addEventListener('scroll', function () {
            backTop.classList.toggle('show', browserEl.scrollTop > 600);
        });
        backTop.addEventListener('click', function () {
            browserEl.scrollTo({ top: 0, behavior: 'smooth' });
        });
    }

    /* ---------- 页面加载动画 ---------- */
    setTimeout(function () {
        [document.querySelector('.content'), document.querySelector('.details-pane')].forEach(function (el, index) {
            if (!el) return;
            el.style.opacity = '0';
            el.style.transform = 'translateY(10px)';
            setTimeout(function () {
                el.style.transition = 'all 0.25s ease-out';
                el.style.opacity = '1';
                el.style.transform = '';
            }, index * 50);
        });
    }, 0);
});
