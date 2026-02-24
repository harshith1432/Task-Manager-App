import os
import datetime
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor
from twilio.rest import Client
from flask_apscheduler import APScheduler

# Load environment variables
load_dotenv()

app = Flask(__name__, static_folder='public')
CORS(app)

# Twilio Configuration
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_WHATSAPP_NUMBER = os.getenv('TWILIO_WHATSAPP_NUMBER')

twilio_client = None
if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
    twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# Scheduler Configuration
class Config:
    SCHEDULER_API_ENABLED = True

app.config.from_object(Config())
scheduler = APScheduler()
scheduler.init_app(app)
scheduler.start()

def get_db_connection():
    conn = psycopg2.connect(os.getenv('DATABASE_URL'))
    return conn

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Create Users Table with phone number support
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            pin TEXT NOT NULL,
            phone_number TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Create Tasks Table
    cur.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            deadline TIMESTAMP,
            completed BOOLEAN DEFAULT false,
            notified_2h BOOLEAN DEFAULT false,
            notified_1h BOOLEAN DEFAULT false,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Migrations
    try:
        cur.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS phone_number TEXT')
        cur.execute('ALTER TABLE tasks ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE CASCADE')
        cur.execute('ALTER TABLE tasks ADD COLUMN IF NOT EXISTS notified_2h BOOLEAN DEFAULT false')
        cur.execute('ALTER TABLE tasks ADD COLUMN IF NOT EXISTS notified_1h BOOLEAN DEFAULT false')
    except:
        conn.rollback()
    else:
        conn.commit()
    
    cur.close()
    conn.close()
    print("Database finalized with User Phone Number support")

with app.app_context():
    init_db()

# --- Auth Endpoints ---

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.json
    name = data.get('name', '').strip()
    pin = data.get('pin', '').strip()
    phone = data.get('phone', '').strip() # Optional

    if not name or not pin:
        return jsonify({"error": "Name and PIN are required"}), 400

    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute('SELECT * FROM users WHERE name = %s', (name,))
        user = cur.fetchone()

        if user:
            if user['pin'] == pin:
                # Update phone if provided
                if phone and phone != user['phone_number']:
                    cur.execute('UPDATE users SET phone_number = %s WHERE id = %s', (phone, user['id']))
                    conn.commit()
                    user['phone_number'] = phone
                return jsonify({"user_id": user['id'], "name": user['name'], "phone": user['phone_number']}), 200
            else:
                return jsonify({"error": "Incorrect PIN"}), 401
        else:
            cur.execute(
                'INSERT INTO users (name, pin, phone_number) VALUES (%s, %s, %s) RETURNING id, name, phone_number',
                (name, pin, phone if phone else None)
            )
            new_user = cur.fetchone()
            conn.commit()
            return jsonify({"user_id": new_user['id'], "name": new_user['name'], "phone": new_user['phone_number'], "is_new": True}), 201

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()

# --- Task Endpoints ---

@app.route('/api/tasks', methods=['GET'])
def get_tasks():
    user_id = request.args.get('user_id')
    if not user_id: return jsonify({"error": "Unauthorized"}), 401
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute('SELECT * FROM tasks WHERE user_id = %s ORDER BY created_at DESC', (user_id,))
        tasks = cur.fetchall()
        for task in tasks:
            if task['deadline']: task['deadline'] = task['deadline'].isoformat()
            if task['created_at']: task['created_at'] = task['created_at'].isoformat()
        cur.close()
        conn.close()
        return jsonify(tasks)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/tasks', methods=['POST'])
def add_task():
    data = request.json
    user_id = data.get('user_id')
    title = data.get('title')
    deadline = data.get('deadline')

    if not user_id: return jsonify({"error": "Unauthorized"}), 401

    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            'INSERT INTO tasks (user_id, title, deadline) VALUES (%s, %s, %s) RETURNING *',
            (user_id, title, deadline if deadline else None)
        )
        new_task = cur.fetchone()
        conn.commit()
        
        # Send "Task Created" alert
        cur.execute('SELECT name, phone_number FROM users WHERE id = %s', (user_id,))
        user = cur.fetchone()
        if user and user['phone_number']:
            send_whatsapp_notification(user['phone_number'], f"✅ *Mission Accepted*\n\nHey {user['name']}, your new mission *'{title}'* has been recorded. Good luck!")

        if new_task['deadline']: new_task['deadline'] = new_task['deadline'].isoformat()
        if new_task['created_at']: new_task['created_at'] = new_task['created_at'].isoformat()
        cur.close()
        conn.close()
        return jsonify(new_task), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/tasks/<int:id>', methods=['PUT'])
