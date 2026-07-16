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
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max file upload

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
    return user == room.creator or user in room.members


def get_file_icon(file_type):
    if file_type.startswith('image/'):
        return 'bi bi-file-image-fill text-primary'
    elif file_type == 'application/pdf':
        return 'bi bi-file-pdf-fill text-danger'
    elif file_type in ['application/msword', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document']:
        return 'bi bi-file-word-fill text-info'
    elif file_type in ['application/vnd.ms-powerpoint', 'application/vnd.openxmlformats-officedocument.presentationml.presentation']:
        return 'bi bi-file-ppt-fill text-warning'
    elif file_type in ['application/vnd.ms-excel', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet']:
        return 'bi bi-file-excel-fill text-success'
    else:
        return 'bi bi-file-earmark-fill text-secondary'

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
    # Cascade delete for all related tables
    history = db.relationship('StudyHistory', cascade='all, delete-orphan', backref='room', lazy=True)
    messages = db.relationship('ChatMessage', cascade='all, delete-orphan', backref='room', lazy=True)
    materials = db.relationship('StudyMaterial', cascade='all, delete-orphan', backref='room', lazy=True)

class StudyHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    room_id = db.Column(db.Integer, db.ForeignKey('room.id', ondelete='CASCADE'), nullable=False)
    action = db.Column(db.String(20), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', backref='history')

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
    file_size = db.Column(db.Integer, nullable=False)  # in bytes
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', backref='materials')

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# ---------- Permission & Call Tracking ----------
whiteboard_permissions = {}
screen_share_active = {}
call_sessions = {}

# ---------- Create Tables ----------
with app.app_context():
    db.create_all()
    os.makedirs('uploads', exist_ok=True)

# ---------- Authentication Routes ----------
@app.route('/')
def welcome():
    return render_template('Wellcome.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        return render_template('login.html')
    login_input = request.form.get('username')
    password = request.form.get('password')
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
    username = request.form.get('username')
    email = request.form.get('email')
    password = request.form.get('password')
    confirm = request.form.get('confirmPassword')
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

# ---------- Room Routes ----------
@app.route('/rooms')
@login_required
def rooms():
    created = Room.query.filter_by(created_by=current_user.id).all()
    joined = current_user.joined_rooms
    room_ids = {room.id for room in created}
    all_rooms = created + [room for room in joined if room.id not in room_ids]
    return render_template('rooms.html', rooms=all_rooms)

@app.route('/rooms/create', methods=['GET', 'POST'])
@login_required
def create_room():
    if request.method == 'POST':
        name = request.form.get('name')
        description = request.form.get('description')
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
            flash('Password must be at least 4 characters for a protected room.', 'danger')
            return redirect(url_for('create_room'))
        room_code = generate_room_code()
        while Room.query.filter_by(room_code=room_code).first():
            room_code = generate_room_code()
        hashed_pw = None
        if is_private and room_password:
            hashed_pw = bcrypt.hashpw(room_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        new_room = Room(
            name=name,
            description=description,
            course_code=course_code,
            room_code=room_code,
            created_by=current_user.id,
            is_private=is_private,
            room_password=hashed_pw
        )
        db.session.add(new_room)
        db.session.commit()
        new_room.members.append(current_user)
        db.session.commit()
        history = StudyHistory(user_id=current_user.id, room_id=new_room.id, action='created')
        db.session.add(history)
        db.session.commit()
        flash(f'Room "{name}" created! Join code: {room_code}', 'success')
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
        flash('You are already a member of this room.', 'info')
        return redirect(url_for('room_detail', room_id=room.id))
    room.members.append(current_user)
    db.session.commit()
    history = StudyHistory(user_id=current_user.id, room_id=room.id, action='joined')
    db.session.add(history)
    db.session.commit()
    flash(f'You joined "{room.name}"!', 'success')
    return redirect(url_for('room_detail', room_id=room.id))

@app.route('/rooms/<int:room_id>')
@login_required
def room_detail(room_id):
    room = Room.query.get_or_404(room_id)
    if not can_access_room(current_user, room):
        flash('You do not have access to this room.', 'danger')
        return redirect(url_for('rooms'))
    return render_template('room_detail.html', room=room)

@app.route('/rooms/delete/<int:room_id>')
@login_required
def delete_room(room_id):
    room = Room.query.get_or_404(room_id)
    if room.created_by != current_user.id:
        flash('Only the creator can delete the room.', 'danger')
        return redirect(url_for('rooms'))
    # Cascade delete handles all related records
    db.session.delete(room)
    db.session.commit()
    flash(f'Room "{room.name}" deleted.', 'success')
    return redirect(url_for('rooms'))

# ---------- Study Materials Routes ----------
@app.route('/rooms/<int:room_id>/files')
@login_required
def room_files(room_id):
    room = Room.query.get_or_404(room_id)
    if not can_access_room(current_user, room):
        return jsonify({'error': 'Access denied'}), 403
    
    files = StudyMaterial.query.filter_by(room_id=room_id).order_by(StudyMaterial.uploaded_at.desc()).all()
    files_data = [{
        'id': f.id,
        'original_filename': f.original_filename,
        'file_type': f.file_type,
        'file_size': f.file_size,
        'uploaded_at': f.uploaded_at.strftime('%Y-%m-%d %H:%M'),
        'uploader': f.user.username,
        'is_uploader': f.user_id == current_user.id,
        'is_host': current_user.id == room.created_by,
        'file_icon': get_file_icon(f.file_type)
    } for f in files]
    return jsonify({'files': files_data})

@app.route('/rooms/<int:room_id>/upload', methods=['POST'])
@login_required
def upload_file(room_id):
    room = Room.query.get_or_404(room_id)
    if not can_access_room(current_user, room):
        return jsonify({'error': 'Access denied'}), 403
    
    if 'file' not in request.files:
        return jsonify({'error': 'No file selected'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    # Create room upload folder if not exists
    upload_folder = os.path.join('uploads', f'room_{room_id}')
    os.makedirs(upload_folder, exist_ok=True)
    
    # Generate unique filename
    ext = os.path.splitext(file.filename)[1]
    stored_filename = f"{uuid.uuid4().hex}{ext}"
    original_filename = file.filename
    
    # Save file
    file_path = os.path.join(upload_folder, stored_filename)
    file.save(file_path)
    
    # Save to database
    new_file = StudyMaterial(
        room_id=room_id,
        user_id=current_user.id,
        original_filename=original_filename,
        stored_filename=stored_filename,
        file_type=file.content_type or mimetypes.guess_type(original_filename)[0] or 'application/octet-stream',
        file_size=os.path.getsize(file_path)
    )
    db.session.add(new_file)
    db.session.commit()
    
    return jsonify({
        'success': True,
        'file': {
            'id': new_file.id,
            'original_filename': new_file.original_filename,
            'file_type': new_file.file_type,
            'file_size': new_file.file_size,
            'uploaded_at': new_file.uploaded_at.strftime('%Y-%m-%d %H:%M'),
            'uploader': current_user.username,
            'is_uploader': True,
            'is_host': current_user.id == room.created_by,
            'file_icon': get_file_icon(new_file.file_type)
        }
    }), 201

@app.route('/rooms/<int:room_id>/download/<int:file_id>')
@login_required
def download_file(room_id, file_id):
    room = Room.query.get_or_404(room_id)
    if not can_access_room(current_user, room):
        flash('Access denied.', 'danger')
        return redirect(url_for('room_detail', room_id=room_id))
    
    file = StudyMaterial.query.get_or_404(file_id)
    if file.room_id != room_id:
        flash('File not found in this room.', 'danger')
        return redirect(url_for('room_detail', room_id=room_id))
    
    file_path = os.path.join('uploads', f'room_{room_id}', file.stored_filename)
    if not os.path.exists(file_path):
        flash('File not found on server.', 'danger')
        return redirect(url_for('room_detail', room_id=room_id))
    
    return send_file(file_path, as_attachment=True, download_name=file.original_filename)

@app.route('/rooms/<int:room_id>/delete/<int:file_id>', methods=['DELETE'])
@login_required
def delete_file(room_id, file_id):
    room = Room.query.get_or_404(room_id)
    if not can_access_room(current_user, room):
        return jsonify({'error': 'Access denied'}), 403
    
    file = StudyMaterial.query.get_or_404(file_id)
    if file.room_id != room_id:
        return jsonify({'error': 'File not found in this room'}), 404
    
    # Check permission: uploader or host can delete
    if file.user_id != current_user.id and current_user.id != room.created_by:
        return jsonify({'error': 'Permission denied'}), 403
    
    # Delete physical file
    file_path = os.path.join('uploads', f'room_{room_id}', file.stored_filename)
    if os.path.exists(file_path):
        os.remove(file_path)
    
    # Delete from database
    db.session.delete(file)
    db.session.commit()
    
    return jsonify({'success': True})

# ---------- Dev Routes ----------
@app.route('/dev/users')
def dev_users():
    users = User.query.all()
    return render_template('dev_users.html', users=users)

@app.route('/dev/user/add', methods=['GET', 'POST'])
def dev_add_user():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        confirm = request.form['confirm']
        if not username or not email or not password:
            flash('All fields required.', 'danger')
            return redirect(url_for('dev_add_user'))
        if password != confirm:
            flash('Passwords do not match.', 'danger')
            return redirect(url_for('dev_add_user'))
        if len(password) < 8:
            flash('Password must be at least 8 characters.', 'danger')
            return redirect(url_for('dev_add_user'))
        if User.query.filter_by(username=username).first():
            flash('Username already exists.', 'danger')
            return redirect(url_for('dev_add_user'))
        if User.query.filter_by(email=email).first():
            flash('Email already exists.', 'danger')
            return redirect(url_for('dev_add_user'))
        new_user = User(username=username, email=email, plain_password=password)
        db.session.add(new_user)
        db.session.commit()
        flash(f'User {username} added.', 'success')
        return redirect(url_for('dev_users'))
    return render_template('dev_user_form.html', title='Add User', user=None)

@app.route('/dev/user/edit/<int:user_id>', methods=['GET', 'POST'])
def dev_edit_user(user_id):
    user = User.query.get_or_404(user_id)
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form.get('password', '')
        if not username or not email:
            flash('Username and email required.', 'danger')
            return redirect(url_for('dev_edit_user', user_id=user_id))
        if User.query.filter(User.username == username, User.id != user_id).first():
            flash('Username already taken.', 'danger')
            return redirect(url_for('dev_edit_user', user_id=user_id))
        if User.query.filter(User.email == email, User.id != user_id).first():
            flash('Email already taken.', 'danger')
            return redirect(url_for('dev_edit_user', user_id=user_id))
        user.username = username
        user.email = email
        if password and len(password) >= 8:
            user.password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        elif password:
            flash('Password must be at least 8 characters (unchanged).', 'warning')
        db.session.commit()
        flash(f'User {username} updated.', 'success')
        return redirect(url_for('dev_users'))
    return render_template('dev_user_form.html', title='Edit User', user=user)

@app.route('/dev/user/delete/<int:user_id>')
def dev_delete_user(user_id):
    user = User.query.get_or_404(user_id)
    db.session.delete(user)
    db.session.commit()
    flash(f'User {user.username} deleted.', 'danger')
    return redirect(url_for('dev_users'))

# ---------- Socket.IO Events ----------

# --- Chat ---
@socketio.on('chat_message')
def handle_chat_message(data):
    room_id = str(data['room_id'])
    message_text = data['message'].strip()
    if not message_text:
        return
    room = Room.query.get(int(room_id))
    if not room:
        return
    new_msg = ChatMessage(
        room_id=room.id,
        user_id=current_user.id,
        message=message_text
    )
    db.session.add(new_msg)
    db.session.commit()
    emit('chat_message', {
        'username': current_user.username,
        'message': message_text,
        'timestamp': new_msg.timestamp.strftime('%I:%M %p'),
        'user_id': current_user.id
    }, to=room_id, skip_sid=request.sid)

@socketio.on('load_chat_history')
def handle_load_chat_history(data):
    room_id = str(data['room_id'])
    room = Room.query.get(int(room_id))
    if not room:
        return
    messages = ChatMessage.query.filter_by(room_id=room.id)\
        .order_by(ChatMessage.timestamp.asc())\
        .limit(100).all()
    history = [{
        'username': msg.user.username,
        'message': msg.message,
        'timestamp': msg.timestamp.strftime('%I:%M %p'),
        'user_id': msg.user_id
    } for msg in messages]
    emit('chat_history', {'messages': history}, to=request.sid)

# --- Whiteboard Save/Load ---
@socketio.on('save_whiteboard')
def handle_save_whiteboard(data):
    room_id = data['room_id']
    canvas_json = data['canvas_data']
    os.makedirs('whiteboard_data', exist_ok=True)
    with open(f'whiteboard_data/room_{room_id}.json', 'w') as f:
        f.write(canvas_json)

@socketio.on('load_whiteboard')
def handle_load_whiteboard(data):
    room_id = data['room_id']
    path = f'whiteboard_data/room_{room_id}.json'
    if os.path.exists(path):
        with open(path, 'r') as f:
            canvas_json = f.read()
        emit('load_whiteboard', {'canvas_data': canvas_json}, to=request.sid)
    else:
        emit('load_whiteboard', {'canvas_data': None}, to=request.sid)

# --- Room Joining & Permissions ---
@socketio.on('join_room')
def handle_join_room(data):
    room_id = str(data['room_id'])
    socket_join_room(room_id)
    emit('joined_room', {'room_id': room_id}, to=request.sid)
    room = Room.query.get(int(room_id))
    user_id = current_user.id
    if room.created_by == user_id:
        can_control = True
    else:
        can_control = user_id in whiteboard_permissions.get(room_id, [])
    emit('permission_update', {'user_id': user_id, 'can_control': can_control}, to=request.sid)
    if room_id in call_sessions and call_sessions[room_id]:
        emit('call_already_active', {'caller_id': call_sessions[room_id][0]}, to=request.sid)

# --- Drawing ---
@socketio.on('draw')
def handle_draw(data):
    room_id = str(data['room_id'])
    user_id = current_user.id
    room = Room.query.get(int(room_id))
    if room.created_by == user_id or user_id in whiteboard_permissions.get(room_id, []):
        emit('draw', data, to=room_id, skip_sid=request.sid)

@socketio.on('clear_canvas')
def handle_clear(data):
    room_id = str(data['room_id'])
    user_id = current_user.id
    room = Room.query.get(int(room_id))
    if room.created_by == user_id:
        emit('clear_canvas', to=room_id)

# --- Tools ---
@socketio.on('tool_started')
def handle_tool_started(data):
    room_id = str(data['room_id'])
    tool = data['tool']
    emit('tool_started', {'tool': tool}, to=room_id, skip_sid=request.sid)

@socketio.on('tool_stopped')
def handle_tool_stopped(data):
    room_id = str(data['room_id'])
    tool = data['tool']
    emit('tool_stopped', {'tool': tool}, to=room_id, skip_sid=request.sid)

# --- Screen Share ---
@socketio.on('screen_frame')
def handle_screen_frame(data):
    room_id = str(data['room_id'])
    frame = data['frame']
    emit('screen_frame', {'frame': frame}, to=room_id, skip_sid=request.sid)

@socketio.on('screen_share_started')
def handle_screen_share_started(data):
    room_id = str(data['room_id'])
    user_id = current_user.id
    room = Room.query.get(int(room_id))
    if room.created_by == user_id or user_id in whiteboard_permissions.get(room_id, []):
        screen_share_active[room_id] = True
        emit('tool_started', {'tool': 'screen_share'}, to=room_id, skip_sid=request.sid)

@socketio.on('screen_share_stopped')
def handle_screen_share_stopped(data):
    room_id = str(data['room_id'])
    screen_share_active[room_id] = False
    emit('tool_stopped', {'tool': 'screen_share'}, to=room_id, skip_sid=request.sid)

# --- Permissions (Toggle) ---
@socketio.on('toggle_permission')
def handle_toggle_permission(data):
    room_id = str(data['room_id'])
    user_id = data['user_id']
    room = Room.query.get(int(room_id))
    if room.created_by != current_user.id:
        return
    if room_id not in whiteboard_permissions:
        whiteboard_permissions[room_id] = []
    if user_id in whiteboard_permissions[room_id]:
        whiteboard_permissions[room_id].remove(user_id)
        can_control = False
    else:
        whiteboard_permissions[room_id].append(user_id)
        can_control = True
    emit('permission_update', {'user_id': user_id, 'can_control': can_control}, to=room_id)

# --- Audio/Video Call Signaling ---
@socketio.on('call_start')
def handle_call_start(data):
    room_id = str(data['room_id'])
    caller_id = current_user.id
    if room_id not in call_sessions:
        call_sessions[room_id] = []
    if caller_id not in call_sessions[room_id]:
        call_sessions[room_id].append(caller_id)
    emit('call_started', {'caller_id': caller_id}, to=room_id, skip_sid=request.sid)

@socketio.on('call_join')
def handle_call_join(data):
    room_id = str(data['room_id'])
    user_id = current_user.id
    if room_id not in call_sessions:
        return
    if user_id not in call_sessions[room_id]:
        call_sessions[room_id].append(user_id)
    emit('user_joined_call', {'user_id': user_id}, to=room_id, skip_sid=request.sid)

@socketio.on('call_offer')
def handle_call_offer(data):
    room_id = str(data['room_id'])
    target_id = data['target_id']
    offer = data['offer']
    emit('call_offer', {'offer': offer, 'from': current_user.id}, to=target_id)

@socketio.on('call_answer')
def handle_call_answer(data):
    room_id = str(data['room_id'])
    target_id = data['target_id']
    answer = data['answer']
    emit('call_answer', {'answer': answer, 'from': current_user.id}, to=target_id)

@socketio.on('ice_candidate')
def handle_ice_candidate(data):
    room_id = str(data['room_id'])
    target_id = data['target_id']
    candidate = data['candidate']
    emit('ice_candidate', {'candidate': candidate, 'from': current_user.id}, to=target_id)

@socketio.on('call_end')
def handle_call_end(data):
    room_id = str(data['room_id'])
    user_id = current_user.id
    if room_id in call_sessions and user_id in call_sessions[room_id]:
        call_sessions[room_id].remove(user_id)
    emit('user_left_call', {'user_id': user_id}, to=room_id, skip_sid=request.sid)

# --- Ping ---
@socketio.on('ping')
def handle_ping(data):
    emit('pong', {'reply': 'pong from server'}, to=request.sid)

# ---------- Run ----------
if __name__ == '__main__':
    socketio.run(app, debug=True)