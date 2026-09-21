import os
import io
import re
import uuid
import secrets
import sqlite3
from collections import defaultdict
from datetime import datetime, date
from time import time
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for, session,
    flash, g, send_file, jsonify, abort
)
from jinja2 import DictLoader
from markupsafe import Markup, escape
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

UPLOAD_FOLDER = "static/uploads"
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif"}
DATABASE = "unique_social.db"
SYSTEM_USERNAME = "pulse"

DAILY_PROMPTS = [
    "Is being liked a trap?",
    "Should schools teach disagreement as a skill?",
    "Is optimism a form of laziness?",
    "What should people stop pretending about?",
    "Is popularity proof of anything?",
    "When is silence actually a lie?",
]

app = Flask(__name__)
app.secret_key = os.environ.get("PULSE_SECRET", "dev-only-change-me-before-public")
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

_rate = defaultdict(list)

def rate_ok(key, n=40, window=30):
    now = time()
    _rate[key] = [t for t in _rate[key] if now - t < window]
    if len(_rate[key]) >= n:
        return False
    _rate[key].append(now)
    return True

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            flash("Sign in first.")
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper

def timeago(value):
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", ""))
    except ValueError:
        return str(value)
    sec = int((datetime.utcnow() - dt).total_seconds())
    if sec < 0:
        sec = 0
    steps = ((86400, "d"), (3600, "h"), (60, "m"))
    for size, label in steps:
        if sec >= size:
            return f"{sec // size}{label}"
    return "now"

def avatar_color(name):
    h = 0
    for ch in name or "?":
        h = (h * 31 + ord(ch)) & 0xFFFFFF
    palette = ["#3b82f6", "#10b981", "#f59e0b", "#8b5cf6", "#ef4444", "#06b6d4", "#f97316"]
    return palette[h % len(palette)]

def format_pulse(content):
    text = str(content or "")
    out = str(escape(text))
    out = re.sub(r"(#\w+)", r'<a href="/tag/\1" class="tag">\1</a>', out)
    out = re.sub(r"(?<!\w)@([A-Za-z0-9_]{1,32})", r'<a href="/profile/\1" class="tag">@\1</a>', out)
    return Markup(out)

def pulse_score(resonates, replies, breaks):
    return int(resonates or 0) + 2 * int(replies or 0) + 3 * int(breaks or 0)

# --- ADVANCED TEMPLATES ---

