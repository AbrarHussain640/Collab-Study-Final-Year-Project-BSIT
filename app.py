from flask import Flask, render_template, request, redirect, url_for, flash, send_file, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_socketio import SocketIO, emit, join_room as socket_join_room, leave_room as socket_leave_room
import bcrypt
from datetime import datetime
import os
import random
import string
import uuid
import mimetypes

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-change-in-production'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*")

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in first.'

# ---------- Helper Functions ----------
def generate_room_code(length=6):
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choices(chars, k=length))

def can_access_room(user, room):
    return user.id == room.created_by or user in room.members

def get_file_icon(file_type):
    if file_type and file_type.startswith('image/'):
        return 'bi bi-file-image-fill'
    elif file_type == 'application/pdf':
        return 'bi bi-file-pdf-fill'
    elif file_type in ['application/msword', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document']:
        return 'bi bi-file-word-fill'
    elif file_type in ['application/vnd.ms-powerpoint', 'application/vnd.openxmlformats-officedocument.presentationml.presentation']:
        return 'bi bi-file-ppt-fill'
    elif file_type in ['application/vnd.ms-excel', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet']:
        return 'bi bi-file-excel-fill'
    else:
        return 'bi bi-file-earmark-fill'

# ---------- Database Models ----------
room_members = db.Table('room_members',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id')),
    db.Column('room_id', db.Integer, db.ForeignKey('room.id'))
)

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)

    def __init__(self, username, email, plain_password):
        self.username = username
        self.email = email
        self.password_hash = bcrypt.hashpw(plain_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

    def check_password(self, plain_password):
        return bcrypt.checkpw(plain_password.encode('utf-8'), self.password_hash.encode('utf-8'))

class Room(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(500))
    course_code = db.Column(db.String(20), unique=True, nullable=False)
    room_code = db.Column(db.String(6), unique=True, nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_private = db.Column(db.Boolean, default=False)
    room_password = db.Column(db.String(200), nullable=True)
    creator = db.relationship('User', foreign_keys=[created_by], backref='created_rooms')
    members = db.relationship('User', secondary=room_members, backref='joined_rooms')
    messages = db.relationship('ChatMessage', cascade='all, delete-orphan', backref='room', lazy=True)
    materials = db.relationship('StudyMaterial', cascade='all, delete-orphan', backref='room', lazy=True)

class StudyHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    room_id = db.Column(db.Integer, db.ForeignKey('room.id', ondelete='CASCADE'), nullable=False)
    action = db.Column(db.String(20), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', backref='history')
    room = db.relationship('Room')

class ChatMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.Integer, db.ForeignKey('room.id', ondelete='CASCADE'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    message = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', backref='messages')

class StudyMaterial(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.Integer, db.ForeignKey('room.id', ondelete='CASCADE'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    original_filename = db.Column(db.String(255), nullable=False)
    stored_filename = db.Column(db.String(255), unique=True, nullable=False)
    file_type = db.Column(db.String(100), nullable=False)
    file_size = db.Column(db.Integer, nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', backref='materials')

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# ---------- Global State ----------
whiteboard_permissions = {}
screen_share_active = {}
call_sessions = {}
active_polls = {}

# ---------- Create Tables ----------
with app.app_context():
    db.create_all()
    os.makedirs('uploads', exist_ok=True)
    os.makedirs('whiteboard_data', exist_ok=True)

# ============================================
# AUTH ROUTES
# ============================================
@app.route('/')
def welcome():
    return render_template('Wellcome.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        return render_template('login.html')
    login_input = request.form.get('username', '').strip()
    password = request.form.get('password', '')
    if not login_input or not password:
        flash('Both fields are required.', 'danger')
        return redirect(url_for('login'))
    if '@' in login_input:
        user = User.query.filter_by(email=login_input).first()
    else:
        user = User.query.filter_by(username=login_input).first()
    if not user or not user.check_password(password):
        flash('Invalid credentials.', 'danger')
        return redirect(url_for('login'))
    login_user(user)
    flash(f'Welcome {user.username}!', 'success')
    return redirect(url_for('dashboard'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'GET':
        return render_template('register.html')
    username = request.form.get('username', '').strip()
    email = request.form.get('email', '').strip()
    password = request.form.get('password', '')
    confirm = request.form.get('confirmPassword', '')
    if not username or not email or not password or not confirm:
        flash('All fields are required.', 'danger')
        return redirect(url_for('register'))
    if password != confirm:
        flash('Passwords do not match.', 'danger')
        return redirect(url_for('register'))
    if len(password) < 8:
        flash('Password must be at least 8 characters.', 'danger')
        return redirect(url_for('register'))
    if User.query.filter_by(username=username).first():
        flash('Username already taken.', 'danger')
        return redirect(url_for('register'))
    if User.query.filter_by(email=email).first():
        flash('Email already registered.', 'danger')
        return redirect(url_for('register'))
    new_user = User(username=username, email=email, plain_password=password)
    db.session.add(new_user)
    db.session.commit()
    flash('Registration successful! Please log in.', 'success')
    return redirect(url_for('login'))

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'success')
    return redirect(url_for('welcome'))

@app.route('/forgotPassword', methods=['GET', 'POST'])
def forgotPassword():
    if request.method == 'GET':
        return render_template('forgotPassword.html')
    flash('Password reset feature coming soon.', 'info')
    return redirect(url_for('login'))

# ============================================
# DASHBOARD & PROFILE
# ============================================
@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html')

@app.route('/profile')
@login_required
def profile():
    history = StudyHistory.query.filter_by(user_id=current_user.id).order_by(StudyHistory.timestamp.desc()).limit(20).all()
    created_rooms = Room.query.filter_by(created_by=current_user.id).all()
    joined_rooms = current_user.joined_rooms
    return render_template('profile.html', user=current_user, history=history, created_rooms=created_rooms, joined_rooms=joined_rooms)

# ============================================
# ROOM ROUTES
# ============================================
@app.route('/rooms')
@login_required
def rooms():
    created = Room.query.filter_by(created_by=current_user.id).all()
    joined = current_user.joined_rooms
    room_ids = {r.id for r in created}
    all_rooms = created + [r for r in joined if r.id not in room_ids]
    return render_template('rooms.html', rooms=all_rooms)

@app.route('/rooms/create', methods=['GET', 'POST'])
@login_required
def create_room():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()
        course_code = request.form.get('course_code', '').strip().upper()
        is_private = request.form.get('protectCheckbox') == 'on'
        room_password = request.form.get('room_password', '').strip()
        if not name or not course_code:
            flash('Room name and course code are required.', 'danger')
            return redirect(url_for('create_room'))
        if Room.query.filter_by(course_code=course_code).first():
            flash('A room with this course code already exists.', 'danger')
            return redirect(url_for('create_room'))
        if is_private and (not room_password or len(room_password) < 4):
            flash('Password must be at least 4 characters.', 'danger')
            return redirect(url_for('create_room'))
        room_code = generate_room_code()
        while Room.query.filter_by(room_code=room_code).first():
            room_code = generate_room_code()
        hashed_pw = None
        if is_private and room_password:
            hashed_pw = bcrypt.hashpw(room_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        new_room = Room(
            name=name, description=description, course_code=course_code,
            room_code=room_code, created_by=current_user.id,
            is_private=is_private, room_password=hashed_pw
        )
        db.session.add(new_room)
        db.session.commit()
        new_room.members.append(current_user)
        history = StudyHistory(user_id=current_user.id, room_id=new_room.id, action='created')
        db.session.add(history)
        db.session.commit()
        flash(f'Room "{name}" created! Code: {room_code}', 'success')
        return redirect(url_for('rooms'))
    return render_template('create_room.html')

@app.route('/rooms/join', methods=['POST'])
@login_required
def join_room():
    code = request.form.get('room_code', '').strip().upper()
    password = request.form.get('room_password', '')
    if not code:
        flash('Please enter a room code.', 'danger')
        return redirect(url_for('rooms'))
    room = Room.query.filter_by(room_code=code).first()
    if not room:
        flash('Invalid room code.', 'danger')
        return redirect(url_for('rooms'))
    if room.is_private:
        if not password:
            return render_template('password_prompt.html', room_code=code)
        if not room.room_password or not bcrypt.checkpw(password.encode('utf-8'), room.room_password.encode('utf-8')):
            flash('Incorrect room password.', 'danger')
            return redirect(url_for('rooms'))
    if current_user in room.members:
        flash('Already a member.', 'info')
        return redirect(url_for('room_detail', room_id=room.id))
    room.members.append(current_user)
    history = StudyHistory(user_id=current_user.id, room_id=room.id, action='joined')
    db.session.add(history)
    db.session.commit()
    flash(f'Joined "{room.name}"!', 'success')
    return redirect(url_for('room_detail', room_id=room.id))

@app.route('/rooms/<int:room_id>')
@login_required
def room_detail(room_id):
    room = Room.query.get_or_404(room_id)
    if not can_access_room(current_user, room):
        flash('Access denied.', 'danger')
        return redirect(url_for('rooms'))
    return render_template('room_detail.html', room=room)

@app.route('/rooms/delete/<int:room_id>')
@login_required
def delete_room(room_id):
    room = Room.query.get_or_404(room_id)
    if room.created_by != current_user.id:
        flash('Only creator can delete.', 'danger')
        return redirect(url_for('rooms'))
    db.session.delete(room)
    db.session.commit()
    flash(f'Room "{room.name}" deleted.', 'success')
    return redirect(url_for('rooms'))

# ============================================
# FILE ROUTES
# ============================================
@app.route('/rooms/<int:room_id>/files')
@login_required
def room_files(room_id):
    room = Room.query.get_or_404(room_id)
    if not can_access_room(current_user, room):
        return jsonify({'error': 'Access denied'}), 403
    files = StudyMaterial.query.filter_by(room_id=room_id).order_by(StudyMaterial.uploaded_at.desc()).all()
    return jsonify({'files': [{
        'id': f.id, 'original_filename': f.original_filename,
        'file_type': f.file_type or '', 'file_size': f.file_size,
        'uploaded_at': f.uploaded_at.strftime('%Y-%m-%d %H:%M'),
        'uploader': f.user.username, 'is_uploader': f.user_id == current_user.id,
        'is_host': current_user.id == room.created_by,
        'file_icon': get_file_icon(f.file_type)
    } for f in files]})

@app.route('/rooms/<int:room_id>/upload', methods=['POST'])
@login_required
def upload_file(room_id):
    room = Room.query.get_or_404(room_id)
    if not can_access_room(current_user, room):
        return jsonify({'error': 'Access denied'}), 403
    if 'file' not in request.files:
        return jsonify({'error': 'No file'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file'}), 400
    upload_folder = os.path.join('uploads', f'room_{room_id}')
    os.makedirs(upload_folder, exist_ok=True)
    ext = os.path.splitext(file.filename)[1]
    stored = f"{uuid.uuid4().hex}{ext}"
    file_path = os.path.join(upload_folder, stored)
    file.save(file_path)
    new_file = StudyMaterial(
        room_id=room_id, user_id=current_user.id,
        original_filename=file.filename, stored_filename=stored,
        file_type=file.content_type or mimetypes.guess_type(file.filename)[0] or 'application/octet-stream',
        file_size=os.path.getsize(file_path)
    )
    db.session.add(new_file)
    db.session.commit()
    return jsonify({'success': True, 'file': {
        'id': new_file.id, 'original_filename': new_file.original_filename,
        'file_type': new_file.file_type, 'file_size': new_file.file_size,
        'uploaded_at': new_file.uploaded_at.strftime('%Y-%m-%d %H:%M'),
        'uploader': current_user.username, 'is_uploader': True,
        'is_host': current_user.id == room.created_by,
        'file_icon': get_file_icon(new_file.file_type)
    }}), 201

@app.route('/rooms/<int:room_id>/download/<int:file_id>')
@login_required
def download_file(room_id, file_id):
    room = Room.query.get_or_404(room_id)
    if not can_access_room(current_user, room):
        flash('Access denied.', 'danger')
        return redirect(url_for('room_detail', room_id=room_id))
    file = StudyMaterial.query.get_or_404(file_id)
    file_path = os.path.join('uploads', f'room_{room_id}', file.stored_filename)
    if not os.path.exists(file_path):
        flash('File not found.', 'danger')
        return redirect(url_for('room_detail', room_id=room_id))
    return send_file(file_path, as_attachment=True, download_name=file.original_filename)

@app.route('/rooms/<int:room_id>/delete/<int:file_id>', methods=['DELETE'])
@login_required
def delete_file(room_id, file_id):
    room = Room.query.get_or_404(room_id)
    if not can_access_room(current_user, room):
        return jsonify({'error': 'Access denied'}), 403
    file = StudyMaterial.query.get_or_404(file_id)
    if file.user_id != current_user.id and current_user.id != room.created_by:
        return jsonify({'error': 'Permission denied'}), 403
    file_path = os.path.join('uploads', f'room_{room_id}', file.stored_filename)
    if os.path.exists(file_path):
        os.remove(file_path)
    db.session.delete(file)
    db.session.commit()
    return jsonify({'success': True})

# ============================================
# DEV ROUTES
# ============================================
@app.route('/dev/users')
def dev_users():
    return render_template('dev_users.html', users=User.query.all())

@app.route('/dev/user/add', methods=['GET', 'POST'])
def dev_add_user():
    if request.method == 'POST':
        u = request.form['username']; e = request.form['email']
        p = request.form['password']; c = request.form['confirm']
        if not u or not e or not p: 
            flash('All fields required.', 'danger')
            return redirect(url_for('dev_add_user'))
        if p != c: 
            flash('Passwords do not match.', 'danger')
            return redirect(url_for('dev_add_user'))
        if len(p) < 8: 
            flash('Min 8 characters.', 'danger')
            return redirect(url_for('dev_add_user'))
        if User.query.filter_by(username=u).first(): 
            flash('Username exists.', 'danger')
            return redirect(url_for('dev_add_user'))
        if User.query.filter_by(email=e).first(): 
            flash('Email exists.', 'danger')
            return redirect(url_for('dev_add_user'))
        db.session.add(User(username=u, email=e, plain_password=p))
        db.session.commit()
        flash(f'User {u} added.', 'success')
        return redirect(url_for('dev_users'))
    return render_template('dev_user_form.html', title='Add User', user=None)

@app.route('/dev/user/edit/<int:user_id>', methods=['GET', 'POST'])
def dev_edit_user(user_id):
    user = User.query.get_or_404(user_id)
    if request.method == 'POST':
        user.username = request.form['username']
        user.email = request.form['email']
        pw = request.form.get('password', '')
        if pw and len(pw) >= 8:
            user.password_hash = bcrypt.hashpw(pw.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        db.session.commit()
        flash(f'User {user.username} updated.', 'success')
        return redirect(url_for('dev_users'))
    return render_template('dev_user_form.html', title='Edit User', user=user)

@app.route('/dev/user/delete/<int:user_id>')
def dev_delete_user(user_id):
    user = User.query.get_or_404(user_id)
    db.session.delete(user)
    db.session.commit()
    flash(f'User {user.username} deleted.', 'danger')
    return redirect(url_for('dev_users'))

# ============================================
# SOCKET EVENTS
# ============================================

@socketio.on('chat_message')
def handle_chat(data):
    room_id = str(data['room_id'])
    msg = data['message'].strip()
    if not msg: return
    room = Room.query.get(int(room_id))
    if not room: return
    cm = ChatMessage(room_id=room.id, user_id=current_user.id, message=msg)
    db.session.add(cm); db.session.commit()
    emit('chat_message', {'username': current_user.username, 'message': msg, 'timestamp': cm.timestamp.strftime('%I:%M %p'), 'user_id': current_user.id}, to=room_id, skip_sid=request.sid)

@socketio.on('load_chat_history')
def handle_chat_history(data):
    room_id = str(data['room_id'])
    room = Room.query.get(int(room_id))
    if not room: return
    msgs = ChatMessage.query.filter_by(room_id=room.id).order_by(ChatMessage.timestamp.asc()).limit(100).all()
    emit('chat_history', {'messages': [{'username': m.user.username, 'message': m.message, 'timestamp': m.timestamp.strftime('%I:%M %p'), 'user_id': m.user_id} for m in msgs]}, to=request.sid)

# Whiteboard
@socketio.on('save_whiteboard')
def handle_save_wb(data):
    os.makedirs('whiteboard_data', exist_ok=True)
    with open(f'whiteboard_data/room_{data["room_id"]}.json', 'w') as f: f.write(data['canvas_data'])

@socketio.on('load_whiteboard')
def handle_load_wb(data):
    path = f'whiteboard_data/room_{data["room_id"]}.json'
    if os.path.exists(path):
        with open(path) as f: emit('load_whiteboard', {'canvas_data': f.read()}, to=request.sid)
    else: emit('load_whiteboard', {'canvas_data': None}, to=request.sid)

@socketio.on('draw')
def handle_draw(data):
    room_id = str(data['room_id'])
    room = Room.query.get(int(room_id))
    if room and (room.created_by == current_user.id or current_user.id in whiteboard_permissions.get(room_id, [])):
        emit('draw', data, to=room_id, skip_sid=request.sid)

@socketio.on('clear_canvas')
def handle_clear(data):
    room_id = str(data['room_id'])
    if Room.query.get(int(room_id)).created_by == current_user.id:
        emit('clear_canvas', to=room_id)

# Room
@socketio.on('join_room')
def handle_join(data):
    room_id = str(data['room_id'])
    socket_join_room(room_id)
    emit('joined_room', {'room_id': room_id}, to=request.sid)
    room = Room.query.get(int(room_id))
    can = room.created_by == current_user.id or current_user.id in whiteboard_permissions.get(room_id, [])
    emit('permission_update', {'user_id': current_user.id, 'can_control': can}, to=request.sid)

@socketio.on('toggle_permission')
def handle_perm(data):
    room_id = str(data['room_id']); uid = data['user_id']
    if Room.query.get(int(room_id)).created_by != current_user.id: return
    if room_id not in whiteboard_permissions: whiteboard_permissions[room_id] = []
    if uid in whiteboard_permissions[room_id]: whiteboard_permissions[room_id].remove(uid); can = False
    else: whiteboard_permissions[room_id].append(uid); can = True
    emit('permission_update', {'user_id': uid, 'can_control': can}, to=room_id)

# Tools
@socketio.on('tool_started')
def handle_tool_start(data):
    emit('tool_started', {'tool': data['tool']}, to=str(data['room_id']), skip_sid=request.sid)

@socketio.on('tool_stopped')
def handle_tool_stop(data):
    emit('tool_stopped', {'tool': data['tool']}, to=str(data['room_id']), skip_sid=request.sid)

# Screen Share
@socketio.on('screen_frame')
def handle_frame(data):
    emit('screen_frame', {'frame': data['frame']}, to=str(data['room_id']), skip_sid=request.sid)

@socketio.on('screen_share_started')
def handle_ss_start(data):
    screen_share_active[str(data['room_id'])] = True
    emit('tool_started', {'tool': 'screen_share'}, to=str(data['room_id']), skip_sid=request.sid)

@socketio.on('screen_share_stopped')
def handle_ss_stop(data):
    screen_share_active[str(data['room_id'])] = False
    emit('tool_stopped', {'tool': 'screen_share'}, to=str(data['room_id']), skip_sid=request.sid)

# Call
@socketio.on('call_start')
def handle_call_start(data):
    rid = str(data['room_id'])
    if rid not in call_sessions: call_sessions[rid] = []
    if current_user.id not in call_sessions[rid]: call_sessions[rid].append(current_user.id)
    emit('call_started', {'caller_id': current_user.id}, to=rid, skip_sid=request.sid)

@socketio.on('call_join')
def handle_call_join(data):
    rid = str(data['room_id'])
    if rid not in call_sessions: return
    if current_user.id not in call_sessions[rid]: call_sessions[rid].append(current_user.id)
    emit('user_joined_call', {'user_id': current_user.id}, to=rid, skip_sid=request.sid)

@socketio.on('call_offer')
def handle_offer(data): emit('call_offer', {'offer': data['offer'], 'from': current_user.id}, to=data['target_id'])

@socketio.on('call_answer')
def handle_answer(data): emit('call_answer', {'answer': data['answer'], 'from': current_user.id}, to=data['target_id'])

@socketio.on('ice_candidate')
def handle_ice(data): emit('ice_candidate', {'candidate': data['candidate'], 'from': current_user.id}, to=data['target_id'])

@socketio.on('call_end')
def handle_call_end(data):
    rid = str(data['room_id'])
    if rid in call_sessions and current_user.id in call_sessions[rid]: call_sessions[rid].remove(current_user.id)
    emit('user_left_call', {'user_id': current_user.id}, to=rid, skip_sid=request.sid)

# Reactions
@socketio.on('raise_hand')
def handle_rh(data): emit('hand_raised', {'user_id': current_user.id, 'username': current_user.username}, to=str(data['room_id']))

@socketio.on('lower_hand')
def handle_lh(data): emit('hand_lowered', {'user_id': current_user.id}, to=str(data['room_id']))

@socketio.on('send_reaction')
def handle_reaction(data): emit('reaction_received', {'user_id': current_user.id, 'username': current_user.username, 'emoji': data['emoji']}, to=str(data['room_id']))

# Polls
@socketio.on('create_poll')
def handle_create_poll(data):
    rid = int(data['room_id'])
    room = Room.query.get(rid)
    if not room or room.created_by != current_user.id: return
    active_polls[rid] = {'question': data['question'], 'options': data['options'], 'votes': {o: 0 for o in data['options']}, 'voters': {}, 'is_active': True, 'created_by': current_user.username}
    emit('poll_created', {'question': data['question'], 'options': data['options'], 'created_by': current_user.username}, to=str(rid))

@socketio.on('vote_poll')
def handle_vote(data):
    rid = int(data['room_id']); opt = data['option']
    poll = active_polls.get(rid)
    if not poll or not poll['is_active']: return
    uid = current_user.id
    if uid in poll['voters']: poll['votes'][poll['voters'][uid]] -= 1
    poll['voters'][uid] = opt; poll['votes'][opt] += 1
    emit('poll_updated', {'votes': poll['votes'], 'total_votes': sum(poll['votes'].values())}, to=str(rid))

@socketio.on('close_poll')
def handle_close_poll(data):
    rid = int(data['room_id'])
    if Room.query.get(rid).created_by != current_user.id: return
    poll = active_polls.get(rid)
    if poll: poll['is_active'] = False; emit('poll_closed', {'votes': poll['votes'], 'total_votes': sum(poll['votes'].values())}, to=str(rid))

@socketio.on('load_poll')
def handle_load_poll(data):
    rid = int(data['room_id'])
    poll = active_polls.get(rid)
    if poll: emit('poll_loaded', {'question': poll['question'], 'options': poll['options'], 'votes': poll['votes'], 'is_active': poll['is_active'], 'created_by': poll['created_by']}, to=request.sid)

@socketio.on('ping')
def handle_ping(data): emit('pong', {}, to=request.sid)

# ============================================
# RUN
# ============================================
if __name__ == '__main__':
    socketio.run(app, debug=True, port=5000)