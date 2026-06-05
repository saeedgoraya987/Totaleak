import os
import secrets
import logging
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
from supabase import create_client

# ==================== CONFIGURATION ====================
SUPABASE_URL = os.getenv('SUPABASE_URL', 'https://xwfdjxqrwkimugsxkvnj.supabase.co')
SUPABASE_KEY = os.getenv('SUPABASE_KEY', 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inh3ZmRqeHFyd2tpbXVnc3hrdm5qIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4MDA2MzMxOSwiZXhwIjoyMDk1NjM5MzE5fQ.CGAzuO6x6hhzaQNHz29NBlJJyvtc8laD5cDLRjfr6nM')
SMS_API_URL = os.getenv('SMS_API_URL', 'http://147.135.212.197/crapi/s1t/viewstats')
SMS_API_TOKEN = os.getenv('SMS_API_TOKEN', 'R1BTQzRSQl93cpl-QW2USn-UUkSCcW9mg4x4WlV0bYBrkHd0clSY')
PORT = int(os.getenv('PORT', 5000))
DEBUG = os.getenv('DEBUG', 'False').lower() == 'true'

# ==================== INITIALIZE ====================
app = Flask(__name__)
CORS(app)

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Supabase
try:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    logger.info("✅ Supabase client initialized")
except Exception as e:
    logger.error(f"❌ Supabase init failed: {e}")
    supabase = None

# ==================== AUTH DECORATOR ====================
def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        api_key = request.headers.get('x-api-key') or request.args.get('api_key')
        
        if not api_key:
            return jsonify({'success': False, 'message': 'API key required'}), 401
        
        try:
            response = supabase.table('managers').select('*').eq('api_key', api_key).eq('status', 'active').single().execute()
            user = response.data
            
            if not user:
                return jsonify({'success': False, 'message': 'Invalid API key'}), 401
            
            request.user = user
            return f(*args, **kwargs)
        except Exception as e:
            return jsonify({'success': False, 'message': str(e)}), 401
    
    return decorated

def require_admin(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not hasattr(request, 'user') or request.user.get('role') != 'admin':
            return jsonify({'success': False, 'message': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated

# ==================== HELPER FUNCTIONS ====================
def calculate_stats(messages):
    now = datetime.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = today - timedelta(days=7)
    month_ago = today - timedelta(days=30)
    
    stats = {
        'total': len(messages),
        'today': {'count': 0, 'payout': 0},
        'week': {'count': 0, 'payout': 0},
        'month': {'count': 0, 'payout': 0},
        'byClient': {},
        'topClients': []
    }
    
    for msg in messages:
        try:
            msg_date = datetime.fromisoformat(str(msg.get('dt') or msg.get('received_at')).replace('Z', '+00:00'))
            if msg_date.tzinfo:
                msg_date = msg_date.replace(tzinfo=None)
        except:
            continue
        
        payout = float(msg.get('payout', 0) or 0)
        client = msg.get('cli') or msg.get('client_name') or 'Unknown'
        
        if msg_date >= today:
            stats['today']['count'] += 1
            stats['today']['payout'] += payout
        if msg_date >= week_ago:
            stats['week']['count'] += 1
            stats['week']['payout'] += payout
        if msg_date >= month_ago:
            stats['month']['count'] += 1
            stats['month']['payout'] += payout
        
        stats['byClient'][client] = stats['byClient'].get(client, 0) + 1
    
    stats['today']['payout'] = round(stats['today']['payout'], 4)
    stats['week']['payout'] = round(stats['week']['payout'], 4)
    stats['month']['payout'] = round(stats['month']['payout'], 4)
    
    stats['topClients'] = sorted(stats['byClient'].items(), key=lambda x: x[1], reverse=True)[:10]
    stats['topClients'] = [{'client': c, 'count': n} for c, n in stats['topClients']]
    
    return stats

# ==================== ROUTES ====================

@app.route('/')
def home():
    return jsonify({
        'success': True,
        'name': 'ZK SMS Enterprise API',
        'version': '3.0.0',
        'timestamp': datetime.now().isoformat()
    })

@app.route('/health')
def health():
    try:
        db_status = 'unknown'
        if supabase:
            try:
                response = supabase.table('managers').select('id').limit(1).execute()
                db_status = 'connected' if response.data else 'empty'
            except Exception as e:
                db_status = f'error: {str(e)}'
        
        sms_status = 'unknown'
        try:
            resp = requests.get(SMS_API_URL, params={'token': SMS_API_TOKEN}, timeout=5)
            sms_status = 'online' if resp.json().get('status') == 'success' else 'degraded'
        except Exception as e:
            sms_status = f'offline: {str(e)}'
        
        return jsonify({
            'success': True,
            'status': 'healthy' if db_status == 'connected' else 'degraded',
            'services': {
                'database': db_status,
                'sms_api': sms_status
            },
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ==================== AUTH ROUTES ====================

@app.route('/api/auth/login', methods=['POST'])
def login():
    try:
        data = request.get_json()
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        
        if not username or not password:
            return jsonify({'success': False, 'message': 'Username and password required'}), 400
        
        response = supabase.table('managers').select('*').eq('username', username).eq('password', password).eq('status', 'active').single().execute()
        user = response.data
        
        if not user:
            return jsonify({'success': False, 'message': 'Invalid credentials'}), 401
        
        if not user.get('api_key'):
            api_key = secrets.token_hex(32)
            supabase.table('managers').update({'api_key': api_key}).eq('id', user['id']).execute()
            user['api_key'] = api_key
        
        supabase.table('managers').update({'last_login': datetime.now().isoformat()}).eq('id', user['id']).execute()
        
        logger.info(f"✅ User logged in: {username}")
        
        return jsonify({
            'success': True,
            'message': 'Login successful',
            'data': {
                'user': {
                    'id': user['id'],
                    'username': user['username'],
                    'email': user.get('email', ''),
                    'phone': user.get('phone', ''),
                    'country': user.get('country', ''),
                    'role': user.get('role', 'manager'),
                    'status': user.get('status', 'active'),
                    'permissions': user.get('permissions', []),
                    'api_key': user['api_key'],
                    'last_login': user.get('last_login')
                }
            }
        })
    except Exception as e:
        logger.error(f"Login error: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/auth/me')
@require_auth
def me():
    return jsonify({'success': True, 'data': {'user': request.user}})

# ==================== SMS ROUTES ====================

@app.route('/api/sms/live')
@require_auth
def sms_live():
    try:
        response = requests.get(SMS_API_URL, params={'token': SMS_API_TOKEN}, timeout=10)
        return jsonify({'success': True, 'source': 'api', 'data': response.json()})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/sms/messages')
@require_auth
def sms_messages():
    try:
        limit = int(request.args.get('limit', 100))
        offset = int(request.args.get('offset', 0))
        number = request.args.get('number')
        client = request.args.get('client')
        
        query = supabase.table('sms_messages').select('*', count='exact').order('received_at', desc=True).range(offset, offset + limit - 1)
        
        if number:
            query = query.eq('phone_number', number)
        if client:
            query = query.eq('client_name', client)
        
        response = query.execute()
        
        return jsonify({
            'success': True,
            'data': response.data or [],
            'pagination': {
                'total': response.count or 0,
                'limit': limit,
                'offset': offset,
                'hasMore': (offset + limit) < (response.count or 0)
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/sms/stats')
@require_auth
def sms_stats():
    try:
        api_messages = []
        try:
            resp = requests.get(SMS_API_URL, params={'token': SMS_API_TOKEN}, timeout=5)
            api_messages = resp.json().get('data', [])
        except:
            pass
        
        thirty_days_ago = (datetime.now() - timedelta(days=30)).isoformat()
        response = supabase.table('sms_messages').select('*').gte('received_at', thirty_days_ago).order('received_at', desc=True).limit(500).execute()
        db_messages = response.data or []
        
        all_messages = db_messages + api_messages
        stats = calculate_stats(all_messages)
        
        return jsonify({
            'success': True,
            'data': stats,
            'sources': {
                'database': len(db_messages),
                'liveApi': len(api_messages)
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/sms/sync', methods=['POST'])
@require_auth
@require_admin
def sms_sync():
    try:
        response = requests.get(SMS_API_URL, params={'token': SMS_API_TOKEN}, timeout=10)
        messages = response.json().get('data', [])
        
        if not messages:
            return jsonify({'success': True, 'message': 'No messages to sync', 'synced': 0})
        
        synced = 0
        for msg in messages:
            try:
                supabase.table('sms_messages').upsert({
                    'phone_number': msg['num'],
                    'client_name': msg['cli'],
                    'message': msg['message'],
                    'payout': float(msg.get('payout', 0) or 0),
                    'status': 'received',
                    'received_at': msg['dt']
                }, on_conflict='phone_number,received_at').execute()
                synced += 1
            except:
                pass
        
        return jsonify({
            'success': True,
            'message': f'Synced {synced} messages',
            'synced': synced,
            'total': len(messages)
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# ==================== DASHBOARD ====================

@app.route('/api/dashboard')
@require_auth
def dashboard():
    try:
        response = supabase.table('sms_messages').select('*').order('received_at', desc=True).limit(50).execute()
        messages = response.data or []
        stats = calculate_stats(messages)
        
        return jsonify({
            'success': True,
            'data': {
                'sms': stats,
                'recentMessages': messages[:10],
                'lastUpdated': datetime.now().isoformat()
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# ==================== MANAGERS ====================

@app.route('/api/managers')
@require_auth
def get_managers():
    try:
        response = supabase.table('managers').select('id, username, email, phone, country, role, status, permissions, created_at, last_login').order('created_at', desc=True).execute()
        return jsonify({'success': True, 'data': response.data or []})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/managers', methods=['POST'])
@require_auth
@require_admin
def create_manager():
    try:
        data = request.get_json()
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        email = data.get('email', '').strip()
        
        if not username or not password or not email:
            return jsonify({'success': False, 'message': 'Username, password, and email are required'}), 400
        
        response = supabase.table('managers').insert({
            'username': username,
            'password': password,
            'email': email,
            'phone': data.get('phone', ''),
            'country': data.get('country', ''),
            'role': data.get('role', 'manager'),
            'status': 'active',
            'permissions': ['view_reports'],
            'created_at': datetime.now().isoformat()
        }).execute()
        
        return jsonify({'success': True, 'message': 'Manager created', 'data': response.data[0] if response.data else {}}), 201
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/managers/<manager_id>/status', methods=['PATCH'])
@require_auth
@require_admin
def toggle_manager_status(manager_id):
    try:
        data = request.get_json()
        status = data.get('status')
        
        if status not in ['active', 'inactive']:
            return jsonify({'success': False, 'message': 'Invalid status'}), 400
        
        supabase.table('managers').update({
            'status': status,
            'updated_at': datetime.now().isoformat()
        }).eq('id', manager_id).execute()
        
        return jsonify({'success': True, 'message': f'Manager {status}'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/managers/<manager_id>', methods=['DELETE'])
@require_auth
@require_admin
def delete_manager(manager_id):
    try:
        supabase.table('managers').delete().eq('id', manager_id).execute()
        return jsonify({'success': True, 'message': 'Manager deleted'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# ==================== CLIENTS ====================

@app.route('/api/clients')
@require_auth
def get_clients():
    try:
        response = supabase.table('clients').select('*').order('created_at', desc=True).execute()
        return jsonify({'success': True, 'data': response.data or []})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/clients', methods=['POST'])
@require_auth
def create_client():
    try:
        data = request.get_json()
        name = data.get('name', '').strip()
        
        if not name:
            return jsonify({'success': False, 'message': 'Client name required'}), 400
        
        response = supabase.table('clients').insert({
            'name': name,
            'email': data.get('email', ''),
            'phone': data.get('phone', ''),
            'country': data.get('country', ''),
            'balance': 0,
            'status': 'active',
            'manager_id': request.user['id'],
            'created_at': datetime.now().isoformat()
        }).execute()
        
        return jsonify({'success': True, 'message': 'Client created', 'data': response.data[0] if response.data else {}}), 201
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# ==================== RATE CARDS ====================

@app.route('/api/rate-cards')
@require_auth
def get_rate_cards():
    try:
        response = supabase.table('rate_cards').select('*').order('price').execute()
        return jsonify({'success': True, 'data': response.data or []})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# ==================== SMS RANGES ====================

@app.route('/api/sms-ranges')
@require_auth
def get_sms_ranges():
    try:
        response = supabase.table('sms_ranges').select('*').order('created_at', desc=True).execute()
        return jsonify({'success': True, 'data': response.data or []})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# ==================== ERROR HANDLERS ====================

@app.errorhandler(404)
def not_found(e):
    return jsonify({'success': False, 'message': f'Route {request.method} {request.path} not found'}), 404

@app.errorhandler(500)
def server_error(e):
    return jsonify({'success': False, 'message': 'Internal server error'}), 500

# ==================== START SERVER ====================

if __name__ == '__main__':
    print('=' * 60)
    print('🚀 ZK SMS Enterprise Backend (Flask)')
    print('=' * 60)
    print(f'📡 Port: {PORT}')
    print(f'🗄️  Supabase: {"✅ Connected" if supabase else "❌ Not configured"}')
    print(f'📨 SMS API: {SMS_API_URL}')
    print(f'🕒 Started at: {datetime.now().isoformat()}')
    print('=' * 60)
    
    app.run(host='0.0.0.0', port=PORT, debug=DEBUG)