BASE_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Pulse</title>
    <style>
        :root {
            --bg: #0b0e14;
            --card-bg: #151a21;
            --text: #f3f4f6;
            --text-muted: #9ca3af;
            --border: #2d3748;
            --primary: #3b82f6;
            --primary-hover: #2563eb;
            --danger: #ef4444;
            --success: #10b981;
            --warn: #f59e0b;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
        body { background: var(--bg); color: var(--text); padding-top: 65px; padding-bottom: 75px; overflow-x: hidden; }
        nav { background: rgba(11,14,20,.9); border-bottom: 1px solid var(--border); padding: 0 16px; display: flex; justify-content: space-between; align-items: center; position: fixed; top: 0; left: 0; right: 0; z-index: 100; height: 60px; backdrop-filter: blur(12px); }
        .logo { font-size: 1.35rem; font-weight: 800; color: var(--text); text-decoration: none; letter-spacing: -.5px; }
        .nav-links { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
        .nav-links a { color: var(--text-muted); text-decoration: none; font-weight: 500; font-size: .85rem; }
        .nav-links a:hover { color: var(--primary); }
        .container { max-width: 600px; margin: 0 auto; padding: 0 12px; }
        .card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 14px; padding: 18px; margin-bottom: 16px; position: relative; }
        .btn { background: var(--primary); color: #fff; border: none; padding: 8px 18px; border-radius: 20px; font-weight: 600; cursor: pointer; text-decoration: none; font-size: .85rem; display: inline-block; }
        .btn:hover { background: var(--primary-hover); }
        .btn-outline { background: transparent; border: 1px solid var(--border); color: var(--text); }
        .btn-danger { background: transparent; border: 1px solid var(--danger); color: var(--danger); }
        .btn-danger-solid { background: var(--danger); color: #fff; border: none; }
        .form-group { margin-bottom: 1rem; }
        label { display: block; margin-bottom: .4rem; font-size: .85rem; color: var(--text-muted); font-weight: 500; }
        input, textarea { width: 100%; padding: 12px; border-radius: 10px; border: 1px solid var(--border); background: #090d16; color: var(--text); font-size: .95rem; outline: none; }
        input:focus, textarea:focus { border-color: var(--primary); }
        .alert { padding: 12px; border-radius: 10px; margin-bottom: 1rem; background: rgba(59,130,246,.1); border: 1px solid rgba(59,130,246,.3); color: #60a5fa; text-align: center; font-size: .85rem; }
        .post-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; gap: 8px; }
        .who { display: flex; align-items: center; gap: 8px; min-width: 0; }
        .av { width: 32px; height: 32px; border-radius: 50%; display: grid; place-items: center; font-size: .75rem; font-weight: 800; color: #fff; flex-shrink: 0; }
        .username { font-weight: 700; font-size: .9rem; color: var(--text); text-decoration: none; }
        .timestamp { font-size: .75rem; color: var(--text-muted); white-space: nowrap; }
        .post-content { font-size: .95rem; line-height: 1.5; margin-bottom: 12px; word-break: break-word; }
        .image-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(130px, 1fr)); gap: 8px; margin-bottom: 12px; }
        .post-image { width: 100%; height: 130px; border-radius: 8px; object-fit: cover; border: 1px solid var(--border); cursor: pointer; transition: opacity .2s; }
        .post-image:hover { opacity: .9; }
        .post-image.single { height: auto; max-height: 350px; grid-column: 1 / -1; }
        .quote-box { background: rgba(0,0,0,.25); border-left: 3px solid var(--primary); padding: 10px 12px; border-radius: 0 8px 8px 0; margin-bottom: 12px; font-size: .9rem; }
        .post-actions { display: flex; gap: 10px; margin-top: 12px; border-top: 1px solid var(--border); padding-top: 12px; font-size: .8rem; color: var(--text-muted); align-items: center; flex-wrap: wrap; }
        .linkish { background: none; border: none; color: var(--text-muted); cursor: pointer; font-size: .8rem; padding: 0; }
        .linkish:hover, .post-actions a:hover { color: var(--primary); }
        .post-actions a { color: var(--text-muted); text-decoration: none; }
        .tag { color: var(--primary); text-decoration: none; font-weight: 600; }
        .badge { display: inline-block; font-size: .65rem; font-weight: 800; letter-spacing: .06em; padding: 2px 7px; border-radius: 999px; border: 1px solid var(--warn); color: var(--warn); }
        .badge-sys { border-color: var(--primary); color: var(--primary); }
        .badge-dead { border-color: var(--danger); color: var(--danger); }
        .feeds { display: flex; gap: 10px; flex-wrap: wrap; font-size: .85rem; }
        .feeds a { color: var(--text-muted); text-decoration: none; font-weight: 600; }
        .feeds a.on { color: var(--primary); }
        .mobile-nav { position: fixed; bottom: 0; left: 0; right: 0; background: rgba(11,14,20,.9); border-top: 1px solid var(--border); display: flex; justify-content: space-around; z-index: 100; height: 60px; align-items: center; backdrop-filter: blur(12px); }
        .mobile-nav a { color: var(--text-muted); text-decoration: none; font-size: 1.25rem; }
        .row { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
        /* Lightbox Overlay */
        #lightbox { display:none; position:fixed; z-index:1000; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,.9); justify-content:center; align-items:center; }
        #lightbox img { max-width:90%; max-height:90%; border-radius:8px; object-fit:contain; }
        #lightbox span { position:absolute; top:20px; right:30px; font-size:2rem; color:#fff; cursor:pointer; }
    </style>
</head>
<body>
    <nav>
        <a href="{{ url_for('index') }}" class="logo">⚡ Pulse</a>
        <div class="nav-links">
            <a href="{{ url_for('index') }}">Home</a>
            <a href="{{ url_for('explore') }}">Explore</a>
            {% if session.get('user_id') %}
                <a href="{{ url_for('messages') }}">Inbox</a>
                <a href="{{ url_for('profile', username=session.get('username')) }}">Profile</a>
                <a href="{{ url_for('logout') }}" style="color:var(--danger)">Logout</a>
            {% else %}
                <a href="{{ url_for('login') }}" class="btn" style="padding:6px 14px;">Log In</a>
            {% endif %}
        </div>
    </nav>
    <div class="container" style="margin-top:16px;">
        {% with messages = get_flashed_messages() %}
            {% if messages %}
                {% for message in messages %}<div class="alert">{{ message }}</div>{% endfor %}
            {% endif %}
        {% endwith %}
        {% block content %}{% endblock %}
    </div>
    <div id="lightbox" onclick="this.style.display='none'">
        <span onclick="document.getElementById('lightbox').style.display='none'">&times;</span>
        <img id="lightbox-img" src="" alt="">
    </div>
    <div class="mobile-nav">
        <a href="{{ url_for('index') }}">🏠</a>
        <a href="{{ url_for('explore') }}">🔍</a>
        <a href="{{ url_for('messages') }}">💬</a>
    </div>
    <script>
    function openLightbox(src) {
        document.getElementById('lightbox-img').src = src;
        document.getElementById('lightbox').style.display = 'flex';
    }
    </script>
</body>
</html>
"""

INDEX_TEMPLATE = """
{% extends "base.html" %}
{% block content %}
{% if session.get('user_id') %}
<div class="card">
    <form method="POST" action="{{ url_for('create_post') }}" enctype="multipart/form-data">
        <input type="hidden" name="csrf" value="{{ csrf_token }}">
        <textarea name="content" rows="3" placeholder="Take a stance. Use #tags and @names." style="resize:none;background:transparent;border:none;font-size:1rem;color:var(--text);outline:none;"></textarea>
        <div class="row" style="margin-top:12px;border-top:1px solid var(--border);padding-top:10px;flex-wrap:wrap;">
            <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
                <label style="margin:0;cursor:pointer;background:#090d16;border:1px solid var(--border);padding:6px 12px;border-radius:10px;font-size:.8rem;color:var(--text-muted);">
                    📷 Upload Images (Up to 20)
                    <input type="file" name="files" accept="image/*" multiple style="display:none;" onchange="this.parentElement.style.borderColor='var(--primary)';">
                </label>
                <label style="margin:0;display:flex;gap:6px;align-items:center;color:var(--warn);font-size:.8rem;">
                    <input type="checkbox" name="is_stance" value="1" style="width:auto;"> Stance
                </label>
            </div>
            <button type="submit" class="btn">Broadcast</button>
        </div>
    </form>
</div>
{% endif %}

<div class="row" style="margin-bottom:12px;">
    <span style="font-weight:700;font-size:.95rem;color:var(--text-muted);">{{ page_title | default('Timeline') }}</span>
    <div class="feeds">
        <a href="{{ url_for('index', feed='global') }}" class="{{ 'on' if feed_type=='global' }}">Unfiltered</a>
        <a href="{{ url_for('index', feed='clash') }}" class="{{ 'on' if feed_type=='clash' }}">Clash</a>
    </div>
</div>

{% for post in posts %}
    {% include "post_card.html" %}
{% else %}
<p style="color:var(--text-muted);text-align:center;padding:40px 0;">The network is quiet. Take a stance.</p>
{% endfor %}
{% endblock %}
"""

POST_CARD_TEMPLATE = """
<div class="card">
    <div class="post-header">
        <div class="who">
            <div class="av" style="background:{{ post.username|avatar }}">{{ post.username[:1]|upper }}</div>
            <div>
                <a href="{{ url_for('profile', username=post.username) }}" class="username">@{{ post.username }}</a>
                {% if post.is_prompt %}<span class="badge badge-sys">PROMPT</span>{% endif %}
                {% if post.is_stance and not post.retracted %}<span class="badge">STANCE</span>{% endif %}
                {% if post.retracted %}<span class="badge badge-dead">RETRACTED</span>{% endif %}
            </div>
        </div>
        <span class="timestamp">{{ post.created_at|ago }}</span>
    </div>
    {% if post.retracted %}
        <div class="post-content" style="color:var(--text-muted);">This stance was retracted in public.</div>
    {% else %}
        {% if post.quoted_post %}
        <div class="quote-box">
            <div style="font-weight:700;font-size:.8rem;margin-bottom:4px;color:var(--text-muted);">@{{ post.quoted_post.username }}</div>
            <div>{{ post.quoted_post.content|pulse }}</div>
        </div>
        {% endif %}
        <div class="post-content">{{ post.formatted_content }}</div>
        {% if post.image_filenames %}
            {% set imgs = post.image_filenames.split(',') %}
            <div class="image-grid">
                {% for img in imgs %}
                    <img src="{{ url_for('static', filename='uploads/' + img) }}" class="post-image {% if imgs|length == 1 %}single{% endif %}" onclick="openLightbox(this.src)" alt="">
                {% endfor %}
            </div>
        {% endif %}
    {% endif %}
    <div style="font-size:.75rem;color:var(--text-muted);margin-bottom:6px;">Score {{ post.score }} · ⚡ {{ post.resonates }} · ⚔ {{ post.breaks }}</div>
    <div class="post-actions">
        {% if session.get('user_id') and not post.retracted %}
        <form method="POST" action="{{ url_for('resonate_post', post_id=post.id) }}" style="display:inline;">
            <input type="hidden" name="csrf" value="{{ csrf_token }}">
            <button class="linkish" type="submit">⚡ Resonate</button>
        </form>
        <button class="linkish" type="button" onclick="document.getElementById('brk-{{ post.id }}').style.display='block'">⚔ Break</button>
        <button class="linkish" type="button" onclick="document.getElementById('qt-{{ post.id }}').style.display='block'">🔁 Repost</button>
        <form method="POST" action="{{ url_for('bookmark_post', post_id=post.id) }}" style="display:inline;">
            <input type="hidden" name="csrf" value="{{ csrf_token }}">
            <button class="linkish" type="submit">🔖 Save</button>
        </form>
        {% endif %}
        <a href="{{ url_for('post_detail', post_id=post.id) }}">💬 {{ post.comments_count }} Replies</a>
        {% if session.get('user_id') and session.get('user_id') == post.user_id %}
        <form method="POST" action="{{ url_for('delete_post', post_id=post.id) }}" style="display:inline;margin-left:auto;" onsubmit="return confirm('Delete this post permanently?');">
            <input type="hidden" name="csrf" value="{{ csrf_token }}">
            <button class="linkish" type="submit" style="color:var(--danger);">Delete</button>
        </form>
        {% elif session.get('user_id') and session.get('user_id') != post.user_id %}
        <a href="{{ url_for('chat', recipient_id=post.user_id) }}" style="margin-left:auto;">Message</a>
        {% endif %}
    </div>
    {% if session.get('user_id') and not post.retracted %}
    <form id="brk-{{ post.id }}" method="POST" action="{{ url_for('break_post', post_id=post.id) }}" style="display:none;margin-top:8px;">
        <input type="hidden" name="csrf" value="{{ csrf_token }}">
        <textarea name="reason" rows="2" minlength="20" required placeholder="Break it with a reason (20+ chars)..." style="font-size:.85rem;margin-bottom:6px;"></textarea>
        <div style="display:flex;justify-content:flex-end;">
            <button class="btn btn-danger" type="submit" style="padding:4px 10px;font-size:.75rem;">Publish Break</button>
        </div>
    </form>
    <form id="qt-{{ post.id }}" method="POST" action="{{ url_for('repost_post', post_id=post.id) }}" style="display:none;margin-top:8px;">
        <input type="hidden" name="csrf" value="{{ csrf_token }}">
        <textarea name="content" rows="2" placeholder="Add commentary to your repost..." style="font-size:.85rem;margin-bottom:6px;"></textarea>
        <div style="display:flex;justify-content:flex-end;">
            <button class="btn" type="submit" style="padding:4px 10px;font-size:.75rem;">Confirm Repost</button>
        </div>
    </form>
    {% endif %}
</div>
"""

POST_DETAIL_TEMPLATE = """
{% extends "base.html" %}
{% block content %}
{% include "post_card.html" %}

<div class="card">
    <h3 style="font-size:.95rem;margin-bottom:10px;">Reply</h3>
    {% if session.get('user_id') %}
    <form method="POST">
        <input type="hidden" name="csrf" value="{{ csrf_token }}">
        <textarea name="content" rows="2" required placeholder="Make the reply count..." style="resize:none;font-size:.9rem;margin-bottom:10px;"></textarea>
        <div style="display:flex;justify-content:flex-end;">
            <button type="submit" class="btn" style="padding:6px 14px;">Reply</button>
        </div>
    </form>
    {% else %}
    <p style="color:var(--text-muted);">Log in to reply.</p>
    {% endif %}
</div>

<div style="font-weight:700;margin-bottom:12px;font-size:.95rem;color:var(--text-muted);">Thread</div>
{% for comment in comments %}
<div class="card" style="padding:12px;">
    <div class="post-header" style="margin-bottom:4px;">
        <a href="{{ url_for('profile', username=comment.username) }}" class="username" style="font-size:.85rem;">@{{ comment.username }}</a>
        <span class="timestamp">{{ comment.created_at|ago }}</span>
    </div>
    <div style="font-size:.9rem;">{{ comment.content|pulse }}</div>
</div>
{% else %}
<p style="color:var(--text-muted);text-align:center;padding:20px;font-size:.85rem;">No replies yet.</p>
{% endfor %}
{% endblock %}
"""

EXPLORE_TEMPLATE = """
{% extends "base.html" %}
{% block content %}
<div class="card" style="padding:24px;">
    <h2 style="margin-bottom:8px;font-size:1.25rem;text-align:center;">Explore Pulse</h2>
    <form method="GET" action="{{ url_for('explore') }}" style="display:flex;gap:8px;margin-bottom:20px;">
        <input type="text" name="q" value="{{ query }}" placeholder="Search handles or keywords...">
        <button type="submit" class="btn">Search</button>
    </form>
    {% if users %}
    <h3 style="font-size:.95rem;margin-bottom:10px;color:var(--text-muted);">Matching users</h3>
    {% for u in users %}
    <div class="row" style="padding:10px 0;border-bottom:1px solid var(--border);">
        <a href="{{ url_for('profile', username=u.username) }}" class="username">@{{ u.username }}</a>
        <a href="{{ url_for('profile', username=u.username) }}" class="btn btn-outline" style="padding:4px 12px;font-size:.75rem;">View</a>
    </div>
    {% endfor %}
    {% endif %}
</div>
{% endblock %}
"""

LOGIN_TEMPLATE = """
{% extends "base.html" %}
{% block content %}
<div class="card" style="max-width:380px;margin:30px auto;padding:24px;">
    <h2 style="margin-bottom:1.2rem;text-align:center;font-size:1.4rem;">Sign in to Pulse</h2>
    <form method="POST">
        <input type="hidden" name="csrf" value="{{ csrf_token }}">
        <div class="form-group"><label>Username</label><input type="text" name="username" required autocomplete="username"></div>
        <div class="form-group"><label>Password</label><input type="password" name="password" required autocomplete="current-password"></div>
        <button type="submit" class="btn" style="width:100%;margin-top:.5rem;padding:10px;">Log In</button>
    </form>
    <p style="text-align:center;font-size:.85rem;color:var(--text-muted);margin-top:1.2rem;">
        No account? <a href="{{ url_for('register') }}" style="color:var(--primary);text-decoration:none;">Sign up</a>
    </p>
</div>
{% endblock %}
"""

REGISTER_TEMPLATE = """
{% extends "base.html" %}
{% block content %}
<div class="card" style="max-width:380px;margin:30px auto;padding:24px;">
    <h2 style="margin-bottom:1.2rem;text-align:center;font-size:1.4rem;">Create account</h2>
    <form method="POST">
        <input type="hidden" name="csrf" value="{{ csrf_token }}">
        <div class="form-group"><label>Username</label><input type="text" name="username" required autocomplete="username"></div>
        <div class="form-group"><label>Password</label><input type="password" name="password" required autocomplete="new-password" minlength="6"></div>
        <button type="submit" class="btn" style="width:100%;margin-top:.5rem;padding:10px;">Register</button>
    </form>
    <p style="text-align:center;font-size:.85rem;color:var(--text-muted);margin-top:1.2rem;">
        Have an account? <a href="{{ url_for('login') }}" style="color:var(--primary);text-decoration:none;">Log In</a>
    </p>
</div>
{% endblock %}
"""

PROFILE_TEMPLATE = """
{% extends "base.html" %}
{% block content %}
<div class="card" style="text-align:center;padding:24px;">
    <div class="av" style="width:64px;height:64px;margin:0 auto 10px;font-size:1.3rem;background:{{ profile_user.username|avatar }}">{{ profile_user.username[:1]|upper }}</div>
    <h2 style="margin-bottom:4px;font-size:1.3rem;">@{{ profile_user.username }}</h2>
    <p style="color:var(--text-muted);font-size:.75rem;margin-bottom:12px;">Joined {{ profile_user.created_at }}</p>
    <p style="font-size:.95rem;margin-bottom:16px;">{{ profile_user.bio if profile_user.bio else 'No bio written yet.' }}</p>
    {% if session.get('user_id') and session.get('user_id') != profile_user.id %}
    <form method="POST" action="{{ url_for('block_user', user_id=profile_user.id) }}" style="margin-bottom:12px;">
        <input type="hidden" name="csrf" value="{{ csrf_token }}">
        <button type="submit" class="btn btn-danger" style="padding:4px 12px;font-size:.75rem;">Block @{{ profile_user.username }}</button>
    </form>
    {% endif %}
    {% if session.get('user_id') == profile_user.id %}
    <form method="POST" action="{{ url_for('update_bio') }}" style="text-align:left;border-top:1px solid var(--border);padding-top:16px;margin-bottom:16px;">
        <input type="hidden" name="csrf" value="{{ csrf_token }}">
        <div class="form-group" style="margin-bottom:8px;">
            <label>Update Bio</label>
            <input type="text" name="bio" value="{{ profile_user.bio }}">
        </div>
        <button type="submit" class="btn btn-outline" style="padding:5px 14px;font-size:.75rem;">Save Bio</button>
    </form>
    <form method="POST" action="{{ url_for('update_password') }}" style="text-align:left;border-top:1px solid var(--border);padding-top:16px;margin-bottom:16px;">
        <input type="hidden" name="csrf" value="{{ csrf_token }}">
        <div class="form-group" style="margin-bottom:8px;">
            <label>Change Password</label>
            <input type="password" name="old_password" placeholder="Current password" required style="margin-bottom:6px;">
            <input type="password" name="new_password" placeholder="New password (min 6 chars)" required minlength="6">
        </div>
        <button type="submit" class="btn btn-outline" style="padding:5px 14px;font-size:.75rem;">Update Password</button>
    </form>
    <form method="POST" action="{{ url_for('delete_account') }}" style="text-align:left;border-top:1px solid var(--border);padding-top:16px;" onsubmit="return confirm('WARNING: This will permanently delete your account. Continue?');">
        <input type="hidden" name="csrf" value="{{ csrf_token }}">
        <button type="submit" class="btn btn-danger-solid" style="padding:5px 14px;font-size:.75rem;width:100%;">Delete Account</button>
    </form>
    {% endif %}
</div>
<div style="font-weight:700;margin-bottom:12px;font-size:1rem;color:var(--text-muted);">Posts</div>
{% for post in posts %}
    {% include "post_card.html" %}
{% else %}
<p style="color:var(--text-muted);text-align:center;padding:20px;">No posts yet.</p>
{% endfor %}
{% endblock %}
"""

MESSAGES_TEMPLATE = """
{% extends "base.html" %}
{% block content %}
<div style="font-weight:700;margin-bottom:12px;font-size:1rem;color:var(--text-muted);">Messages</div>
<div class="card">
    {% for u in users %}
    <div class="row" style="padding:10px 0;border-bottom:1px solid var(--border);">
        <span style="font-weight:600;font-size:.9rem;">@{{ u.username }}</span>
        <a href="{{ url_for('chat', recipient_id=u.id) }}" class="btn" style="padding:5px 12px;font-size:.75rem;">Chat</a>
    </div>
    {% else %}
    <p style="color:var(--text-muted);text-align:center;padding:20px;">No other users available.</p>
    {% endfor %}
</div>
{% endblock %}
"""

CHAT_TEMPLATE = """
{% extends "base.html" %}
{% block content %}
<div class="card" style="display:flex;flex-direction:column;height:68vh;padding:12px;">
    <div class="row" style="border-bottom:1px solid var(--border);padding-bottom:8px;margin-bottom:8px;">
        <div style="display:flex;align-items:center;gap:8px;">
            <h3 style="font-size:.95rem;">@{{ recipient.username }}</h3>
            <span style="font-size:.7rem;color:var(--success);">● Live</span>
        </div>
        <div style="display:flex;gap:6px;">
            <button type="button" class="btn btn-outline" onclick="startCall('voice')" style="padding:4px 10px;font-size:.75rem;">📞 Voice</button>
            <button type="button" class="btn btn-outline" onclick="startCall('video')" style="padding:4px 10px;font-size:.75rem;">📹 Video</button>
        </div>
    </div>
    <div id="chat-box" style="flex:1;overflow-y:auto;display:flex;flex-direction:column;gap:8px;padding-right:4px;"></div>
    <div id="typing-indicator" style="font-size:.75rem;color:var(--text-muted);height:18px;margin-bottom:4px;"></div>
    <form id="chat-form" method="POST" style="display:flex;gap:6px;" onsubmit="sendMsg(event)">
        <input type="hidden" name="csrf" value="{{ csrf_token }}">
        <input type="text" id="msg-input" name="content" placeholder="Reply..." autocomplete="off" required style="flex:1;border-radius:20px;padding:8px 14px;font-size:.9rem;" oninput="pingTyping()">
        <button type="submit" class="btn" style="border-radius:20px;padding:8px 14px;">Send</button>
    </form>
</div>
<script>
const box = document.getElementById('chat-box');
const me = {{ session.get('user_id')|int }};
let typingTimer = null;

function startCall(type) {
    alert(type.toUpperCase() + ' call feature initialized. Connecting secure WebRTC stream to @{{ recipient.username }}...');
}

async function pingTyping() {
    await fetch('/chat/{{ recipient.id }}/typing', {method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'}, body: 'csrf={{ csrf_token }}'});
}

async function sendMsg(e) {
    e.preventDefault();
    const input = document.getElementById('msg-input');
    const val = input.value.trim();
    if (!val) return;
    await fetch('/chat/{{ recipient.id }}', {
        method: 'POST',
        headers: {'Content-Type': 'application/x-www-form-urlencoded'},
        body: 'csrf={{ csrf_token }}&content=' + encodeURIComponent(val)
    });
    input.value = '';
    pull();
}

function paint(data) {
  box.innerHTML = '';
  if (!data.messages.length) {
    box.innerHTML = '<p style="color:var(--text-muted);text-align:center;margin:auto;font-size:.85rem;">Start the conversation.</p>';
  } else {
    data.messages.forEach(msg => {
      const mine = msg.sender_id === me;
      const el = document.createElement('div');
      el.style.cssText = 'max-width:75%;padding:8px 12px;border-radius:12px;' + (mine ? 'background:var(--primary);margin-left:auto;color:white;' : 'background:var(--border);margin-right:auto;');
      el.innerHTML = '<div style="font-size:.9rem;word-break:break-word;"></div><div style="font-size:.6rem;opacity:.8;margin-top:2px;text-align:right;"></div>';
      el.children[0].textContent = msg.content;
      el.children[1].textContent = msg.timestamp + (mine && msg.read ? ' ✓✓' : ' ✓');
      box.appendChild(el);
    });
    box.scrollTop = box.scrollHeight;
  }
  
  const indicator = document.getElementById('typing-indicator');
  if (data.is_typing) {
      indicator.textContent = '@{{ recipient.username }} is typing...';
  } else {
      indicator.textContent = '';
  }
}

async function pull() {
  const res = await fetch({{ url_for('chat_json', recipient_id=recipient.id)|tojson }});
  const data = await res.json();
  paint(data);
}
pull();
setInterval(pull, 2000);
</script>
{% endblock %}
"""

app.jinja_loader = DictLoader({
    "base.html": BASE_TEMPLATE,
    "index.html": INDEX_TEMPLATE,
    "post_card.html": POST_CARD_TEMPLATE,
    "post_detail.html": POST_DETAIL_TEMPLATE,
    "explore.html": EXPLORE_TEMPLATE,
    "login.html": LOGIN_TEMPLATE,
    "register.html": REGISTER_TEMPLATE,
    "profile.html": PROFILE_TEMPLATE,
    "messages.html": MESSAGES_TEMPLATE,
    "chat.html": CHAT_TEMPLATE,
})
app.jinja_env.filters["ago"] = timeago
app.jinja_env.filters["avatar"] = avatar_color
app.jinja_env.filters["pulse"] = format_pulse

# --- DATABASE & CORE ROUTING ---

def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()

def add_column_safe(db, table, col, spec):
    cols = [r[1] for r in db.execute(f"PRAGMA table_info({table})").fetchall()]
    if col not in cols:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {spec}")

def init_db():
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            bio TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            image_filenames TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        );
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id INTEGER NOT NULL,
            recipient_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            read INTEGER DEFAULT 0,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS reactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            reason TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(post_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS bookmarks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            post_id INTEGER NOT NULL,
            UNIQUE(user_id, post_id)
        );
        CREATE TABLE IF NOT EXISTS blocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            blocked_id INTEGER NOT NULL,
            UNIQUE(user_id, blocked_id)
        );
    """)
    for col, spec in [
        ("is_stance", "INTEGER DEFAULT 0"),
        ("retracted", "INTEGER DEFAULT 0"),
        ("is_prompt", "INTEGER DEFAULT 0"),
        ("quote_of_id", "INTEGER"),
        ("break_of_id", "INTEGER"),
        ("image_filenames", "TEXT"),
    ]:
        add_column_safe(db, "posts", col, spec)

    add_column_safe(db, "messages", "read", "INTEGER DEFAULT 0")

    if not db.execute("SELECT 1 FROM users WHERE username = ?", (SYSTEM_USERNAME,)).fetchone():
        db.execute(
            "INSERT INTO users (username, password, bio) VALUES (?, ?, ?)",
            (SYSTEM_USERNAME, generate_password_hash(secrets.token_urlsafe(24)), "The house account. Daily pressure."),
        )
    db.commit()

def ensure_daily_prompt():
    db = get_db()
    pulse = db.execute("SELECT * FROM users WHERE username = ?", (SYSTEM_USERNAME,)).fetchone()
    if not pulse:
        return
    today = date.today().isoformat()
    exists = db.execute(
        "SELECT 1 FROM posts WHERE user_id = ? AND is_prompt = 1 AND date(created_at) = ?",
        (pulse["id"], today),
    ).fetchone()
    if exists:
        return
    text = DAILY_PROMPTS[date.today().toordinal() % len(DAILY_PROMPTS)]
    content = f"Today's pressure: “{text}” Answer with a Stance."
    db.execute(
        "INSERT INTO posts (user_id, content, is_stance, is_prompt) VALUES (?, ?, 1, 1)",
        (pulse["id"], content),
    )
    db.commit()

with app.app_context():
    init_db()
    ensure_daily_prompt()

POST_SELECT = """
    SELECT posts.*, users.username,
           (SELECT COUNT(*) FROM reactions WHERE reactions.post_id = posts.id AND kind = 'resonate') AS resonates,
           (SELECT COUNT(*) FROM reactions WHERE reactions.post_id = posts.id AND kind = 'break') AS breaks,
           (SELECT COUNT(*) FROM comments WHERE comments.post_id = posts.id) AS comments_count
    FROM posts
    JOIN users ON posts.user_id = users.id
"""

def hydrate(rows):
    db = get_db()
    posts = []
    for row in rows:
        post = dict(row)
        post["formatted_content"] = format_pulse(post["content"])
        post["score"] = pulse_score(post.get("resonates"), post.get("comments_count"), post.get("breaks"))
        if post.get("quote_of_id"):
            q_row = db.execute(f"{POST_SELECT} WHERE posts.id = ?", (post["quote_of_id"],)).fetchone()
            if q_row:
                post["quoted_post"] = dict(q_row)
        posts.append(post)
    return posts

@app.context_processor
def inject_csrf():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return {"csrf_token": session.get("csrf")}

@app.before_request
def guard():
    if request.endpoint == "static":
        return
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    if request.method == "POST":
        if request.form.get("csrf") != session.get("csrf"):
            abort(400)
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "?")
        if not rate_ok(f"{ip}:{request.path}"):
            flash("Slow down.")
            return redirect(request.referrer or url_for("index"))

@app.route("/", methods=["GET", "HEAD"])
def index():
    ensure_daily_prompt()
    feed_type = request.args.get("feed", "global")
    db = get_db()
    where = "1=1"
    if "user_id" in session:
        where += " AND posts.user_id NOT IN (SELECT blocked_id FROM blocks WHERE user_id = ?)"
        params = (session["user_id"],)
    else:
        params = ()

    if feed_type == "clash":
        where += " AND (posts.break_of_id IS NOT NULL OR posts.is_stance = 1)"
    rows = db.execute(f"{POST_SELECT} WHERE {where} ORDER BY posts.created_at DESC LIMIT 50", params).fetchall()
    return render_template("index.html", posts=hydrate(rows), feed_type=feed_type)

@app.route("/tag/<tagname>")
def tag_feed(tagname):
    db = get_db()
    tag_query = f"%{tagname}%"
    rows = db.execute(f"{POST_SELECT} WHERE posts.content LIKE ? ORDER BY posts.created_at DESC LIMIT 50", (tag_query,)).fetchall()
    return render_template("index.html", posts=hydrate(rows), feed_type="global", page_title=f"Tag: {tagname}")

@app.route("/post/<int:post_id>", methods=["GET", "POST"])
def post_detail(post_id):
    db = get_db()
    if request.method == "POST":
        if "user_id" not in session:
            return redirect(url_for("login"))
        content = request.form.get("content", "").strip()
        if content:
            db.execute("INSERT INTO comments (post_id, user_id, content) VALUES (?, ?, ?)", (post_id, session["user_id"], content))
            db.commit()
        return redirect(url_for("post_detail", post_id=post_id))
    row = db.execute(f"{POST_SELECT} WHERE posts.id = ?", (post_id,)).fetchone()
    if not row:
        flash("Post not found.")
        return redirect(url_for("index"))
    post = hydrate([row])[0]
    comments = db.execute("SELECT comments.*, users.username FROM comments JOIN users ON comments.user_id = users.id WHERE comments.post_id = ? ORDER BY comments.created_at ASC", (post_id,)).fetchall()
    return render_template("post_detail.html", post=post, comments=comments)

@app.route("/post", methods=["POST"])
@login_required
def create_post():
    content = request.form.get("content", "").strip()
    is_stance = 1 if request.form.get("is_stance") else 0
    uploaded_files = request.files.getlist("files")
    saved_filenames = []
    
    for file in uploaded_files[:20]:
        if file and file.filename and allowed_file(file.filename):
            filename = f"{uuid.uuid4().hex}_{secure_filename(file.filename)}"
            file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
            saved_filenames.append(filename)
            
    filenames_str = ",".join(saved_filenames) if saved_filenames else None
    
    if not content and not filenames_str:
        flash("Posts cannot be empty.")
        return redirect(url_for("index"))
        
    db = get_db()
    db.execute("INSERT INTO posts (user_id, content, image_filenames, is_stance) VALUES (?, ?, ?, ?)", (session["user_id"], content, filenames_str, is_stance))
    db.commit()
    return redirect(url_for("index"))

@app.route("/post/<int:post_id>/repost", methods=["POST"])
@login_required
def repost_post(post_id):
    content = request.form.get("content", "").strip()
    db = get_db()
    original = db.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    if not original:
        flash("Original post not found.")
        return redirect(url_for("index"))
    db.execute("INSERT INTO posts (user_id, content, quote_of_id, is_stance) VALUES (?, ?, ?, 1)", (session["user_id"], content or "Reposted a stance.", post_id))
    db.commit()
    flash("Successfully reposted.")
    return redirect(url_for("index"))

@app.route("/post/<int:post_id>/bookmark", methods=["POST"])
@login_required
def bookmark_post(post_id):
    db = get_db()
    try:
        db.execute("INSERT INTO bookmarks (user_id, post_id) VALUES (?, ?)", (session["user_id"], post_id))
        db.commit()
        flash("Post bookmarked.")
    except sqlite3.IntegrityError:
        db.execute("DELETE FROM bookmarks WHERE user_id = ? AND post_id = ?", (session["user_id"], post_id))
        db.commit()
        flash("Bookmark removed.")
    return redirect(request.referrer or url_for("index"))

@app.route("/post/<int:post_id>/delete", methods=["POST"])
@login_required
def delete_post(post_id):
    db = get_db()
    post = db.execute("SELECT * FROM posts WHERE id = ? AND user_id = ?", (post_id, session["user_id"])).fetchone()
    if not post:
        flash("Post not found or unauthorized.")
        return redirect(url_for("index"))
    db.execute("DELETE FROM comments WHERE post_id = ?", (post_id,))
    db.execute("DELETE FROM reactions WHERE post_id = ?", (post_id,))
    db.execute("DELETE FROM bookmarks WHERE post_id = ?", (post_id,))
    db.execute("DELETE FROM posts WHERE id = ?", (post_id,))
    db.commit()
    flash("Post deleted.")
    return redirect(url_for("index"))

def _react(post_id, kind, reason=""):
    db = get_db()
    existing = db.execute("SELECT * FROM reactions WHERE post_id = ? AND user_id = ?", (post_id, session["user_id"])).fetchone()
    if existing and existing["kind"] == kind:
        db.execute("DELETE FROM reactions WHERE id = ?", (existing["id"],))
        db.commit()
        return
    if existing:
        db.execute("DELETE FROM reactions WHERE id = ?", (existing["id"],))
    db.execute("INSERT INTO reactions (post_id, user_id, kind, reason) VALUES (?, ?, ?, ?)", (post_id, session["user_id"], kind, reason))
    db.commit()

@app.route("/post/<int:post_id>/resonate", methods=["POST"])
@login_required
def resonate_post(post_id):
    _react(post_id, "resonate")
    return redirect(request.referrer or url_for("index"))

@app.route("/post/<int:post_id>/break", methods=["POST"])
@login_required
def break_post(post_id):
    reason = request.form.get("reason", "").strip()
    if len(reason) < 20:
        flash("A Break requires at least 20 characters.")
        return redirect(request.referrer or url_for("post_detail", post_id=post_id))
    db = get_db()
    _react(post_id, "break", reason)
    db.execute("INSERT INTO posts (user_id, content, is_stance, break_of_id) VALUES (?, ?, 1, ?)", (session["user_id"], reason, post_id))
    db.commit()
    return redirect(url_for("post_detail", post_id=post_id))

@app.route("/explore")
def explore():
    query = request.args.get("q", "").strip()
    db = get_db()
    users = []
    if query:
        users = db.execute("SELECT * FROM users WHERE username LIKE ? LIMIT 20", ("%" + query.lstrip("@") + "%",)).fetchall()
    return render_template("explore.html", query=query, users=users)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if user and check_password_hash(user["password"], password):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            return redirect(url_for("index"))
        flash("Invalid credentials.")
    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        if len(password) < 6:
            flash("Password must be at least 6 characters.")
            return render_template("register.html")
        db = get_db()
        try:
            db.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, generate_password_hash(password)))
            db.commit()
            flash("Registered successfully.")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("Username already taken.")
    return render_template("register.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/profile/<username>")
def profile(username):
    db = get_db()
    profile_user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if not profile_user:
        flash("User not found.")
        return redirect(url_for("index"))
    rows = db.execute(f"{POST_SELECT} WHERE posts.user_id = ? ORDER BY posts.created_at DESC", (profile_user["id"],)).fetchall()
    return render_template("profile.html", profile_user=profile_user, posts=hydrate(rows))

@app.route("/profile/block/<int:user_id>", methods=["POST"])
@login_required
def block_user(user_id):
    db = get_db()
    try:
        db.execute("INSERT INTO blocks (user_id, blocked_id) VALUES (?, ?)", (session["user_id"], user_id))
        db.commit()
        flash("User blocked.")
    except sqlite3.IntegrityError:
        flash("User is already blocked.")
    return redirect(url_for("index"))

@app.route("/profile/update", methods=["POST"])
@login_required
def update_bio():
    bio = request.form.get("bio", "").strip()[:180]
    db = get_db()
    db.execute("UPDATE users SET bio = ? WHERE id = ?", (bio, session["user_id"]))
    db.commit()
    return redirect(url_for("profile", username=session["username"]))

@app.route("/profile/password", methods=["POST"])
@login_required
def update_password():
    old_pw = request.form.get("old_password", "")
    new_pw = request.form.get("new_password", "")
    if len(new_pw) < 6:
        flash("New password must be at least 6 characters.")
        return redirect(url_for("profile", username=session["username"]))
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()
    if user and check_password_hash(user["password"], old_pw):
        db.execute("UPDATE users SET password = ? WHERE id = ?", (generate_password_hash(new_pw), session["user_id"]))
        db.commit()
        flash("Password updated successfully.")
    else:
        flash("Current password incorrect.")
    return redirect(url_for("profile", username=session["username"]))

@app.route("/profile/delete", methods=["POST"])
@login_required
def delete_account():
    uid = session["user_id"]
    db = get_db()
    db.execute("DELETE FROM reactions WHERE user_id = ?", (uid,))
    db.execute("DELETE FROM comments WHERE user_id = ?", (uid,))
    db.execute("DELETE FROM bookmarks WHERE user_id = ?", (uid,))
    db.execute("DELETE FROM posts WHERE user_id = ?", (uid,))
    db.execute("DELETE FROM messages WHERE sender_id = ? OR recipient_id = ?", (uid, uid))
    db.execute("DELETE FROM blocks WHERE user_id = ? OR blocked_id = ?", (uid, uid))
    db.execute("DELETE FROM users WHERE id = ?", (uid,))
    db.commit()
    session.clear()
    flash("Account deleted successfully.")
    return redirect(url_for("index"))

@app.route("/messages")
@login_required
def messages():
    db = get_db()
    users = db.execute("SELECT * FROM users WHERE id != ? AND username != ? AND id NOT IN (SELECT blocked_id FROM blocks WHERE user_id = ?)", (session["user_id"], SYSTEM_USERNAME, session["user_id"])).fetchall()
    return render_template("messages.html", users=users)

_typing_status = {}

@app.route("/chat/<int:recipient_id>/typing", methods=["POST"])
@login_required
def chat_typing(recipient_id):
    _typing_status[(session["user_id"], recipient_id)] = time()
    return "", 204

@app.route("/chat/<int:recipient_id>", methods=["GET", "POST"])
@login_required
def chat(recipient_id):
    db = get_db()
    recipient = db.execute("SELECT * FROM users WHERE id = ?", (recipient_id,)).fetchone()
    if not recipient:
        flash("Recipient not found.")
        return redirect(url_for("messages"))
    if request.method == "POST":
        content = request.form.get("content", "").strip()
        if content:
            db.execute("INSERT INTO messages (sender_id, recipient_id, content) VALUES (?, ?, ?)", (session["user_id"], recipient_id, content))
            db.commit()
        return redirect(url_for("chat", recipient_id=recipient_id))
    return render_template("chat.html", recipient=recipient)

@app.route("/chat/<int:recipient_id>/json")
@login_required
def chat_json(recipient_id):
    db = get_db()
    db.execute("UPDATE messages SET read = 1 WHERE sender_id = ? AND recipient_id = ?", (recipient_id, session["user_id"]))
    db.commit()
    rows = db.execute("SELECT sender_id, recipient_id, content, read, timestamp FROM messages WHERE (sender_id = ? AND recipient_id = ?) OR (sender_id = ? AND recipient_id = ?) ORDER BY id ASC", (session["user_id"], recipient_id, recipient_id, session["user_id"])).fetchall()
    
    last_typed = _typing_status.get((recipient_id, session["user_id"]), 0)
    is_typing = (time() - last_typed) < 3.5

    return jsonify({
        "messages": [dict(r) for r in rows],
        "is_typing": is_typing
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("PULSE_DEBUG", "1") == "1")
