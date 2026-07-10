# -*- coding: utf-8 -*-
import os
import requests
from cachelib.redis import RedisCache
from flask import redirect, request, render_template_string, url_for
from flask_login import login_user
from flask_appbuilder.security.manager import AUTH_OAUTH
from superset.security import SupersetSecurityManager

# ==========================================
# 1. 基础认证与安全配置 (OAuth2 企业微信)
# ==========================================
AUTH_TYPE = AUTH_OAUTH
AUTH_USER_REGISTRATION = True
AUTH_USER_REGISTRATION_ROLE = "Public"
AUTH_ROLE_PUBLIC = "Public"

# 关闭 Talisman CSP（登录页需要加载外部企业微信 JS SDK 和内联脚本）
TALISMAN_ENABLED = False

# 企业微信环境变量读取
WECOM_CORP_ID = os.environ.get("WECOM_CORP_ID")
WECOM_SECRET = os.environ.get("WECOM_SECRET")
WECOM_AGENT_ID = os.environ.get("WECOM_AGENT_ID")
WECOM_REDIRECT_URI = os.environ.get("WECOM_REDIRECT_URI")

# 賬號映射
WECOM_USER_MAP = {
    '2': 'superman',
    '8': 'admin',
    # 按需继续添加更多映射：
    # '15': 'zhangsan',
    # '23': 'lisi',
}
OAUTH_PROVIDERS = [
    {
        'name': 'wecom',
        'icon': 'fa-wechat',
        'token_key': 'access_token',
        'remote_app': {
            'client_id': WECOM_CORP_ID,
            'client_secret': WECOM_SECRET,
            'api_base_url': 'https://qyapi.weixin.qq.com/cgi-bin/',
            'access_token_url': 'https://qyapi.weixin.qq.com/cgi-bin/gettoken',
            'authorize_url': 'https://open.work.weixin.qq.com/wwopen/sso/qrConnect',
            'request_token_params': {
                'appid': WECOM_CORP_ID,
                'agentid': WECOM_AGENT_ID,
                'redirect_uri': WECOM_REDIRECT_URI,
                'state': 'wecom_login',
            }
        }
    }
]

