# -*- coding: utf-8 -*-
import os
import requests
import json
import datetime
import flask_login
from superset.extensions import db
from urllib.parse import quote_plus
from sqlalchemy import Column, Integer, String, Date, DateTime, Text, ForeignKey
from sqlalchemy.orm import relationship
from flask_babel import lazy_gettext as _
from datetime import datetime, date
from dateutil.relativedelta import relativedelta
from cachelib.redis import RedisCache
from flask import redirect, request, render_template_string, url_for, make_response, session
from flask_login import login_user, current_user
from superset.extensions import appbuilder 
from flask_appbuilder.security.manager import AUTH_OAUTH
from flask_appbuilder import ModelView, BaseView, expose
from flask_appbuilder.models.sqla.interface import SQLAInterface
from superset.security import SupersetSecurityManager

# ==========================================
# 1. 基础认证与安全配置 (OAuth2 企业微信)
# ==========================================
AUTH_TYPE = AUTH_OAUTH
AUTH_USER_REGISTRATION = True
AUTH_USER_REGISTRATION_ROLE = "Public"
AUTH_ROLE_PUBLIC = "Public"

TALISMAN_ENABLED = True
TALISMAN_CONFIG = {
    "content_security_policy": {
        "default-src": ["'self'"],
        "script-src": ["'self'", "'unsafe-inline'", "https://wwcdn.weixin.qq.com", "https://res.wx.qq.com", "https://apachesuperset.gateway.scarf.sh", "https://fonts.googleapis.com"],
        "frame-src": ["'self'", "https://open.work.weixin.qq.com", "https://open.weixin.qq.com"],
        "img-src": ["'self'", "data:", "https://open.work.weixin.qq.com"],
        "style-src": ["'self'", "'unsafe-inline'", "https://fonts.googleapis.com"],
        "font-src": ["'self'", "data:", "https://fonts.gstatic.com"],
        "connect-src": ["'self'"],
        "object-src": "'none'",
    },
    "force_https": False,
}

WECOM_CORP_ID = os.environ.get("WECOM_CORP_ID")
WECOM_SECRET = os.environ.get("WECOM_SECRET")
WECOM_AGENT_ID = os.environ.get("WECOM_AGENT_ID")
WECOM_REDIRECT_URI = os.environ.get("WECOM_REDIRECT_URI")

_map_list = os.environ.get("WECOM_USER_MAP_LIST", "").split(",")
WECOM_USER_MAP = {}
for item in _map_list:
    if ":" in item:
        wecom_id, superset_name = item.split(":", 1)
        WECOM_USER_MAP[wecom_id.strip()] = superset_name.strip()

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
# 2. 自定义登录页 HTML 模板 (三语自适应)
# ==========================================
APP_DISPLAY_NAME = os.environ.get("APP_DISPLAY_NAME")
APP_SUBTITLE = os.environ.get("APP_SUBTITLE")

