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
        <button class="linkish" type="button" onclick="document.getElementById