def update_task(id):
    data = request.json
    user_id = data.get('user_id')
    completed = data.get('completed')
    if not user_id: return jsonify({"error": "Unauthorized"}), 401
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            'UPDATE tasks SET completed = %s WHERE id = %s AND user_id = %s RETURNING *',
            (completed, id, user_id)
        )
        updated_task = cur.fetchone()
        conn.commit()
        if updated_task['deadline']: updated_task['deadline'] = updated_task['deadline'].isoformat()
        if updated_task['created_at']: updated_task['created_at'] = updated_task['created_at'].isoformat()
        cur.close()
        conn.close()
        return jsonify(updated_task)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/tasks/<int:id>', methods=['DELETE'])
def delete_task(id):
    user_id = request.args.get('user_id')
    if not user_id: return jsonify({"error": "Unauthorized"}), 401
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('DELETE FROM tasks WHERE id = %s AND user_id = %s', (id, user_id))
        conn.commit()
        cur.close()
        conn.close()
        return '', 204
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve(path):
    if path != "" and os.path.exists(app.static_folder + '/' + path):
        return send_from_directory(app.static_folder, path)
    else:
        return send_from_directory(app.static_folder, 'index.html')

# WhatsApp Notification Scheduler
@scheduler.task('interval', id='check_tasks', minutes=1)
def check_tasks():
    if not twilio_client: return
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        now = datetime.datetime.now()
        
        # 2 Hour Reminders
        cur.execute('''
            SELECT t.*, u.phone_number, u.name as user_name FROM tasks t
            JOIN users u ON t.user_id = u.id
            WHERE t.completed = false AND t.notified_2h = false AND t.deadline IS NOT NULL 
            AND t.deadline > %s AND t.deadline <= %s AND u.phone_number IS NOT NULL
        ''', (now, now + datetime.timedelta(hours=2)))
        for task in cur.fetchall():
            send_whatsapp_notification(task['phone_number'], f"🚀 *Urgent Mission Update*\n\nHey {task['user_name']}, your mission *'{task['title']}'* is due in *2 hours*!")
            cur.execute('UPDATE tasks SET notified_2h = true WHERE id = %s', (task['id'],))

        # 1 Hour Reminders
        cur.execute('''
            SELECT t.*, u.phone_number, u.name as user_name FROM tasks t
            JOIN users u ON t.user_id = u.id
            WHERE t.completed = false AND t.notified_1h = false AND t.deadline IS NOT NULL 
            AND t.deadline > %s AND t.deadline <= %s AND u.phone_number IS NOT NULL
        ''', (now, now + datetime.timedelta(hours=1)))
        for task in cur.fetchall():
            send_whatsapp_notification(task['phone_number'], f"⚠️ *Final Countdown*\n\nHey {task['user_name']}, the clock is ticking! *'{task['title']}'* is due in just *1 hour*!")
            cur.execute('UPDATE tasks SET notified_1h = true WHERE id = %s', (task['id'],))

        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Notification Error: {e}")

def send_whatsapp_notification(to_number, body):
    if not twilio_client: return
    try:
        # Prepend whatsapp: if not present and ensure country code
        formatted_to = to_number if to_number.startswith('whatsapp:') else f'whatsapp:{to_number}'
        twilio_client.messages.create(from_=TWILIO_WHATSAPP_NUMBER, body=body, to=formatted_to)
        print(f"Sent WhatsApp notification to {formatted_to}")
    except Exception as e:
        print(f"Twilio Error: {e}")

if __name__ == '__main__':
    port = int(os.getenv('PORT', 3000))
    app.run(host='0.0.0.0', port=port, debug=True, use_reloader=False)