# ==========================================
# 2. 自定义登录页 HTML 模板（响应式：PC 并排，移动端堆叠）
# ==========================================
LOGIN_PAGE_HTML = """
<!DOCTYPE html>
<html lang="zh">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BI 数据分析平台 - 登录</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            background: linear-gradient(135deg, #F4F6FA 0%, #EBF0FF 100%);
            padding: 16px;
        }
        .login-wrapper {
            width: 100%;
            max-width: 820px;
        }
        .login-header {
            text-align: center;
            margin-bottom: 28px;
        }
        .logo {
            width: 56px; height: 56px; border-radius: 16px;
            background: #315EFB; display: inline-flex;
            align-items: center; justify-content: center;
            margin-bottom: 12px;
            box-shadow: 0 4px 12px rgba(49,94,251,0.3);
        }
        .logo svg { width: 28px; height: 28px; }
        .login-header h2 {
            font-size: 22px; font-weight: 600; color: #1a1a1a;
            margin-bottom: 4px;
        }
        .login-header p {
            font-size: 13px; color: #999;
        }
        .login-panels {
            display: flex;
            gap: 20px;
            align-items: stretch;
        }
        .panel {
            background: #fff;
            border: 1px solid #f0f0f0;
            border-radius: 20px;
            padding: 28px 24px;
            box-shadow: 0 8px 32px rgba(0,0,0,0.06);
            flex: 1;
            display: flex;
            flex-direction: column;
        }
        .panel-title {
            font-size: 15px; font-weight: 500; color: #333;
            margin-bottom: 20px; text-align: center;
        }
        label {
            display: block; margin-bottom: 6px;
            font-size: 12px; color: #666; font-weight: 500;
        }
        input[type="text"], input[type="password"] {
            width: 100%; height: 42px; padding: 0 14px;
            border: 1px solid #e0e0e0; border-radius: 10px;
            font-size: 14px; outline: none;
            transition: border-color 0.2s, box-shadow 0.2s;
            background: #fafafa;
        }
        input[type="text"]:focus, input[type="password"]:focus {
            border-color: #315EFB;
            box-shadow: 0 0 0 3px rgba(49,94,251,0.1);
            background: #fff;
        }
        .form-group { margin-bottom: 14px; }
        .btn-login {
            width: 100%; height: 42px; background: #315EFB;
            color: #fff; border: none; border-radius: 10px;
            font-size: 14px; font-weight: 500; cursor: pointer;
            transition: background 0.2s, transform 0.1s;
            margin-top: 4px;
        }
        .btn-login:hover { background: #4a6fff; }
        .btn-login:active { transform: scale(0.98); }
        .error {
            margin-top: 12px; padding: 8px 12px;
            background: #fff2f0; border: 1px solid #ffccc7;
            border-radius: 8px; color: #ff4d4f;
            font-size: 12px; text-align: center;
        }
        .qr-section {
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            flex: 1;
        }
        .qr-hint {
            font-size: 12px; color: #999;
            margin-bottom: 12px; text-align: center;
        }
        #wx_reg {
            width: 100%; max-width: 260px;
            display: flex; align-items: center; justify-content: center;
            min-height: 260px;
        }
        .tip {
            margin-top: 16px;
            font-size: 11px; color: #bbb; text-align: center;
        }
        .divider-mobile {
            display: none;
            text-align: center;
            padding: 8px 0;
            color: #ccc;
            font-size: 12px;
        }

        /* 响应式：移动端堆叠 */
        @media (max-width: 680px) {
            .login-panels {
                flex-direction: column;
                gap: 16px;
            }
            .divider-mobile {
                display: block;
            }
            .panel {
                padding: 24px 20px;
                border-radius: 16px;
            }
            .login-header h2 {
                font-size: 20px;
            }
            #wx_reg {
                max-width: 220px;
                min-height: 220px;
            }
        }

        /* 超小屏幕 */
        @media (max-width: 380px) {
            body { padding: 8px; }
            .panel { padding: 20px 16px; }
            #wx_reg {
                max-width: 180px;
                min-height: 180px;
            }
        }
    </style>
</head>
<body>
    <div class="login-wrapper">
        <div class="login-header">
            <div class="logo">
                <svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2">
                    <path d="M3 3h18v18H3zM12 8v8M8 12h8"/>
                </svg>
            </div>
            <h2>BI 数据分析平台</h2>
            <p>Superset</p>
        </div>

        <div class="login-panels">
            <!-- 左侧：账号密码登录 -->
            <div class="panel">
                <div class="panel-title">账号密码登录</div>
                <form method="POST" action="/login/local" style="flex:1;display:flex;flex-direction:column;">
                    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}"/>
                    <input type="hidden" name="next" value="{{ next_url }}"/>

                    <div class="form-group">
                        <label>用户名</label>
                        <input type="text" name="username" required autocomplete="username" placeholder="请输入用户名">
                    </div>

                    <div class="form-group">
                        <label>密码</label>
                        <input type="password" name="password" required autocomplete="current-password" placeholder="请输入密码">
                    </div>

                    <div style="flex:1;"></div>

                    <button type="submit" class="btn-login">登 录</button>

                    {% if error %}
                    <div class="error">{{ error }}</div>
                    {% endif %}
                </form>
            </div>

            <!-- 移动端分隔线 -->
            <div class="divider-mobile">— 或 —</div>

            <!-- 右侧：企业微信扫码登录 -->
            <div class="panel">
                <div class="panel-title">企业微信扫码登录</div>
                <div class="qr-section">
                    <div class="qr-hint">使用企业微信扫描下方二维码</div>
                    <div id="wx_reg"></div>
                    <div class="tip">在企业微信客户端中打开将自动登录</div>
                </div>
            </div>
        </div>
    </div>

    <script src="https://wwcdn.weixin.qq.com/node/wework/wwopen/js/wwLogin-1.2.7.js"></script>
    <script>
    (function() {
        if (window.location.search.indexOf('noredirect=1') !== -1) return;
        var ua = navigator.userAgent.toLowerCase();
        if (ua.indexOf('wxwork') !== -1) {
            window.location.replace('/auth/wecom-client-login');
        }
    })();

    (function() {
        try {
            new WwLogin({
                id: "wx_reg",
                appid: "{{ wecom_corp_id }}",
                agentid: "{{ wecom_agent_id }}",
                redirect_uri: encodeURIComponent("{{ wecom_qr_redirect_uri }}"),
                state: "wecom_login",
                href: "",
                lang: "zh"
            });
        } catch (e) {
            document.getElementById('wx_reg').innerHTML =
                '<div style="color:#ff4d4f;font-size:12px;padding:16px;text-align:center;">二维码加载失败，请刷新重试</div>';
        }
    })();
    </script>
</body>
</html>
"""