LOGIN_PAGE_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ t.title }}</title>
    <link rel="icon" href="data:,">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; min-height: 100vh; display: flex; align-items: center; justify-content: center; background: linear-gradient(135deg, #F4F6FA 0%, #EBF0FF 100%); padding: 16px; position: relative; }
        .lang-switcher { position: absolute; top: 20px; right: 20px; background: #fff; border-radius: 8px; padding: 4px 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); font-size: 13px; z-index: 100; }
        .lang-switcher a { text-decoration: none; color: #666; padding: 4px 8px; }
        .lang-switcher a.active { color: #315EFB; font-weight: 600; }
        .login-wrapper { width: 100%; max-width: 820px; }
        .login-header { text-align: center; margin-bottom: 28px; }
        .logo { height: 56px; display: inline-flex; align-items: center; justify-content: center; margin-bottom: 12px; }
        .logo img { height: 100%; object-fit: contain; }
        .login-panels { display: flex; gap: 20px; align-items: stretch; }
        .panel { background: #fff; border: 1px solid #f0f0f0; border-radius: 20px; padding: 28px 24px; box-shadow: 0 8px 32px rgba(0,0,0,0.06); flex: 1; display: flex; flex-direction: column; }
        .panel-title { font-size: 15px; font-weight: 500; color: #333; margin-bottom: 20px; text-align: center; }
        label { display: block; margin-bottom: 6px; font-size: 12px; color: #666; }
        input[type="text"], input[type="password"] { width: 100%; height: 42px; padding: 0 14px; border: 1px solid #e0e0e0; border-radius: 10px; outline: none; background: #fafafa; }
        .btn-login { width: 100%; height: 42px; background: #315EFB; color: #fff; border: none; border-radius: 10px; font-weight: 500; cursor: pointer; margin-top: 10px; }
        .error { margin-top: 12px; padding: 8px 12px; background: #fff2f0; border: 1px solid #ffccc7; border-radius: 8px; color: #ff4d4f; font-size: 12px; text-align: center; }
        .qr-section { display: flex; flex-direction: column; align-items: center; justify-content: center; flex: 1; }
        #wx_reg { width: 100%; max-width: 260px; min-height: 260px; }
        .divider-mobile { display: none; text-align: center; padding: 8px 0; color: #ccc; font-size: 12px; }
        @media (max-width: 680px) { .login-panels { flex-direction: column; } .divider-mobile { display: block; } }
    </style>
</head>
<body>
    <div class="lang-switcher">
        <a href="?locale=zh" class="{{ 'active' if current_lang == 'zh' else '' }}">简体</a> |
        <a href="?locale=zh_TW" class="{{ 'active' if current_lang == 'zh_TW' else '' }}">繁體</a> |
        <a href="?locale=en" class="{{ 'active' if current_lang == 'en' else '' }}">English</a>
    </div>
    <div class="login-wrapper">
        <div class="login-header">
            <div class="logo"><img src="/static/assets/images/superset-logo-horiz.png"></div>
            <h1>{{ app_display_name }}</h1>
            <p>{{ app_subtitle }}</p>
        </div>
        <div class="login-panels">
            <div class="panel">
                <div class="panel-title">{{ t.local_login }}</div>
                <form method="POST" action="/login/local">
                    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}"/>
                    <input type="hidden" name="next" value="{{ next_url }}"/>
                    <div class="form-group"><label>{{ t.username }}</label><input type="text" name="username" required placeholder="{{ t.user_placeholder }}"></div>
                    <div class="form-group"><label>{{ t.password }}</label><input type="password" name="password" required placeholder="{{ t.pwd_placeholder }}"></div>
                    <button type="submit" class="btn-login">{{ t.login_btn }}</button>
                    {% if error %}<div class="error">{{ error }}</div>{% endif %}
                </form>
            </div>
            <div class="divider-mobile">{{ t.divider }}</div>
            <div class="panel">
                <div class="panel-title">{{ t.wecom_login }}</div>
                <div class="qr-section">
                    <div style="font-size:12px;color:#999;margin-bottom:12px;">{{ t.qr_hint }}</div>
                    <div id="wx_reg"></div>
                    <div style="margin-top:16px;font-size:11px;color:#bbb;">{{ t.client_tip }}</div>
                </div>
            </div>
        </div>
    </div>
    <script src="https://wwcdn.weixin.qq.com/node/wework/wwopen/js/wwLogin-1.2.7.js"></script>
    <script>
    (function() {
        new WwLogin({ id: "wx_reg", appid: "{{ wecom_corp_id }}", agentid: "{{ wecom_agent_id }}", redirect_uri: encodeURIComponent("{{ wecom_qr_redirect_uri }}"), state: "wecom_login", lang: "{{ wecom_lang }}" });
    })();
    </script>
</body>
</html>
"""

# ==========================================
# 3. 自定义安全管理器与权限补丁
# ==========================================
from flask_appbuilder.models.sqla.filters import FilterContains
from flask_appbuilder.security.sqla.apis import PermissionViewMenuApi

class SupersetPermissionViewMenuApi(PermissionViewMenuApi):
    search_columns = ["id", "permission.name", "view_menu.name"]
    def _init_properties(self) -> None:
        super()._init_properties()
        for col in ["permission.name", "view_menu.name"]:
            self._filters._search_filters[col] = [FilterContains(col, self.datamodel)]

class CustomSecurityManager(SupersetSecurityManager):
    permission_view_menu_api = SupersetPermissionViewMenuApi
    def __init__(self, appbuilder):
        super().__init__(appbuilder)
        self._register_custom_routes()

    def _register_custom_routes(self):
        LOGIN_I18N = {
            'zh': {'title': '登录', 'local_login': '账号密码登录', 'username': '用户名', 'password': '密码', 'login_btn': '登 录', 'wecom_login': '企业微信扫码', 'qr_hint': '使用企业微信扫码', 'client_tip': '客户端内自动登录', 'divider': '— 或 —', 'user_placeholder': '用户名', 'pwd_placeholder': '密码', 'qr_error': '加载失败'},
            'zh_TW': {'title': '登錄', 'local_login': '帳號密碼登錄', 'username': '用戶名', 'password': '密碼', 'login_btn': '登 錄', 'wecom_login': '企業微信掃碼', 'qr_hint': '使用企業微信掃碼', 'client_tip': '客戶端內自動登錄', 'divider': '— 或 —', 'user_placeholder': '用戶名', 'pwd_placeholder': '密碼', 'qr_error': '加載失敗'},
            'en': {'title': 'Login', 'local_login': 'Account Login', 'username': 'Username', 'password': 'Password', 'login_btn': 'Login', 'wecom_login': 'WeCom Login', 'qr_hint': 'Scan with WeCom', 'client_tip': 'Auto-login in Client', 'divider': '— OR —', 'user_placeholder': 'Username', 'pwd_placeholder': 'Password', 'qr_error': 'Error'}
        }

        @self.appbuilder.app.route('/login/', methods=['GET'])
        @self.appbuilder.app.route('/login', methods=['GET'])
        def custom_login():
            from flask import request, make_response, session
            if current_user.is_authenticated: return redirect(url_for('Superset.welcome'))
            locale = request.args.get('locale') or request.cookies.get('locale') or request.accept_languages.best_match(['zh-TW', 'zh', 'en'])
            if locale in ['zh-TW', 'zh-HK', 'zh_TW']: lang_code, wecom_lang = 'zh_TW', 'zh'
            elif locale in ['zh-CN', 'zh']: lang_code, wecom_lang = 'zh', 'zh'
            else: lang_code, wecom_lang = 'en', 'en'
            session['locale'] = lang_code
            wecom_qr_redirect_uri = request.host_url.rstrip('/') + '/auth/wecom-callback'
            response = make_response(render_template_string(LOGIN_PAGE_HTML, t=LOGIN_I18N[lang_code], current_lang=lang_code, wecom_lang=wecom_lang, app_display_name=APP_DISPLAY_NAME, app_subtitle=APP_SUBTITLE, next_url=request.args.get('next', ''), error=request.args.get('error', ''), wecom_corp_id=WECOM_CORP_ID, wecom_agent_id=WECOM_AGENT_ID, wecom_qr_redirect_uri=wecom_qr_redirect_uri))
            response.set_cookie('locale', lang_code, max_age=30*24*3600, path='/')
            return response

        @self.appbuilder.app.route('/login/local', methods=['POST'])
        def local_login():
            username, password = request.form.get('username', '').strip(), request.form.get('password', '')
            from flask_appbuilder.security.sqla.models import User
            from werkzeug.security import check_password_hash
            user = self.appbuilder.session.query(User).filter_by(username=username, active=True).first()
            if user and check_password_hash(user.password, password):
                login_user(user, remember=True)
                return redirect(request.form.get('next') or url_for('Superset.welcome'))
            return redirect(url_for('custom_login', error='Invalid credentials'))

        @self.appbuilder.app.route('/auth/wecom-callback', methods=['GET'])
        def wecom_qr_callback():
            code = request.args.get('code')
            if not code: return redirect(url_for('custom_login', error='Auth failed'))
            token_res = requests.get('https://qyapi.weixin.qq.com/cgi-bin/gettoken', params={'corpid': WECOM_CORP_ID, 'corpsecret': WECOM_SECRET}).json()
            access_token = token_res.get('access_token')
            user_info = requests.get('https://qyapi.weixin.qq.com/cgi-bin/user/getuserinfo', params={'access_token': access_token, 'code': code}).json()
            userid = user_info.get('UserId')
            mapped_username = WECOM_USER_MAP.get(userid, userid)
            from flask_appbuilder.security.sqla.models import User
            user = self.appbuilder.session.query(User).filter_by(username=mapped_username).first()
            if not user:
                user = User(username=mapped_username, first_name=mapped_username, last_name='', email=f"{mapped_username}@wecom.local", active=True)
                public_role = self.appbuilder.session.query(self.role_model).filter_by(name=self.auth_user_registration_role).first()
                if public_role: user.roles.append(public_role)
                self.appbuilder.session.add(user); self.appbuilder.session.commit()
            login_user(user, remember=True)
            return redirect(url_for('Superset.welcome'))

CUSTOM_SECURITY_MANAGER = CustomSecurityManager

# ==========================================
# 4. 数据维护模块 (多语言 + 批量录入)
# ==========================================
SQL_DB_DISPLAYNAME = os.environ.get("SQL_DB_DISPLAYNAME")
_mssql_uri = f"mssql+pymssql://{os.environ.get('SQL_USER')}:{quote_plus(os.environ.get('SQL_PASSWORD'))}@{os.environ.get('SQL_HOST')}:{os.environ.get('SQL_PORT')}/{os.environ.get('SQL_DB')}?charset=utf8"
SQLALCHEMY_BINDS = {SQL_DB_DISPLAYNAME: _mssql_uri}

class CapacityType(db.Model):
    __bind_key__ = SQL_DB_DISPLAYNAME
    __tablename__ = 'u_capacity_type'
    __table_args__ = {'schema': 'dbo'}
    id = Column(Integer, primary_key=True); type_name = Column(String(100), unique=True)
    def __repr__(self): return self.type_name

class K3Supplier(db.Model):
    __bind_key__ = SQL_DB_DISPLAYNAME
    __tablename__ = 'K3_Supplier'
    __table_args__ = {'schema': 'dbo'}
    供应商编码 = Column(String(50), primary_key=True); 供应商名称 = Column(String(200))
    def __repr__(self): return f"{self.供应商编码} | {self.供应商名称}"

class SupplierCapacity(db.Model):
    __bind_key__ = SQL_DB_DISPLAYNAME
    __tablename__ = 'u_sup_capacity'
    __table_args__ = {'schema': 'dbo'}
    id = Column(Integer, primary_key=True, autoincrement=True)
    sup_number = Column(String(50), ForeignKey('dbo.K3_Supplier.供应商编码')); supplier = relationship("K3Supplier", foreign_keys=[sup_number])
    sup_name = Column(String(200)); type_id = Column(Integer, ForeignKey('dbo.u_capacity_type.id'), nullable=True); cap_type_rel = relationship("CapacityType")
    capacity_date = Column(Date); capacity_qty = Column(Integer, default=0); forcast_qty = Column(Integer, default=0)
    create_date = Column(DateTime, default=datetime.now); create_user = Column(String(50))
    last_update_time = Column(DateTime, default=datetime.now, onupdate=datetime.now); last_update_user = Column(String(50))

class BIProduct(db.Model):
    __bind_key__ = SQL_DB_DISPLAYNAME
    __tablename__ = 'view_bi_product'
    __table_args__ = {'schema': 'dbo'}
    product_code = Column(String(50), primary_key=True); product_name = Column(String(200)); style_no = Column(String(100)); color_zh = Column(String(100))
    def __repr__(self): return f"{self.product_code} | {self.product_name}"

class SupplyCategory(db.Model):
    __bind_key__ = SQL_DB_DISPLAYNAME
    __tablename__ = 'u_supply_category'
    __table_args__ = {'schema': 'dbo'}
    id = Column(Integer, primary_key=True); product_code = Column(String(50), ForeignKey('dbo.view_bi_product.product_code')); product_rel = relationship("BIProduct")
    product_name = Column(String(200)); style_no = Column(String(100)); color_zh = Column(String(100)); type_id = Column(Integer, ForeignKey('dbo.u_capacity_type.id'), nullable=True); category_rel = relationship("CapacityType"); category_name = Column(String(100))
    create_user = Column(String(50)); create_date = Column(DateTime, default=datetime.now); last_update_user = Column(String(50)); last_update_time = Column(DateTime, default=datetime.now)

class CapacityTypeModelView(ModelView):
    datamodel = SQLAInterface(CapacityType); list_columns = ['type_name']; label_columns = {'type_name': _('Type Name')}

class SupplierCapacityModelView(ModelView):
    datamodel = SQLAInterface(SupplierCapacity)
    list_columns = ['sup_number', 'supplier.供应商名称', 'capacity_date', 'capacity_qty', 'forcast_qty', 'last_update_time']
    add_columns = edit_columns = ['supplier', 'cap_type_rel', 'capacity_date', 'capacity_qty', 'forcast_qty']
    label_columns = {'supplier': _('Select Supplier'), 'supplier.供应商名称': _('Supplier Name'), 'capacity_date': _('Capacity Date'), 'cap_type_rel': _('Capacity Type'), 'capacity_qty': _('Quantity'), 'forcast_qty': _('Forecast'), 'last_update_time': _('Last Update')}
    def pre_add(self, item):
        if item.capacity_date: item.capacity_date = item.capacity_date.replace(day=1)
        if item.supplier: item.sup_number, item.sup_name = item.supplier.供应商编码, item.supplier.供应商名称
        item.create_user = item.last_update_user = current_user.username
    def pre_update(self, item): self.pre_add(item)

class SupplyCategoryModelView(ModelView):
    datamodel = SQLAInterface(SupplyCategory)
    list_columns = ['product_code', 'product_name', 'style_no', 'color_zh', 'category_name', 'last_update_time']
    add_columns = edit_columns = ['product_rel', 'category_rel']
    label_columns = {'product_rel': _('Select Product'), 'product_name': _('Product Name'), 'style_no': _('Style No'), 'color_zh': _('Color'), 'category_rel': _('Category')}
    def _sync(self, item):
        if item.product_rel: item.product_code, item.product_name, item.style_no, item.color_zh = item.product_rel.product_code, item.product_rel.product_name, item.product_rel.style_no, item.product_rel.color_zh
        if item.category_rel: item.category_name = item.category_rel.type_name
        item.last_update_user = current_user.username
    def pre_add(self, item): self._sync(item); item.create_user = current_user.username
    def pre_update(self, item): self._sync(item)

# ==========================================
# 5. 批量录入视图 (Bulk Entry)
# ==========================================
BULK_FORM_HTML = """
{% extends "appbuilder/base.html" %}
{% block content %}
<div class="container well">
    <div class="row">
        <div class="col-md-12">
            <h3>{{ title }}</h3>
            <hr/>
        </div>
    </div>
    <form action="/suppliercapacitybulk/save" method="post" id="bulkForm">
        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}"/>
        
        <div class="row">
            <div class="col-md-4">
                <label>{{ _('Select Supplier') }}</label>
                <select name="sup_number" id="sup_select" class="form-control select2" required>
                    <option value="">-- {{ _('Search Supplier') }} --</option>
                    {% for s in suppliers %}<option value="{{ s.供应商编码 }}">{{ s.供应商编码 }} | {{ s.供应商名称 }}</option>{% endfor %}
                </select>
            </div>
            <div class="col-md-4">
                <label>{{ _('Capacity Type') }}</label>
                <select name="type_id" class="form-control select2">
                    <option value="">-- {{ _('None (Optional)') }} --</option>
                    {% for t in cap_types %}<option value="{{ t.id }}">{{ t.type_name }}</option>{% endfor %}
                </select>
            </div>
            <div class="col-md-2">
                <label>{{ _('Start Month') }}</label>
                <input type="month" id="start_month" class="form-control" value="{{ months[0].strftime('%Y-%m') }}">
            </div>
            <div class="col-md-2">
                <label>{{ _('End Month') }}</label>
                <input type="month" id="end_month" class="form-control" value="{{ months[-1].strftime('%Y-%m') }}">
            </div>
        </div>
        
        <br/>
        
        <table class="table table-bordered table-striped" id="monthTable">
            <thead>
                <tr>
                    <th style="width: 20%">{{ _('Month') }}</th>
                    <th>{{ _('Capacity Quantity') }}</th>
                    <th>{{ _('Forecast Quantity') }}</th>
                </tr>
            </thead>
            <tbody id="monthBody">
                <!-- 由 JavaScript 动态渲染 -->
            </tbody>
        </table>
        
        <div class="text-right">
            <button type="submit" class="btn btn-primary" style="padding: 10px 30px;">{{ _('Save All Data') }}</button>
            <a href="/suppliercapacitymodelview/list/" class="btn btn-default">{{ _('Back to List') }}</a>
        </div>
    </form>
</div>

<script>
$(document).ready(function() {
    $('.select2').select2({
        placeholder: "{{ _('Search or Select...') }}",
        allowClear: true,
        width: '100%'
    });
});

function renderMonths() {
    const startVal = document.getElementById('start_month').value;
    const endVal = document.getElementById('end_month').value;
    const tbody = document.getElementById('monthBody');
    tbody.innerHTML = '';
    
    if (!startVal || !endVal) return;

    const start = new Date(startVal + '-01T00:00:00');
    const end = new Date(endVal + '-01T00:00:00');
    
    if (start > end) {
        tbody.innerHTML = '<tr><td colspan="3" class="text-center text-danger">{{ _("Start month cannot be later than end month") }}</td></tr>';
        return;
    }

    let current = new Date(start);
    let index = 0;

    while (current <= end) {
        const year = current.getFullYear();
        const month = String(current.getMonth() + 1).padStart(2, '0');
        const dateStr = `${year}-${month}-01`;
        const displayStr = `${year}-${month}`;

        const row = `<tr>
            <td>
                <strong>${displayStr}</strong>
                <input type="hidden" name="date_${index}" value="${dateStr}">
            </td>
            <td><input type="number" name="qty_${index}" class="form-control" placeholder="0"></td>
            <td><input type="number" name="f_qty_${index}" class="form-control" placeholder="0"></td>
        </tr>`;
        
        tbody.insertAdjacentHTML('beforeend', row);
        current.setMonth(current.getMonth() + 1);
        index++;
        if (index > 60) break; 
    }

    let countInput = document.getElementById('row_count');
    if (!countInput) {
        countInput = document.createElement('input');
        countInput.type = 'hidden'; countInput.id = 'row_count'; countInput.name = 'row_count';
        document.getElementById('bulkForm').appendChild(countInput);
    }
    countInput.value = index;
}

document.getElementById('start_month').addEventListener('change', renderMonths);
document.getElementById('end_month').addEventListener('change', renderMonths);

document.addEventListener('DOMContentLoaded', function() {
    renderMonths();
});
</script>
{% endblock %}
"""

class SupplierCapacityBulkView(BaseView):
    route_base = "/suppliercapacitybulk"
    default_view = "form"
    
    @expose('/form')
    def form(self):
        suppliers = db.session.query(K3Supplier).all()
        cap_types = db.session.query(CapacityType).all()
        start_month = date.today().replace(day=1)
        months = [start_month + relativedelta(months=i) for i in range(12)]
        
        return render_template_string(
            BULK_FORM_HTML,
            suppliers=suppliers,
            cap_types=cap_types,
            months=months,
            title=_("Bulk Capacity & Forecast Entry"),
            base_template=appbuilder.base_template,
            appbuilder=appbuilder,
            _=_
        )

    @expose('/save', methods=['POST'])
    def save(self):
        sup_num = request.form.get('sup_number')
        type_id_raw = request.form.get('type_id')
        # 处理非必填的 type_id
        type_id = int(type_id_raw) if (type_id_raw and type_id_raw.isdigit()) else None
        
        row_count = int(request.form.get('row_count', 0))
        supplier = db.session.query(K3Supplier).filter_by(供应商编码=sup_num).first()
        
        for i in range(row_count):
            qty = request.form.get(f'qty_{i}', '').strip()
            f_qty = request.form.get(f'f_qty_{i}', '').strip()
            date_str = request.form.get(f'date_{i}')
            
            if (qty and qty.isdigit()) or (f_qty and f_qty.isdigit()):
                dt = datetime.strptime(date_str, '%Y-%m-%d').date()
                
                # 更新查询逻辑以支持 type_id 为空
                reg = db.session.query(SupplierCapacity).filter_by(
                    sup_number=sup_num, 
                    capacity_date=dt, 
                    type_id=type_id
                ).first()
                
                if not reg:
                    reg = SupplierCapacity(
                        sup_number=sup_num, 
                        sup_name=supplier.供应商名称, 
                        capacity_date=dt, 
                        type_id=type_id, 
                        create_user=current_user.username, 
                        create_date=datetime.now()
                    )
                    db.session.add(reg)
                
                reg.capacity_qty = int(qty) if (qty and qty.isdigit()) else 0
                reg.forcast_qty = int(f_qty) if (f_qty and f_qty.isdigit()) else 0
                reg.last_update_user = current_user.username
                reg.last_update_time = datetime.now()
        
        db.session.commit()
        return redirect('/suppliercapacitymodelview/list/')

def register_custom_views(app):
    with app.app_context():
        from superset.extensions import appbuilder
        m_label = "Data Maintenance"
        appbuilder.add_view(SupplierCapacityModelView, "Supplier Capacity", icon="fa-truck", category=m_label, category_icon="fa-database")
        appbuilder.add_view(SupplierCapacityBulkView, "Bulk Capacity Entry", icon="fa-list-ol", category=m_label)
        appbuilder.add_view(SupplyCategoryModelView, "Product Category", icon="fa-shopping-cart", category=m_label)
        appbuilder.add_view(CapacityTypeModelView, "Dictionary Management", icon="fa-tags", category=m_label)

FLASK_APP_MUTATOR = register_custom_views

# ==========================================
# 6. 常规配置 (AI, 缓存, 语言)
# ==========================================
FEATURE_FLAGS = {"ENABLE_TEMPLATE_PROCESSING": True, "ENABLE_AI_ASSISTANT": True, "SQL_LAB_AI_ASSIST": True}
REDIS_HOST, REDIS_PORT = "redis", 6379
RATELIMIT_STORAGE_URI = "redis://superset_cache:6379/2"
CACHE_CONFIG = {'CACHE_TYPE': 'RedisCache', 'CACHE_DEFAULT_TIMEOUT': 300, 'CACHE_KEY_PREFIX': 'superset_', 'CACHE_REDIS_URL': f'redis://{REDIS_HOST}:{REDIS_PORT}/0'}
DATA_CACHE_CONFIG = CACHE_CONFIG.copy(); DATA_CACHE_CONFIG['CACHE_REDIS_URL'] = f'redis://{REDIS_HOST}:{REDIS_PORT}/1'
RESULTS_BACKEND = RedisCache(host=REDIS_HOST, port=REDIS_PORT, db=2, key_prefix="superset_results_")
RESULTS_BACKEND_USE_GZIP = True

ENABLE_PROXY_FIX = True
PROXY_FIX_CONFIG = {"x_for": 1, "x_proto": 1, "x_host": 1, "x_port": 1, "x_prefix": 0}
PUBLIC_ROLE_LIKE = "Public"
LANGUAGES = {'zh': {'flag': 'cn', 'name': '简体中文'}, 'zh_TW': {'flag': 'hk', 'name': '繁体中文'}, 'en': {'flag': 'us', 'name': 'English'}}
BABEL_DEFAULT_LOCALE, BABEL_DEFAULT_TIMEZONE = 'zh', 'Asia/Shanghai'

OPENAI_API_BASE = os.environ.get("OPENAI_API_BASE")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
SUPERSET_AI_MODEL = os.environ.get("SUPERSET_AI_MODEL")
SUPERSET_ENABLE_MCP = os.environ.get("SUPERSET_ENABLE_MCP")

DB_USER, DB_PASS, DB_HOST, DB_PORT, DB_NAME = os.environ.get("POSTGRES_USER"), os.environ.get("POSTGRES_PASSWORD"), os.environ.get("POSTGRES_HOST"), os.environ.get("POSTGRES_PORT"), os.environ.get("POSTGRES_DB")
SQLALCHEMY_DATABASE_URI = f'postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}'
APP_NAME = os.environ.get("APP_NAME")
