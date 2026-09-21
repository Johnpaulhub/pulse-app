import os
import io
import re
import uuid
import secrets
import sqlite3
from collections import defaultdict
from datetime import datetime, date, timedelta
from time import time
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for, session,
    flash, g, send_file, jsonify, abort
)
from flask_socketio import SocketIO, join_room, leave_room, emit
from jinja2 import DictLoader
from markupsafe import Markup, escape
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from bs4 import BeautifulSoup
import requests
from PIL import Image

from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

UPLOAD_FOLDER = "static/uploads"
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
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

socketio = SocketIO(app, cors_allowed_origins="*")
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

def optimize_image(file_storage, output_path, max_size=(1200, 1200), quality=80):
    try:
        img = Image.open(file_storage)
        img.verify()
        file_storage.seek(0)
        img = Image.open(file_storage)
        img.thumbnail(max_size)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.save(output_path, "WEBP", quality=quality)
        return True
    except Exception:
        return False

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

# --- TEMPLATES ---

BASE_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Pulse</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.min.js"></script>
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
        .nav-links a { color: var(--text-muted); text-decoration: none; font-weight: 500; font-size: .85rem; position: relative; }
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
        .av { width: 34px; height: 34px; border-radius: 50%; display: grid; place-items: center; font-size: .75rem; font-weight: 800; color: #fff; flex-shrink: 0; object-fit: cover; border: 1px solid var(--border); }
        .username { font-weight: 700; font-size: .9rem; color: var(--text); text-decoration: none; }
        .timestamp { font-size: .75rem; color: var(--text-muted); white-space: nowrap; }
        .post-content { font-size: .95rem; line-height: 1.5; margin-bottom: 12px; word-break: break-word; }
        .image-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(130px, 1fr)); gap: 8px; margin-bottom: 12px; }
        .post-image { width: 100%; height: 130px; border-radius: 8px; object-fit: cover; border: 1px solid var(--border); cursor: pointer; transition: opacity .2s; }
        .post-image:hover { opacity: .9; }
        .post-image.single { height: auto; max-height: 350px; grid-column: 1 / -1; }
        .quote-box { background: rgba(0,0,0,.25); border-left: 3px solid var(--primary); padding: 10px 12px; border-radius: 0 8px 8px 0; margin-bottom: 12px; font-size: .9rem; }
        .poll-container { background: #090d16; border: 1px solid var(--border); border-radius: 10px; padding: 12px; margin-bottom: 12px; }
        .poll-option { display: block; width: 100%; text-align: left; background: var(--card-bg); border: 1px solid var(--border); padding: 8px 12px; border-radius: 8px; margin-top: 6px; color: var(--text); cursor: pointer; position: relative; overflow: hidden; font-size: .85rem; }
        .poll-option:hover { border-color: var(--primary); }
        .poll-bar { position: absolute; top: 0; left: 0; bottom: 0; background: rgba(59, 130, 246, 0.2); z-index: 1; pointer-events: none; }
        .poll-text { position: relative; z-index: 2; display: flex; justify-content: space-between; }
        .post-actions { display: flex; gap: 10px; margin-top: 12px; border-top: 1px solid var(--border); padding-top: 12px; font-size: .8rem; color: var(--text-muted); align-items: center; flex-wrap: wrap; }
        .linkish { background: none; border: none; color: var(--text-muted); cursor: pointer; font-size: .8rem; padding: 0; }
        .linkish:hover, .post-actions a:hover { color: var(--primary); }
        .post-actions a { color: var(--text-muted); text-decoration: none; }
        .tag { color: var(--primary); text-decoration: none; font-weight: 600; }
        .badge { display: inline-block; font-size: .65rem; font-weight: 800; letter-spacing: .06em; padding: 2px 7px; border-radius: 999px; border: 1px solid var(--warn); color: var(--warn); }
        .badge-sys { border-color: var(--primary); color: var(--primary); }
        .badge-dead { border-color: var(--danger); color: var(--danger); }
        .badge-counter { background: var(--danger); color: white; border-radius: 50%; padding: 1px 5px; font-size: .6rem; position: absolute; top: -6px; right: -10px; font-weight: bold; display: none; }
        .feeds { display: flex; gap: 10px; flex-wrap: wrap; font-size: .85rem; }
        .feeds a { color: var(--text-muted); text-decoration: none; font-weight: 600; }
        .feeds a.on { color: var(--primary); }
        .mobile-nav { position: fixed; bottom: 0; left: 0; right: 0; background: rgba(11,14,20,.9); border-top: 1px solid var(--border); display: flex; justify-content: space-around; z-index: 100; height: 60px; align-items: center; backdrop-filter: blur(12px); }
        .mobile-nav a { color: var(--text-muted); text-decoration: none; font-size: 1.25rem; }
        .row { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
        .stories-bar { display: flex; gap: 12px; overflow-x: auto; padding-bottom: 12px; margin-bottom: 16px; scrollbar-width: none; }
        .stories-bar::-webkit-scrollbar { display: none; }
        .story-ring { width: 56px; height: 56px; border-radius: 50%; background: linear-gradient(45deg, var(--primary), var(--warn)); padding: 2px; display: flex; align-items: center; justify-content: center; flex-shrink: 0; cursor: pointer; }
        .story-inner { width: 100%; height: 100%; border-radius: 50%; background: var(--card-bg); display: flex; align-items: center; justify-content: center; overflow: hidden; }
        .story-inner img { width: 100%; height: 100%; object-fit: cover; }
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
            <a href="{{ url_for('trending_page') }}">🔥 Trending</a>
            {% if session.get('user_id') %}
                <a href="{{ url_for('bookmarks_page') }}">🔖 Bookmarks</a>
                <a href="{{ url_for('messages') }}">Inbox</a>
                <a href="{{ url_for('notifications') }}">Notifications<span id="notif-badge" class="badge-counter">0</span></a>
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
        <a href="{{ url_for('trending_page') }}">🔥</a>
        <a href="{{ url_for('messages') }}">💬</a>
    </div>
    <script>
    const socket = io();
    function openLightbox(src) {
        document.getElementById('lightbox-img').src = src;
        document.getElementById('lightbox').style.display = 'flex';
    }
    {% if session.get('user_id') %}
    socket.emit('join_notifications', {user_id: {{ session.get('user_id') }}});
    socket.on('new_notification', function(data) {
        const badge = document.getElementById('notif-badge');
        if (badge) {
            let count = parseInt(badge.textContent || '0') + 1;
            badge.textContent = count;
            badge.style.display = 'inline-block';
        }
    });
    fetch('/notifications/json').then(r => r.json()).then(data => {
        if (data.length > 0) {
            const badge = document.getElementById('notif-badge');
            if (badge) {
                badge.textContent = data.length;
                badge.style.display = 'inline-block';
            }
        }
    });
    {% endif %}
    </script>
</body>
</html>
"""

INDEX_TEMPLATE = """
{% extends "base.html" %}
{% block content %}
{% if session.get('user_id') %}
<div class="stories-bar">
    <div style="text-align:center;flex-shrink:0;">
        <div class="story-ring" onclick="document.getElementById('story-upload-form').style.display=document.getElementById('story-upload-form').style.display=='none'?'block':'none'">
            <div class="story-inner" style="background:var(--primary);color:#fff;font-size:1.2rem;font-weight:bold;">+</div>
        </div>
        <span style="font-size:.65rem;color:var(--text-muted);">Add 24h</span>
    </div>
    {% for story in stories %}
    <div style="text-align:center;flex-shrink:0;" onclick="openLightbox('{{ url_for('static', filename='uploads/' + story.image_filename) }}')">
        <div class="story-ring">
            <div class="story-inner">
                <img src="{{ url_for('static', filename='uploads/' + story.image_filename) }}" alt="">
            </div>
        </div>
        <span style="font-size:.65rem;color:var(--text-muted);">@{{ story.username }}</span>
    </div>
    {% endfor %}
</div>

<div id="story-upload-form" class="card" style="display:none;background:#10151c;border-style:dashed;">
    <h3 style="font-size:.9rem;margin-bottom:8px;color:var(--warn);">⏱️ Post a 24-Hour Drop</h3>
    <form method="POST" action="{{ url_for('create_story') }}" enctype="multipart/form-data">
        <input type="hidden" name="csrf" value="{{ csrf_token }}">
        <input type="file" name="file" accept="image/*" required style="margin-bottom:8px;font-size:.8rem;">
        <div style="display:flex;justify-content:flex-end;">
            <button type="submit" class="btn" style="padding:4px 12px;font-size:.75rem;">Upload 24h Drop</button>
        </div>
    </form>
</div>

<div class="card">
    <form method="POST" action="{{ url_for('create_post') }}" enctype="multipart/form-data">
        <input type="hidden" name="csrf" value="{{ csrf_token }}">
        <textarea name="content" rows="3" placeholder="Take a stance. Use #tags and @names." style="resize:none;background:transparent;border:none;font-size:1rem;color:var(--text);outline:none;"></textarea>
        
        <div id="poll-creator" style="display:none;margin-top:10px;border-top:1px dashed var(--border);padding-top:10px;">
            <label style="font-size:.8rem;color:var(--warn);margin-bottom:4px;">Attach Poll Options</label>
            <input type="text" name="poll_opt1" placeholder="Option 1" style="margin-bottom:6px;font-size:.85rem;padding:8px;">
            <input type="text" name="poll_opt2" placeholder="Option 2" style="font-size:.85rem;padding:8px;">
        </div>

        <div class="row" style="margin-top:12px;border-top:1px solid var(--border);padding-top:10px;flex-wrap:wrap;">
            <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
                <label style="margin:0;cursor:pointer;background:#090d16;border:1px solid var(--border);padding:6px 12px;border-radius:10px;font-size:.8rem;color:var(--text-muted);">
                    📷 Upload Images
                    <input type="file" name="files" accept="image/*" multiple style="display:none;">
                </label>
                <button type="button" class="btn btn-outline" style="padding:5px 10px;font-size:.75rem;" onclick="let el=document.getElementById('poll-creator');el.style.display=el.style.display=='none'?'block':'none';">📊 Add Poll</button>
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
        {% if session.get('user_id') %}
        <a href="{{ url_for('index', feed='following') }}" class="{{ 'on' if feed_type=='following' }}">Following</a>
        {% endif %}
        <a href="{{ url_for('index', feed='global') }}" class="{{ 'on' if feed_type=='global' }}">Unfiltered</a>
        <a href="{{ url_for('index', feed='clash') }}" class="{{ 'on' if feed_type=='clash' }}">Clash</a>
    </div>
</div>

<div id="posts-container">
    {% for post in posts %}
        {% include "post_card.html" %}
    {% else %}
    <p style="color:var(--text-muted);text-align:center;padding:40px 0;">The network is quiet. Take a stance.</p>
    {% endfor %}
</div>

<div id="load-more-trigger" style="text-align:center;padding:20px;">
    <button id="load-more-btn" class="btn btn-outline" onclick="loadMorePosts()" style="font-size:.8rem;">Load More Older Posts</button>
</div>

<script>
let page = 1;
const feedType = "{{ feed_type }}";
async function loadMorePosts() {
    page++;
    const btn = document.getElementById('load-more-btn');
    btn.textContent = 'Loading...';
    try {
        const res = await fetch(`/feed/json?feed=${feedType}&page=${page}`);
        const data = await res.json();
        if (data.posts.length === 0) {
            document.getElementById('load-more-trigger').innerHTML = '<p style="color:var(--text-muted);font-size:.8rem;">No more posts to load.</p>';
            return;
        }
        const container = document.getElementById('posts-container');
        for (const post of data.posts) {
            // Render post card fragment dynamically via client template rendering or simple HTML injection
            const div = document.createElement('div');
            div.innerHTML = post.html_card;
            container.appendChild(div.firstElementChild);
        }
        btn.textContent = 'Load More Older Posts';
    } catch(err) {
        btn.textContent = 'Error loading posts';
    }
}
</script>
{% endblock %}
"""

POST_CARD_TEMPLATE = """
<div class="card">
    <div class="post-header">
        <div class="who">
            {% if post.avatar %}
                <img src="{{ url_for('static', filename='uploads/' + post.avatar) }}" class="av" alt="">
            {% else %}
                <div class="av" style="background:{{ post.username|avatar }}">{{ post.username[:1]|upper }}</div>
            {% endif %}
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
            <div>{{ post.quoted_post.formatted_content }}</div>
        </div>
        {% endif %}
        <div class="post-content">{{ post.formatted_content }}</div>
        
        {% if post.og_url %}
        <a href="{{ post.og_url }}" target="_blank" style="text-decoration:none;color:inherit;">
            <div style="border:1px solid var(--border);border-radius:8px;background:#090d16;overflow:hidden;margin-bottom:12px;display:flex;flex-direction:column;">
                {% if post.og_image %}
                <img src="{{ post.og_image }}" style="width:100%;height:150px;object-fit:cover;" alt="">
                {% endif %}
                <div style="padding:10px;">
                    <div style="font-weight:700;font-size:.85rem;margin-bottom:2px;color:var(--text);">{{ post.og_title or post.og_url }}</div>
                    <div style="font-size:.75rem;color:var(--text-muted);">{{ post.og_description or '' }}</div>
                </div>
            </div>
        </a>
        {% endif %}

        {% if post.poll %}
        <div class="poll-container">
            <div style="font-size:.8rem;font-weight:bold;margin-bottom:6px;color:var(--text-muted);">📊 Live Poll</div>
            {% set total_votes = post.poll.options | sum(attribute='votes') %}
            {% for opt in post.poll.options %}
                {% set pct = (opt.votes / total_votes * 100) | round | int if total_votes > 0 else 0 %}
                <form method="POST" action="{{ url_for('vote_poll', option_id=opt.id) }}" style="margin:0;">
                    <input type="hidden" name="csrf" value="{{ csrf_token }}">
                    <button type="submit" class="poll-option">
                        <div class="poll-bar" style="width: {{ pct }}%;"></div>
                        <div class="poll-text">
                            <span>{{ opt.text }}</span>
                            <span style="font-weight:bold;color:var(--primary);">{{ pct }}% ({{ opt.votes }})</span>
                        </div>
                    </button>
                </form>
            {% endfor %}
        </div>
        {% endif %}

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

BOOKMARKS_TEMPLATE = """
{% extends "base.html" %}
{% block content %}
<div style="font-weight:700;margin-bottom:12px;font-size:1rem;color:var(--text-muted);">🔖 Your Saved Collections</div>
{% for post in posts %}
    {% include "post_card.html" %}
{% else %}
<p style="color:var(--text-muted);text-align:center;padding:40px;">No saved bookmarks yet.</p>
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

TRENDING_TEMPLATE = """
{% extends "base.html" %}
{% block content %}
<div class="card" style="padding:24px;">
    <h2 style="margin-bottom:16px;font-size:1.25rem;">🔥 Trending Conversations</h2>
    <div style="display:flex;flex-direction:column;gap:12px;">
        {% for tag, count in trending %}
        <div class="row" style="padding:8px 0;border-bottom:1px solid var(--border);">
            <a href="{{ url_for('tag_feed', tagname=tag[1:]) }}" class="tag" style="font-size:1rem;">{{ tag }}</a>
            <span style="font-size:.8rem;color:var(--text-muted);">{{ count }} mentions</span>
        </div>
        {% else %}
        <p style="color:var(--text-muted);">No active trending topics right now.</p>
        {% endfor %}
    </div>
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
    {% if profile_user.avatar %}
        <img src="{{ url_for('static', filename='uploads/' + profile_user.avatar) }}" class="av" style="width:72px;height:72px;margin:0 auto 10px;" alt="">
    {% else %}
        <div class="av" style="width:72px;height:72px;margin:0 auto 10px;font-size:1.5rem;background:{{ profile_user.username|avatar }}">{{ profile_user.username[:1]|upper }}</div>
    {% endif %}
    <h2 style="margin-bottom:4px;font-size:1.3rem;">@{{ profile_user.username }}</h2>
    <p style="color:var(--text-muted);font-size:.75rem;margin-bottom:12px;">Joined {{ profile_user.created_at }}</p>
    <p style="font-size:.95rem;margin-bottom:16px;">{{ profile_user.bio if profile_user.bio else 'No bio written yet.' }}</p>
    
    {% if session.get('user_id') and session.get('user_id') != profile_user.id %}
    <div style="display:flex;justify-content:center;gap:8px;margin-bottom:12px;">
        <form method="POST" action="{{ url_for('toggle_follow', user_id=profile_user.id) }}">
            <input type="hidden" name="csrf" value="{{ csrf_token }}">
            <button type="submit" class="btn {% if not is_following %}btn-outline{% endif %}" style="padding:4px 14px;font-size:.75rem;">{{ 'Unfollow' if is_following else 'Follow' }}</button>
        </form>
        <form method="POST" action="{{ url_for('block_user', user_id=profile_user.id) }}">
            <input type="hidden" name="csrf" value="{{ csrf_token }}">
            <button type="submit" class="btn btn-danger" style="padding:4px 12px;font-size:.75rem;">Block</button>
        </form>
    </div>
    {% endif %}

    <div style="margin-bottom:16px;">
        <a href="{{ url_for('export_pdf', username=profile_user.username) }}" class="btn btn-outline" style="padding:4px 12px;font-size:.75rem;">📥 Download Activity PDF</a>
    </div>
    
    {% if session.get('user_id') == profile_user.id %}
    <form method="POST" action="{{ url_for('update_avatar') }}" enctype="multipart/form-data" style="text-align:left;border-top:1px solid var(--border);padding-top:16px;margin-bottom:16px;">
        <input type="hidden" name="csrf" value="{{ csrf_token }}">
        <div class="form-group" style="margin-bottom:8px;">
            <label>Update Profile Photo</label>
            <input type="file" name="avatar" accept="image/*" required style="font-size:.8rem;">
        </div>
        <button type="submit" class="btn btn-outline" style="padding:5px 14px;font-size:.75rem;">Upload Avatar</button>
    </form>
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

NOTIFICATIONS_TEMPLATE = """
{% extends "base.html" %}
{% block content %}
<div style="font-weight:700;margin-bottom:12px;font-size:1rem;color:var(--text-muted);">Notifications</div>
<div class="card">
    {% for notif in notifications %}
    <div class="row" style="padding:10px 0;border-bottom:1px solid var(--border);font-size:.9rem;">
        <span>{{ notif.message }}</span>
        <span class="timestamp">{{ notif.created_at|ago }}</span>
    </div>
    {% else %}
    <p style="color:var(--text-muted);text-align:center;padding:20px;">No notifications yet.</p>
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
const recipientId = {{ recipient.id }};
let typingTimer;

socket.emit('join_chat', {recipient_id: recipientId});

socket.on('new_message', function(msg) {
    appendMessage(msg);
});

socket.on('typing_status', function(data) {
    const indicator = document.getElementById('typing-indicator');
    if (data.is_typing && data.user_id === recipientId) {
        indicator.textContent = '@{{ recipient.username }} is typing...';
    } else {
        indicator.textContent = '';
    }
});

function startCall(type) {
    alert(type.toUpperCase() + ' call feature initialized. Connecting secure WebRTC stream to @{{ recipient.username }}...');
}

function pingTyping() {
    socket.emit('typing', {recipient_id: recipientId, is_typing: true});
    clearTimeout(typingTimer);
    typingTimer = setTimeout(() => {
        socket.emit('typing', {recipient_id: recipientId, is_typing: false});
    }, 1500);
}

async function sendMsg(e) {
    e.preventDefault();
    const input = document.getElementById('msg-input');
    const val = input.value.trim();
    if (!val) return;
    
    const res = await fetch('/chat/' + recipientId, {
        method: 'POST',
        headers: {'Content-Type': 'application/x-www-form-urlencoded'},
        body: 'csrf={{ csrf_token }}&content=' + encodeURIComponent(val)
    });
    input.value = '';
    socket.emit('typing', {recipient_id: recipientId, is_typing: false});
    loadMessages();
}

function appendMessage(msg) {
    const mine = msg.sender_id === me;
    const el = document.createElement('div');
    el.style.cssText = 'max-width:75%;padding:8px 12px;border-radius:12px;' + (mine ? 'background:var(--primary);margin-left:auto;color:white;' : 'background:var(--border);margin-right:auto;');
    el.innerHTML = '<div style="font-size:.9rem;word-break:break-word;"></div><div style="font-size:.6rem;opacity:.8;margin-top:2px;text-align:right;"></div>';
    el.children[0].textContent = msg.content;
    el.children[1].textContent = msg.timestamp + (mine && msg.read ? ' ✓✓' : ' ✓');
    box.appendChild(el);
    box.scrollTop = box.scrollHeight;
}

async function loadMessages() {
    const res = await fetch('/chat/' + recipientId + '/json');
    const data = await res.json();
    box.innerHTML = '';
    if (!data.messages.length) {
        box.innerHTML = '<p style="color:var(--text-muted);text-align:center;margin:auto;font-size:.85rem;">Start the conversation.</p>';
    } else {
        data.messages.forEach(msg => appendMessage(msg));
    }
}
loadMessages();
</script>
{% endblock %}
"""

app.jinja_loader = DictLoader({
    "base.html": BASE_TEMPLATE,
    "index.html": INDEX_TEMPLATE,
    "post_card.html": POST_CARD_TEMPLATE,
    "post_detail.html": POST_DETAIL_TEMPLATE,
    "bookmarks.html": BOOKMARKS_TEMPLATE,
    "explore.html": EXPLORE_TEMPLATE,
    "trending.html": TRENDING_TEMPLATE,
    "login.html": LOGIN_TEMPLATE,
    "register.html": REGISTER_TEMPLATE,
    "profile.html": PROFILE_TEMPLATE,
    "messages.html": MESSAGES_TEMPLATE,
    "notifications.html": NOTIFICATIONS_TEMPLATE,
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
            avatar TEXT,
            email TEXT,
            is_verified INTEGER DEFAULT 0,
            totp_secret TEXT,
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
        CREATE TABLE IF NOT EXISTS polls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            FOREIGN KEY (post_id) REFERENCES posts (id)
        );
        CREATE TABLE IF NOT EXISTS poll_options (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            poll_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            votes INTEGER DEFAULT 0,
            FOREIGN KEY (poll_id) REFERENCES polls (id)
        );
        CREATE TABLE IF NOT EXISTS poll_votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            poll_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            option_id INTEGER NOT NULL,
            UNIQUE(poll_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS stories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            image_filename TEXT NOT NULL,
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
        CREATE TABLE IF NOT EXISTS follows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            follower_id INTEGER NOT NULL,
            followed_id INTEGER NOT NULL,
            UNIQUE(follower_id, followed_id)
        );
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            is_read INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        );
    """)
    for col, spec in [
        ("is_stance", "INTEGER DEFAULT 0"),
        ("retracted", "INTEGER DEFAULT 0"),
        ("is_prompt", "INTEGER DEFAULT 0"),
        ("quote_of_id", "INTEGER"),
        ("break_of_id", "INTEGER"),
        ("image_filenames", "TEXT"),
        ("avatar", "TEXT"),
        ("og_title", "TEXT"),
        ("og_description", "TEXT"),
        ("og_image", "TEXT"),
        ("og_url", "TEXT"),
        ("status", "TEXT DEFAULT 'published'"),
        ("scheduled_for", "TIMESTAMP"),
    ]:
        add_column_safe(db, "users" if col == "avatar" else "posts", col, spec)

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
    SELECT posts.*, users.username, users.avatar,
           (SELECT COUNT(*) FROM reactions WHERE reactions.post_id = posts.id AND kind = 'resonate') AS resonates,
           (SELECT COUNT(*) FROM reactions WHERE reactions.post_id = posts.id AND kind = 'break') AS breaks,
           (SELECT COUNT(*) FROM comments WHERE comments.post_id = posts.id) AS comments_count
    FROM posts
    JOIN users ON posts.user_id = users.id
"""

def fetch_og_data(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=3)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            title = soup.find('meta', property='og:title') or soup.find('title')
            desc = soup.find('meta', property='og:description') or soup.find('meta', attrs={'name': 'description'})
            img = soup.find('meta', property='og:image')
            return {
                'title': title.get('content') if title and title.has_attr('content') else (title.text if title else None),
                'description': desc.get('content') if desc and desc.has_attr('content') else None,
                'image': img.get('content') if img and img.has_attr('image') or (img and img.has_attr('content')) else None
            }
    except Exception:
        pass
    return None

def hydrate(rows):
    db = get_db()
    posts = []
    
    quote_ids = [dict(row).get("quote_of_id") for row in rows if dict(row).get("quote_of_id")]
    post_ids = [dict(row).get("id") for row in rows]
    quoted_posts_map = {}
    polls_map = {}

    if quote_ids:
        q_sql = f"""
            SELECT posts.*, users.username, users.avatar,
                   (SELECT COUNT(*) FROM reactions WHERE reactions.post_id = posts.id AND kind = 'resonate') AS resonates,
                   (SELECT COUNT(*) FROM reactions WHERE reactions.post_id = posts.id AND kind = 'break') AS breaks,
                   (SELECT COUNT(*) FROM comments WHERE comments.post_id = posts.id) AS comments_count
            FROM posts
            JOIN users ON posts.user_id = users.id
            WHERE posts.id IN ({','.join('?' * len(quote_ids))})
        """
        q_rows = db.execute(q_sql, quote_ids).fetchall()
        quoted_posts_map = {p["id"]: dict(p) for p in q_rows}

    if post_ids:
        p_sql = f"""
            SELECT polls.id as poll_id, polls.post_id, poll_options.id as option_id, poll_options.text, poll_options.votes
            FROM polls
            JOIN poll_options ON polls.id = poll_options.poll_id
            WHERE polls.post_id IN ({','.join('?' * len(post_ids))})
        """
        poll_rows = db.execute(p_sql, post_ids).fetchall()
        temp_polls = defaultdict(lambda: {"id": None, "options": []})
        for pr in poll_rows:
            temp_polls[pr["post_id"]]["id"] = pr["poll_id"]
            temp_polls[pr["post_id"]]["options"].append({"id": pr["option_id"], "text": pr["text"], "votes": pr["votes"]})
        polls_map = dict(temp_polls)

    for row in rows:
        post = dict(row)
        post["formatted_content"] = format_pulse(post["content"])
        post["score"] = pulse_score(post.get("resonates"), post.get("comments_count"), post.get("breaks"))
        
        if post.get("quote_of_id") in quoted_posts_map:
            post["quoted_post"] = quoted_posts_map[post["quote_of_id"]]
            post["quoted_post"]["formatted_content"] = format_pulse(post["quoted_post"]["content"])

        if post["id"] in polls_map:
            post["poll"] = polls_map[post["id"]]
            
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
    
    cutoff = (datetime.utcnow() - timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')
    db.execute("DELETE FROM stories WHERE created_at < ?", (cutoff,))
    db.commit()

    where = "1=1"
    params = []
    if "user_id" in session:
        where += " AND posts.user_id NOT IN (SELECT blocked_id FROM blocks WHERE user_id = ?)"
        params.append(session["user_id"])
        if feed_type == "following":
            where += " AND (posts.user_id = ? OR posts.user_id IN (SELECT followed_id FROM follows WHERE follower_id = ?))"
            params.extend([session["user_id"], session["user_id"]])

    if feed_type == "clash":
        where += " AND (posts.break_of_id IS NOT NULL OR posts.is_stance = 1)"
    
    where += " AND (posts.status = 'published' OR (posts.status = 'scheduled' AND posts.scheduled_for <= datetime('now')))"
    
    rows = db.execute(f"{POST_SELECT} WHERE {where} ORDER BY posts.created_at DESC LIMIT 10", tuple(params)).fetchall()
    stories = db.execute("SELECT stories.*, users.username FROM stories JOIN users ON stories.user_id = users.id ORDER BY stories.created_at DESC").fetchall()
    
    return render_template("index.html", posts=hydrate(rows), stories=stories, feed_type=feed_type)

@app.route("/feed/json")
def feed_json():
    feed_type = request.args.get("feed", "global")
    page = int(request.args.get("page", 1))
    limit = 10
    offset = (page - 1) * limit
    db = get_db()

    where = "1=1"
    params = []
    if "user_id" in session:
        where += " AND posts.user_id NOT IN (SELECT blocked_id FROM blocks WHERE user_id = ?)"
        params.append(session["user_id"])
        if feed_type == "following":
            where += " AND (posts.user_id = ? OR posts.user_id IN (SELECT followed_id FROM follows WHERE follower_id = ?))"
            params.extend([session["user_id"], session["user_id"]])

    if feed_type == "clash":
        where += " AND (posts.break_of_id IS NOT NULL OR posts.is_stance = 1)"
    
    where += " AND (posts.status = 'published' OR (posts.status = 'scheduled' AND posts.scheduled_for <= datetime('now')))"
    params.extend([limit, offset])

    rows = db.execute(f"{POST_SELECT} WHERE {where} ORDER BY posts.created_at DESC LIMIT ? OFFSET ?", tuple(params)).fetchall()
    hydrated = hydrate(rows)
    
    out = []
    for p in hydrated:
        card_html = render_template("post_card.html", post=p)
        out.append({"html_card": card_html})
    return jsonify({"posts": out})

@app.route("/bookmarks")
@login_required
def bookmarks_page():
    db = get_db()
    rows = db.execute(f"""
        {POST_SELECT} 
        JOIN bookmarks ON posts.id = bookmarks.post_id 
        WHERE bookmarks.user_id = ? 
        ORDER BY bookmarks.id DESC
    """, (session["user_id"],)).fetchall()
    return render_template("bookmarks.html", posts=hydrate(rows))

@app.route("/tag/<tagname>")
def tag_feed(tagname):
    db = get_db()
    tag_query = f"%{tagname}%"
    rows = db.execute(f"{POST_SELECT} WHERE posts.content LIKE ? ORDER BY posts.created_at DESC LIMIT 50", (tag_query,)).fetchall()
    return render_template("index.html", posts=hydrate(rows), stories=[], feed_type="global", page_title=f"Tag: #{tagname}")

@app.route("/trending")
def trending_page():
    db = get_db()
    posts = db.execute("SELECT content FROM posts WHERE status = 'published'").fetchall()
    hashtag_counts = {}
    for p in posts:
        for word in p["content"].split():
            if word.startswith("#") and len(word) > 1:
                hashtag_counts[word] = hashtag_counts.get(word, 0) + 1
    sorted_tags = sorted(hashtag_counts.items(), key=lambda x: x[1], reverse=True)
    return render_template("trending.html", trending=sorted_tags[:20])

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
            post_owner = db.execute("SELECT user_id FROM posts WHERE id = ?", (post_id,)).fetchone()
            if post_owner and post_owner["user_id"] != session["user_id"]:
                db.execute("INSERT INTO notifications (user_id, message) VALUES (?, ?)", (post_owner["user_id"], f"@{session['username']} replied to your post."))
                db.commit()
                socketio.emit('new_notification', {'message': 'New reply'}, room=f"user_{post_owner['user_id']}")
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
    status = request.form.get("status", "published")
    scheduled_for = request.form.get("scheduled_for") if status == "scheduled" else None
    
    uploaded_files = request.files.getlist("files")
    saved_filenames = []
    
    for file in uploaded_files[:20]:
        if file and file.filename and allowed_file(file.filename):
            filename = f"{uuid.uuid4().hex}.webp"
            filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
            if optimize_image(file, filepath):
                saved_filenames.append(filename)
            
    filenames_str = ",".join(saved_filenames) if saved_filenames else None
    
    if not content and not filenames_str:
        flash("Posts cannot be empty.")
        return redirect(url_for("index"))
        
    og_title, og_description, og_image, og_url = None, None, None, None
    for word in content.split():
        if word.startswith("http://") or word.startswith("https://"):
            og_url = word
            og_data = fetch_og_data(word)
            if og_data:
                og_title = og_data["title"]
                og_description = og_data["description"]
                og_image = og_data["image"]
            break

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "INSERT INTO posts (user_id, content, image_filenames, is_stance, status, scheduled_for, og_title, og_description, og_image, og_url) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (session["user_id"], content, filenames_str, is_stance, status, scheduled_for, og_title, og_description, og_image, og_url)
    )
    new_post_id = cursor.lastrowid

    poll_opt1 = request.form.get("poll_opt1", "").strip()
    poll_opt2 = request.form.get("poll_opt2", "").strip()
    if poll_opt1 and poll_opt2:
        cursor.execute("INSERT INTO polls (post_id) VALUES (?)", (new_post_id,))
        poll_id = cursor.lastrowid
        cursor.execute("INSERT INTO poll_options (poll_id, text) VALUES (?, ?)", (poll_id, poll_opt1))
        cursor.execute("INSERT INTO poll_options (poll_id, text) VALUES (?, ?)", (poll_id, poll_opt2))

    db.commit()
    return redirect(url_for("index"))

@app.route("/poll/vote/<int:option_id>", methods=["POST"])
@login_required
def vote_poll(option_id):
    db = get_db()
    opt = db.execute("SELECT * FROM poll_options WHERE id = ?", (option_id,)).fetchone()
    if not opt:
        flash("Poll option not found.")
        return redirect(url_for("index"))
    poll_id = opt["poll_id"]
    
    existing = db.execute("SELECT * FROM poll_votes WHERE poll_id = ? AND user_id = ?", (poll_id, session["user_id"])).fetchone()
    if existing:
        flash("You have already voted in this poll.")
        return redirect(request.referrer or url_for("index"))
    
    db.execute("INSERT INTO poll_votes (poll_id, user_id, option_id) VALUES (?, ?, ?)", (poll_id, session["user_id"], option_id))
    db.execute("UPDATE poll_options SET votes = votes + 1 WHERE id = ?", (option_id,))
    db.commit()
    return redirect(request.referrer or url_for("index"))

@app.route("/story", methods=["POST"])
@login_required
def create_story():
    file = request.files.get("file")
    if file and file.filename and allowed_file(file.filename):
        filename = f"story_{uuid.uuid4().hex}.webp"
        filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        if optimize_image(file, filepath):
            db = get_db()
            db.execute("INSERT INTO stories (user_id, image_filename) VALUES (?, ?)", (session["user_id"], filename))
            db.commit()
            flash("24-hour drop uploaded successfully!")
    else:
        flash("Invalid image file for story.")
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
    
    post_owner = db.execute("SELECT user_id FROM posts WHERE id = ?", (post_id,)).fetchone()
    if post_owner and post_owner["user_id"] != session["user_id"]:
        db.execute("INSERT INTO notifications (user_id, message) VALUES (?, ?)", (post_owner["user_id"], f"@{session['username']} reacted to your post."))
        db.commit()
        socketio.emit('new_notification', {'message': 'New reaction'}, room=f"user_{post_owner['user_id']}")

@app.route("/post/<int:post_id>/resonate", methods=["POST"])
@login_required
def resonate_post(post_id):
    _react(post_id, "resonate")
    return redirect(request.referrer or url_for("index"))

@app.route("/post/<int:post_id>/resonates")
def post_resonates(post_id):
    db = get_db()
    users = db.execute("""
        SELECT users.username FROM users 
        JOIN reactions ON users.id = reactions.user_id 
        WHERE reactions.post_id = ? AND reactions.kind = 'resonate'
    """, (post_id,)).fetchall()
    return jsonify([u['username'] for u in users])

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
    return redirect(request.referrer or url_for("post_detail", post_id=post_id))

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
    
    is_following = False
    if "user_id" in session:
        f_row = db.execute("SELECT 1 FROM follows WHERE follower_id = ? AND followed_id = ?", (session["user_id"], profile_user["id"])).fetchone()
        if f_row:
            is_following = True

    rows = db.execute(f"{POST_SELECT} WHERE posts.user_id = ? ORDER BY posts.created_at DESC", (profile_user["id"],)).fetchall()
    return render_template("profile.html", profile_user=profile_user, posts=hydrate(rows), is_following=is_following)

@app.route("/profile/follow/<int:user_id>", methods=["POST"])
@login_required
def toggle_follow(user_id):
    if user_id == session["user_id"]:
        return redirect(url_for("index"))
    db = get_db()
    existing = db.execute("SELECT * FROM follows WHERE follower_id = ? AND followed_id = ?", (session["user_id"], user_id)).fetchone()
    target = db.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
    if existing:
        db.execute("DELETE FROM follows WHERE id = ?", (existing["id"],))
        db.commit()
        flash(f"Unfollowed @{target['username'] if target else ''}.")
    else:
        db.execute("INSERT INTO follows (follower_id, followed_id) VALUES (?, ?)", (session["user_id"], user_id))
        db.commit()
        db.execute("INSERT INTO notifications (user_id, message) VALUES (?, ?)", (user_id, f"@{session['username']} started following you."))
        db.commit()
        socketio.emit('new_notification', {'message': 'New follower'}, room=f"user_{user_id}")
        flash(f"Now following @{target['username'] if target else ''}.")
    return redirect(request.referrer or url_for("index"))

@app.route("/profile/<username>/export-pdf")
def export_pdf(username):
    db = get_db()
    profile_user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if not profile_user:
        abort(404)
    
    posts = db.execute("SELECT content, created_at FROM posts WHERE user_id = ? ORDER BY created_at DESC", (profile_user["id"],)).fetchall()
    
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    story = []
    
    story.append(Paragraph(f"Activity Report: @{profile_user['username']}", styles['Title']))
    story.append(Spacer(1, 12))
    story.append(Paragraph(f"Joined: {profile_user['created_at']} | Bio: {profile_user['bio'] or 'None'}", styles['Normal']))
    story.append(Spacer(1, 18))
    
    for p in posts:
        story.append(Paragraph(f"<b>[{p['created_at']}]</b> {p['content']}", styles['Normal']))
        story.append(Spacer(1, 8))
        
    doc.build(story)
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name=f"{username}_pulse_report.pdf", mimetype='application/pdf')

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

@app.route("/profile/avatar", methods=["POST"])
@login_required
def update_avatar():
    file = request.files.get("avatar")
    if file and file.filename and allowed_file(file.filename):
        filename = f"avatar_{uuid.uuid4().hex}.webp"
        filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        if optimize_image(file, filepath, max_size=(400, 400)):
            db = get_db()
            db.execute("UPDATE users SET avatar = ? WHERE id = ?", (filename, session["user_id"]))
            db.commit()
            flash("Profile picture updated successfully!")
    else:
        flash("Invalid image for profile picture.")
    return redirect(url_for("profile", username=session["username"]))

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
    db.execute("DELETE FROM stories WHERE user_id = ?", (uid,))
    db.execute("DELETE FROM messages WHERE sender_id = ? OR recipient_id = ?", (uid, uid))
    db.execute("DELETE FROM blocks WHERE user_id = ? OR blocked_id = ?", (uid, uid))
    db.execute("DELETE FROM follows WHERE follower_id = ? OR followed_id = ?", (uid, uid))
    db.execute("DELETE FROM notifications WHERE user_id = ?", (uid,))
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

@app.route("/notifications")
@login_required
def notifications():
    db = get_db()
    notifs = db.execute("SELECT * FROM notifications WHERE user_id = ? ORDER BY created_at DESC", (session["user_id"],)).fetchall()
    db.execute("UPDATE notifications SET is_read = 1 WHERE user_id = ?", (session["user_id"],))
    db.commit()
    return render_template("notifications.html", notifications=notifs)

@app.route("/notifications/json")
def notifications_json():
    if "user_id" not in session:
        return jsonify([])
    db = get_db()
    notifs = db.execute("SELECT * FROM notifications WHERE user_id = ? AND is_read = 0", (session["user_id"],)).fetchall()
    return jsonify([dict(n) for n in notifs])

@socketio.on('join_notifications')
def handle_join_notifications(data):
    user_id = data.get('user_id')
    if user_id:
        join_room(f"user_{user_id}")

@socketio.on('join_chat')
def handle_join(data):
    if "user_id" not in session:
        return
    recipient_id = data.get('recipient_id')
    room = f"chat_{min(session['user_id'], recipient_id)}_{max(session['user_id'], recipient_id)}"
    join_room(room)

@socketio.on('typing')
def handle_typing(data):
    if "user_id" not in session:
        return
    recipient_id = data.get('recipient_id')
    room = f"chat_{min(session['user_id'], recipient_id)}_{max(session['user_id'], recipient_id)}"
    emit('typing_status', {'user_id': session['user_id'], 'is_typing': data.get('is_typing')}, room=room, include_self=False)

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
            
            db.execute("INSERT INTO notifications (user_id, message) VALUES (?, ?)", (recipient_id, f"@{session['username']} sent you a message."))
            db.commit()
            socketio.emit('new_notification', {'message': 'New message'}, room=f"user_{recipient_id}")
            
            msg = {
                "sender_id": session["user_id"],
                "recipient_id": recipient_id,
                "content": content,
                "read": 0,
                "timestamp": datetime.utcnow().strftime("%H:%M")
            }
            room = f"chat_{min(session['user_id'], recipient_id)}_{max(session['user_id'], recipient_id)}"
            socketio.emit('new_message', msg, room=room)
        return "", 204
    return render_template("chat.html", recipient=recipient)

@app.route("/chat/<int:recipient_id>/json")
@login_required
def chat_json(recipient_id):
    db = get_db()
    db.execute("UPDATE messages SET read = 1 WHERE sender_id = ? AND recipient_id = ?", (recipient_id, session["user_id"]))
    db.commit()
    rows = db.execute("SELECT sender_id, recipient_id, content, read, strftime('%H:%M', timestamp) as timestamp FROM messages WHERE (sender_id = ? AND recipient_id = ?) OR (sender_id = ? AND recipient_id = ?) ORDER BY id ASC", (session["user_id"], recipient_id, recipient_id, session["user_id"])).fetchall()

    return jsonify({
        "messages": [dict(r) for r in rows]
    })