# ==========================================
# 3. 自定义安全管理器（覆盖登录路由 + 账号密码 + 企业微信扫码回调）
# ==========================================
class CustomSecurityManager(SupersetSecurityManager):

    def __init__(self, appbuilder):
        super().__init__(appbuilder)
        self._register_custom_routes()

    def _register_custom_routes(self):
        """覆盖默认登录页 + 注册账号密码登录路由 + 企业微信自定义回调"""

        @self.appbuilder.app.route('/login/', methods=['GET'])
        @self.appbuilder.app.route('/login', methods=['GET'])
        def custom_login():
            from flask_login import current_user
            if current_user.is_authenticated:
                return redirect(url_for('Superset.welcome'))

            next_url = request.args.get('next', '')
            error = request.args.get('error', '')

            # 构造二维码回调地址（不走 FAB OAuth，走自定义回调）
            wecom_qr_redirect_uri = request.host_url.rstrip('/') + '/auth/wecom-callback'

            return render_template_string(
                LOGIN_PAGE_HTML,
                next_url=next_url,
                error=error,
                wecom_corp_id=WECOM_CORP_ID,
                wecom_agent_id=WECOM_AGENT_ID,
                wecom_redirect_uri=WECOM_REDIRECT_URI,
                wecom_qr_redirect_uri=wecom_qr_redirect_uri,
            )

        @self.appbuilder.app.route('/login/local', methods=['POST'])
        def local_login():
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '')

            if not username or not password:
                return redirect(url_for('custom_login', error='请输入用户名和密码'))

            from flask_appbuilder.security.sqla.models import User
            from werkzeug.security import check_password_hash

            user = self.appbuilder.session.query(User).filter_by(
                username=username, active=True
            ).first()

            if user and check_password_hash(user.password, password):
                login_user(user, remember=True)
                next_url = request.form.get('next') or url_for('Superset.welcome')
                return redirect(next_url)
            else:
                return redirect(url_for('custom_login', error='用户名或密码错误'))

        @self.appbuilder.app.route('/auth/wecom-callback', methods=['GET'])
        def wecom_qr_callback():
            """
            自定义企业微信二维码扫码回调。
            绕过 FAB OAuth 的 state 校验，直接调用企业微信 API 换取用户信息。
            """
            code = request.args.get('code')
            if not code:
                return redirect(url_for('custom_login', error='企业微信授权失败，请重试'))

            # 1. 获取 access_token
            token_res = requests.get(
                'https://qyapi.weixin.qq.com/cgi-bin/gettoken',
                params={'corpid': WECOM_CORP_ID, 'corpsecret': WECOM_SECRET}
            ).json()
            access_token = token_res.get('access_token')

            if not access_token:
                return redirect(url_for('custom_login', error='企业微信 token 获取失败'))

            # 2. 用 code 换取 UserId
            user_info_res = requests.get(
                'https://qyapi.weixin.qq.com/cgi-bin/user/getuserinfo',
                params={'access_token': access_token, 'code': code}
            ).json()

            userid = user_info_res.get('UserId')
            if not userid:
                err_msg = user_info_res.get('errmsg', '未知错误')
                return redirect(url_for('custom_login', error=f'企业微信登录失败: {err_msg}'))

            # 3. 通过映射表查找对应的 Superset 用户名
            mapped_username = WECOM_USER_MAP.get(userid, userid)

            from flask_appbuilder.security.sqla.models import User
            user = self.appbuilder.session.query(User).filter_by(
                username=mapped_username
            ).first()

            if not user:
                # 映射的账号不存在，回退用原始 userid 查找
                if mapped_username != userid:
                    user = self.appbuilder.session.query(User).filter_by(
                        username=userid
                    ).first()

                # 仍然不存在，自动创建
                if not user:
                    user = User()
                    user.username = mapped_username
                    user.first_name = mapped_username
                    user.last_name = ''
                    user.email = f"{mapped_username}@wecom.local"
                    user.active = True

                    public_role = self.appbuilder.session.query(
                        self.role_model
                    ).filter_by(name=self.auth_user_registration_role).first()
                    if public_role:
                        user.roles.append(public_role)

                    self.appbuilder.session.add(user)
                    self.appbuilder.session.commit()

            # 4. 登录用户
            login_user(user, remember=True)
            return redirect(url_for('Superset.welcome'))

        @self.appbuilder.app.route('/auth/wecom-client-login', methods=['GET'])
        def wecom_client_login():
            """企业微信客户端内静默授权，自行构造授权 URL 并回调到自定义接口"""
            from urllib.parse import urlencode

            # 静默授权地址（注意：必须是 open.weixin.qq.com，末尾要加 #wechat_redirect）
            base_url = 'https://open.weixin.qq.com/connect/oauth2/authorize'

            # 回调地址统一使用您已有的扫码回调（确保与企业在后台配置的可信域名一致）
            redirect_uri = request.host_url.rstrip('/') + '/auth/wecom-callback'

            params = {
                'appid': WECOM_CORP_ID,          # 这里填企业 corpid
                'redirect_uri': redirect_uri,
                'response_type': 'code',
                'scope': 'snsapi_base',          # 静默授权，不需要用户确认
                'state': 'wecom_client_login',
            }
            auth_url = f"{base_url}?{urlencode(params)}#wechat_redirect"

            return redirect(auth_url)

    def oauth_user_info(self, provider, response=None):
        """企业微信 OAuth 用户信息获取（客户端内静默授权走此路径）"""
        if provider == 'wecom':
            token_res = requests.get(
                'https://qyapi.weixin.qq.com/cgi-bin/gettoken',
                params={'corpid': WECOM_CORP_ID, 'corpsecret': WECOM_SECRET}
            ).json()
            access_token = token_res.get('access_token')

            auth_code = response.get('code') if response else None
            user_info_res = requests.get(
                'https://qyapi.weixin.qq.com/cgi-bin/user/getuserinfo',
                params={'access_token': access_token, 'code': auth_code}
            ).json()

            userid = user_info_res.get('UserId')

            if userid:
                # 通过映射表转换为 Superset 用户名
                mapped_username = WECOM_USER_MAP.get(userid, userid)
                return {
                    'username': mapped_username,
                    'first_name': mapped_username,
                    'last_name': '',
                    'email': f"{mapped_username}@wecom.local"
                }

        return super().oauth_user_info(provider, response)

