# -*- coding: utf-8 -*-
# 集中配置:改这里即可,不用动 app.py

# 服务端口
PORT = 5000

# 共享根目录(Windows 下正斜杠写法等效,如需改回反斜杠写法请用原始字符串 r'D:\共享')
SHARED_DIR = 'D:/共享'

# 单次上传大小上限(整个请求体)
MAX_CONTENT_LENGTH = 10 * 1024**3

# 文件列表每页条数
PAGE_SIZE = 300

# 缩略图最长边(像素)
THUMB_SIZE = 256

# 分享链接有效期选项(小时)与默认值
SHARE_HOURS_OPTIONS = [1, 24, 168]
SHARE_DEFAULT_HOURS = 24

# 账号列表:(用户名, 密码哈希, 角色)
# 角色:admin 可上传/管理/分享;guest 只读(浏览/下载/预览)
# 修改密码:python -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('新密码'))"
# 把输出替换到对应位置即可
USERS = [
    ('1', 'scrypt:32768:8:1$AHkIt479yxtRKlLh$9abfdaa08d5259410e1c87a984df3ef70897c749b673a78840d7edffbed302fe669873ff19a37dd865a65463c3592c6b22f0dff1840aaed0f53fbe9dc696419c', 'admin'),
    ('guest', 'scrypt:32768:8:1$S9tbQ8PGHWfLjtpc$2e57fccf272346aadeb153b8e0517b65f5a74e1dcc7c2f1f5442f307ad7166e6494c546f8d87174a3a2f67ffc0f796c6f77462f9c9e507458cf996da291254b6', 'guest'),
]