CUSTOM_SECURITY_MANAGER = CustomSecurityManager

# ==========================================
# 4. 功能开关
# ==========================================
FEATURE_FLAGS = {
    "ENABLE_TEMPLATE_PROCESSING": True,
    "ENABLE_AI_ASSISTANT": True,
    "SQL_LAB_AI_ASSIST": True,
}

# ==========================================
# 5. 缓存与异步结果后端配置 (Redis 逻辑库隔离)
# ==========================================
REDIS_HOST = "redis"
REDIS_PORT = 6379

# 元数据与图表缓存 (0号库)
CACHE_CONFIG = {
    'CACHE_TYPE': 'RedisCache',
    'CACHE_DEFAULT_TIMEOUT': 300,
    'CACHE_KEY_PREFIX': 'superset_',
    'CACHE_REDIS_URL': f'redis://{REDIS_HOST}:{REDIS_PORT}/0'
}

# 报表切片数据缓存 (1号库)
DATA_CACHE_CONFIG = CACHE_CONFIG.copy()
DATA_CACHE_CONFIG['CACHE_REDIS_URL'] = f'redis://{REDIS_HOST}:{REDIS_PORT}/1'

# SQL Lab 异步长查询结果缓存 (2号库)
RESULTS_BACKEND = RedisCache(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=2,
    key_prefix="superset_results_"
)
RESULTS_BACKEND_USE_GZIP = True

# ==========================================
# 6. 代理、公共访问及多语言环境配置
# ==========================================
ENABLE_PROXY_FIX = True
PROXY_FIX_CONFIG = {"x_for": 1, "x_proto": 1, "x_host": 1, "x_port": 1, "x_prefix": 0}

PUBLIC_ROLE_LIKE = "Public"
SERVICE_WORKER_ASSET_URL = None

LANGUAGES = {
    'zh': {'flag': 'cn', 'name': '简体中文'},
    'zh_TW': {'flag': 'tw', 'name': '繁体中文'},
    'en': {'flag': 'us', 'name': 'English'},
}
BABEL_DEFAULT_LOCALE = 'zh'
BABEL_DEFAULT_TIMEZONE = 'Asia/Shanghai'

# ==========================================
# 7. AI & 大模型平台配置
# ==========================================
OPENAI_API_BASE = os.environ.get("OPENAI_API_BASE")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
SUPERSET_AI_MODEL = os.environ.get("SUPERSET_AI_MODEL")
SUPERSET_ENABLE_MCP = os.environ.get("SUPERSET_ENABLE_MCP")

# ==========================================
# 8. 数据库连接
# ==========================================
DB_USER = os.environ.get("POSTGRES_USER")
DB_PASS = os.environ.get("POSTGRES_PASSWORD")
DB_HOST = os.environ.get("POSTGRES_HOST")
DB_PORT = os.environ.get("POSTGRES_PORT")
DB_NAME = os.environ.get("POSTGRES_DB")

SQLALCHEMY_DATABASE_URI = f'postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}'

APP_NAME="BI"
