"""
ZK SMS Enterprise Backend - Complete Production Server (Flask)
Single file implementation with ALL features
"""
import os
import sys
import io
import csv
import secrets
import logging
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import requests
from supabase import create_client, Client

# ==================== CONFIGURATION ====================
SUPABASE_URL = os.getenv('SUPABASE_URL', 'https://xwfdjxqrwkimugsxkvnj.supabase.co')
SUPABASE_KEY = os.getenv('SUPABASE_KEY', 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inh3ZmRqeHFyd2tpbXVnc3hrdm5qIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4MDA2MzMxOSwiZXhwIjoyMDk1NjM5MzE5fQ.CGAzuO6x6hhzaQNHz29NBlJJyvtc8laD5cDLRjfr6nM')
SMS_API_URL = os.getenv('SMS_API_URL', 'http://147.135.212.197/crapi/s1t/viewstats')
SMS_API_TOKEN = os.getenv('SMS_API_TOKEN', 'R1BTQzRSQl93cpl-QW2USn-UUkSCcW9mg4x4WlV0bYBrkHd0clSY')
PORT = int(os.getenv('PORT', 5000))

# ==================== INITIALIZE ====================
app = Flask(__name__)
CORS(app)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

supabase: Client = None
try:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    logger.info("✅ Supabase client initialized")
except Exception as e:
    logger.error(f"❌ Supabase init failed: {e}")

@app.before_request
def log_request():
    logger.info(f"[{datetime.now().isoformat()}] {request.method} {request.path}")

# ==================== AUTH MIDDLEWARE ====================
def authenticate(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        api_key = request.headers.get('x-api-key') or request.args.get('api_key')
        if not api_key:
            return jsonify({'success': False, 'message': 'API key required. Use x-api-key header or api_key query parameter.'}), 401
        
        try:
            response = supabase.table('managers').select('*').eq('api_key', api_key).eq('status', 'active').single().execute()
            user = response.data
            if user:
                request.user = user
                request.user_type = 'manager'
                return f(*args, **kwargs)
        except:
            pass
        
        try:
            response = supabase.table('clients').select('*').eq('api_key', api_key).eq('status', 'active').single().execute()
            user = response.data
            if user:
                request.user = user
                request.user_type = 'client'
                return f(*args, **kwargs)
        except:
            pass
        
        return jsonify({'success': False, 'message': 'Invalid or inactive API key'}), 401
    return decorated

def require_admin(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not hasattr(request, 'user') or request.user.get('role') != 'admin':
            return jsonify({'success': False, 'message': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated

# ==================== HELPER FUNCTIONS ====================
def calculate_sms_stats(messages):
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
        date_str = msg.get('dt') or msg.get('received_at')
        if not date_str: continue
        try:
            msg_date = datetime.fromisoformat(str(date_str).replace('Z', '+00:00'))
            if msg_date.tzinfo: msg_date = msg_date.replace(tzinfo=None)
        except: continue
        
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
    stats['topClients'] = [{'client': c, 'count': n} for c, n in sorted(stats['byClient'].items(), key=lambda x: x[1], reverse=True)[:10]]
    return stats

def get_top_numbers(messages, limit=10):
    number_stats = {}
    for msg in messages:
        number = msg.get('num') or msg.get('phone_number')
        if not number: continue
        if number not in number_stats: number_stats[number] = {'count': 0, 'payout': 0}
        number_stats[number]['count'] += 1
        number_stats[number]['payout'] += float(msg.get('payout', 0) or 0)
    return [{'number': n, 'messages': d['count'], 'totalPayout': round(d['payout'], 4)} 
            for n, d in sorted(number_stats.items(), key=lambda x: x[1]['count'], reverse=True)[:limit]]

def get_top_clients(messages, limit=10):
    client_stats = {}
    for msg in messages:
        client = msg.get('cli') or msg.get('client_name') or 'Unknown'
        client_stats[client] = client_stats.get(client, 0) + 1
    return [{'client': c, 'count': n} for c, n in sorted(client_stats.items(), key=lambda x: x[1], reverse=True)[:limit]]

def get_hourly_breakdown(messages):
    hours = {}
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    for msg in messages:
        date_str = msg.get('dt') or msg.get('received_at')
        if not date_str: continue
        try:
            msg_date = datetime.fromisoformat(str(date_str).replace('Z', '+00:00'))
            if msg_date.tzinfo: msg_date = msg_date.replace(tzinfo=None)
        except: continue
        if msg_date >= today:
            hour = msg_date.hour
            hours[hour] = hours.get(hour, 0) + 1
    return [{'hour': f'{h}:00', 'count': c} for h, c in sorted(hours.items())]

def sync_messages_to_db(messages):
    synced = 0
    for msg in messages:
        try:
            supabase.table('sms_messages').upsert({
                'phone_number': msg['num'], 'client_name': msg['cli'],
                'message': msg['message'], 'payout': float(msg.get('payout', 0) or 0),
                'status': 'received', 'received_at': msg['dt']
            }, on_conflict='phone_number,received_at').execute()
            synced += 1
        except Exception as e:
            logger.error(f"Sync error: {e}")
    return synced

# ==================== HEALTH CHECK ====================
@app.route('/')
def home():
    return jsonify({'success': True, 'name': 'ZK SMS Enterprise API', 'version': '4.0.0', 'timestamp': datetime.now().isoformat()})

@app.route('/health')
def health():
    try:
        start_time = datetime.now()
        db_status = 'unknown'
        if supabase:
            try:
                response = supabase.table('managers').select('id').limit(1).execute()
                db_status = 'connected' if response.data else 'empty'
            except Exception as e: db_status = f'error: {str(e)}'
        
        sms_status = 'unknown'
        try:
            resp = requests.get(SMS_API_URL, params={'token': SMS_API_TOKEN}, timeout=5)
            sms_status = 'online' if resp.status_code == 200 else 'degraded'
        except Exception as e: sms_status = f'offline: {str(e)}'
        
        response_time = (datetime.now() - start_time).total_seconds() * 1000
        return jsonify({
            'success': True, 'status': 'healthy' if db_status == 'connected' else 'degraded',
            'services': {'database': db_status, 'sms_api': sms_status},
            'responseTime': f'{response_time:.0f}ms', 'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ==================== AUTH ROUTES ====================
@app.route('/api/auth/login', methods=['POST'])
def manager_login():
    try:
        data = request.get_json()
        username = (data.get('username') or '').strip()
        password = (data.get('password') or '').strip()
        if not username or not password:
            return jsonify({'success': False, 'message': 'Username and password are required'}), 400
        
        response = supabase.table('managers').select('*').eq('username', username).eq('password', password).eq('status', 'active').single().execute()
        user = response.data
        if not user:
            return jsonify({'success': False, 'message': 'Invalid credentials or account inactive'}), 401
        
        if not user.get('api_key'):
            api_key = secrets.token_hex(32)
            supabase.table('managers').update({'api_key': api_key}).eq('id', user['id']).execute()
            user['api_key'] = api_key
        
        supabase.table('managers').update({'last_login': datetime.now().isoformat()}).eq('id', user['id']).execute()
        logger.info(f"✅ Manager logged in: {username}")
        
        return jsonify({
            'success': True, 'message': 'Login successful',
            'data': {'user': {
                'id': user['id'], 'username': user['username'], 'email': user.get('email', ''),
                'phone': user.get('phone', ''), 'country': user.get('country', ''),
                'role': user.get('role', 'manager'), 'status': user.get('status', 'active'),
                'permissions': user.get('permissions', []), 'api_key': user['api_key'],
                'last_login': user.get('last_login')
            }}
        })
    except Exception as e:
        logger.error(f"Login error: {e}")
        return jsonify({'success': False, 'message': 'Login failed'}), 500

@app.route('/api/auth/me')
@authenticate
def me():
    return jsonify({'success': True, 'data': {'user': request.user, 'type': getattr(request, 'user_type', 'unknown')}})

# ==================== CLIENT AUTH ROUTES ====================
@app.route('/api/client/login', methods=['POST'])
def client_login():
    try:
        data = request.get_json()
        email = (data.get('email') or '').strip()
        password = (data.get('password') or '').strip()
        if not email or not password:
            return jsonify({'success': False, 'message': 'Email and password are required'}), 400
        
        response = supabase.table('clients').select('*').eq('email', email).eq('password', password).eq('status', 'active').single().execute()
        client = response.data
        if not client:
            return jsonify({'success': False, 'message': 'Invalid credentials or account inactive'}), 401
        
        if not client.get('api_key'):
            api_key = secrets.token_hex(32)
            supabase.table('clients').update({'api_key': api_key}).eq('id', client['id']).execute()
            client['api_key'] = api_key
        
        supabase.table('clients').update({'last_login': datetime.now().isoformat()}).eq('id', client['id']).execute()
        logger.info(f"✅ Client logged in: {email}")
        
        return jsonify({
            'success': True, 'message': 'Login successful',
            'data': {'client': {
                'id': client['id'], 'name': client['name'], 'email': client.get('email', ''),
                'phone': client.get('phone', ''), 'country': client.get('country', ''),
                'balance': float(client.get('balance', 0)), 'status': client.get('status', 'active'),
                'api_key': client['api_key'], 'last_login': client.get('last_login')
            }}
        })
    except Exception as e:
        logger.error(f"Client login error: {e}")
        return jsonify({'success': False, 'message': 'Login failed'}), 500

@app.route('/api/client/register', methods=['POST'])
def client_register():
    try:
        data = request.get_json()
        name = (data.get('name') or '').strip()
        email = (data.get('email') or '').strip()
        password = (data.get('password') or '').strip()
        if not name or not email or not password:
            return jsonify({'success': False, 'message': 'Name, email, and password are required'}), 400
        
        existing = supabase.table('clients').select('id').eq('email', email).execute()
        if existing.data:
            return jsonify({'success': False, 'message': 'Email already registered'}), 400
        
        api_key = secrets.token_hex(32)
        new_client = {
            'name': name, 'email': email, 'password': password,
            'phone': data.get('phone', ''), 'country': data.get('country', ''),
            'balance': 0, 'status': 'active', 'api_key': api_key,
            'created_at': datetime.now().isoformat(), 'updated_at': datetime.now().isoformat()
        }
        response = supabase.table('clients').insert(new_client).execute()
        client = response.data[0] if response.data else {}
        logger.info(f"✅ New client registered: {email}")
        
        return jsonify({
            'success': True, 'message': 'Registration successful',
            'data': {'client': {'id': client['id'], 'name': client['name'], 'email': client['email'],
                     'phone': client.get('phone', ''), 'country': client.get('country', ''), 'api_key': api_key}}
        }), 201
    except Exception as e:
        logger.error(f"Client registration error: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

# ==================== SMS MESSAGES ROUTES ====================
@app.route('/api/sms/messages')
@authenticate
def sms_messages():
    try:
        limit = int(request.args.get('limit', 100))
        offset = int(request.args.get('offset', 0))
        number = request.args.get('number')
        client = request.args.get('client')
        refresh = request.args.get('refresh', 'false').lower() == 'true'
        
        if refresh:
            try:
                api_response = requests.get(SMS_API_URL, params={'token': SMS_API_TOKEN}, timeout=10)
                if api_response.json().get('data'):
                    sync_messages_to_db(api_response.json()['data'])
            except Exception as e: logger.error(f"Sync error: {e}")
        
        query = supabase.table('sms_messages').select('*', count='exact').order('received_at', desc=True).range(offset, offset + limit - 1)
        if number: query = query.eq('phone_number', number)
        if client: query = query.eq('client_name', client)
        response = query.execute()
        
        return jsonify({
            'success': True, 'data': response.data or [],
            'pagination': {'total': response.count or 0, 'limit': limit, 'offset': offset,
                          'hasMore': (offset + limit) < (response.count or 0)}
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/sms/messages/live')
@authenticate
def sms_messages_live():
    try:
        response = requests.get(SMS_API_URL, params={'token': SMS_API_TOKEN}, timeout=10)
        messages = response.json().get('data', [])
        return jsonify({'success': True, 'source': 'api', 'data': messages, 'total': len(messages)})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/sms/stats')
@authenticate
def sms_stats():
    try:
        thirty_days_ago = (datetime.now() - timedelta(days=30)).isoformat()
        db_response = supabase.table('sms_messages').select('*').gte('received_at', thirty_days_ago).order('received_at', desc=True).execute()
        db_messages = db_response.data or []
        
        api_messages = []
        try:
            api_response = requests.get(SMS_API_URL, params={'token': SMS_API_TOKEN}, timeout=5)
            api_messages = api_response.json().get('data', [])
        except: pass
        
        all_messages = db_messages + api_messages
        stats = calculate_sms_stats(all_messages)
        top_numbers = get_top_numbers(all_messages, 10)
        top_clients = get_top_clients(all_messages, 5)
        hourly_breakdown = get_hourly_breakdown(all_messages)
        
        return jsonify({
            'success': True,
            'data': {'overview': stats, 'topNumbers': top_numbers, 'topClients': top_clients,
                     'hourlyBreakdown': hourly_breakdown, 'lastUpdated': datetime.now().isoformat(),
                     'sources': {'database': len(db_messages), 'liveApi': len(api_messages)}}
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/sms/sync', methods=['POST'])
@authenticate
def sms_sync():
    try:
        response = requests.get(SMS_API_URL, params={'token': SMS_API_TOKEN}, timeout=10)
        messages = response.json().get('data', [])
        if not messages:
            return jsonify({'success': True, 'message': 'No messages to sync', 'synced': 0})
        
        synced = sync_messages_to_db(messages)
        try:
            supabase.table('sms_logs').insert({
                'to_number': 'SYSTEM', 'message': f'Synced {synced} messages from external API',
                'status': 'synced', 'manager_id': request.user['id'], 'created_at': datetime.now().isoformat()
            }).execute()
        except: pass
        
        return jsonify({'success': True, 'message': f'Synced {synced} messages', 'synced': synced, 'total': len(messages)})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# ==================== DASHBOARD ====================
@app.route('/api/dashboard/stats')
@authenticate
def dashboard_stats():
    try:
        managers_count = supabase.table('managers').select('id', count='exact').execute().count or 0
        clients_count = supabase.table('clients').select('id', count='exact').execute().count or 0
        active_clients_count = supabase.table('clients').select('id', count='exact').eq('status', 'active').execute().count or 0
        sms_ranges_resp = supabase.table('sms_ranges').select('*').execute()
        rate_cards_resp = supabase.table('rate_cards').select('*').order('price').execute()
        recent_messages_resp = supabase.table('sms_messages').select('*').order('received_at', desc=True).limit(50).execute()
        
        sms_stats = calculate_sms_stats(recent_messages_resp.data or [])
        active_ranges = len([r for r in (sms_ranges_resp.data or []) if r.get('status') == 'active'])
        
        dashboard = {
            'overview': {'totalManagers': managers_count, 'totalClients': clients_count,
                        'activeClients': active_clients_count, 'activeSMSRanges': active_ranges,
                        'totalRateCards': len(rate_cards_resp.data or [])},
            'sms': {'today': sms_stats['today'], 'week': sms_stats['week'], 'month': sms_stats['month'],
                   'allTime': sms_stats['total'], 'topClients': sms_stats['topClients'],
                   'recentMessages': (recent_messages_resp.data or [])[:10]},
            'lastUpdated': datetime.now().isoformat()
        }
        return jsonify({'success': True, 'data': dashboard})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# ==================== MANAGERS CRUD ====================
@app.route('/api/managers')
@authenticate
def get_managers():
    try:
        status = request.args.get('status'); role = request.args.get('role')
        query = supabase.table('managers').select('*', count='exact').order('created_at', desc=True)
        if status: query = query.eq('status', status)
        if role: query = query.eq('role', role)
        response = query.execute()
        safe_data = [{k: v for k, v in m.items() if k != 'password'} for m in (response.data or [])]
        return jsonify({'success': True, 'data': safe_data, 'total': response.count or 0})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/managers', methods=['POST'])
@authenticate
@require_admin
def create_manager():
    try:
        data = request.get_json()
        username = (data.get('username') or '').strip()
        password = (data.get('password') or '').strip()
        email = (data.get('email') or '').strip()
        if not username or not password or not email:
            return jsonify({'success': False, 'message': 'Username, password, and email are required'}), 400
        
        existing = supabase.table('managers').select('id').or_(f'username.eq.{username},email.eq.{email}').limit(1).execute()
        if existing.data:
            return jsonify({'success': False, 'message': 'Username or email already exists'}), 400
        
        new_manager = {
            'username': username, 'password': password, 'email': email,
            'phone': data.get('phone', ''), 'country': data.get('country', ''),
            'role': data.get('role', 'manager'), 'status': 'active',
            'permissions': data.get('permissions', ['view_reports']),
            'created_at': datetime.now().isoformat(), 'updated_at': datetime.now().isoformat()
        }
        response = supabase.table('managers').insert(new_manager).execute()
        created = response.data[0] if response.data else {}
        safe_data = {k: v for k, v in created.items() if k != 'password'}
        return jsonify({'success': True, 'message': 'Manager created', 'data': safe_data}), 201
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/managers/<manager_id>', methods=['PUT'])
@authenticate
@require_admin
def update_manager(manager_id):
    try:
        data = request.get_json()
        data.pop('password', None)
        data['updated_at'] = datetime.now().isoformat()
        response = supabase.table('managers').update(data).eq('id', manager_id).execute()
        return jsonify({'success': True, 'message': 'Manager updated', 'data': response.data[0] if response.data else {}})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/managers/<manager_id>/status', methods=['PATCH'])
@authenticate
@require_admin
def toggle_manager_status(manager_id):
    try:
        data = request.get_json()
        status = data.get('status')
        if status not in ['active', 'inactive', 'suspended']:
            return jsonify({'success': False, 'message': 'Valid status required'}), 400
        supabase.table('managers').update({'status': status, 'updated_at': datetime.now().isoformat()}).eq('id', manager_id).execute()
        return jsonify({'success': True, 'message': f"Manager {'activated' if status == 'active' else status}"})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/managers/<manager_id>', methods=['DELETE'])
@authenticate
@require_admin
def delete_manager(manager_id):
    try:
        supabase.table('managers').delete().eq('id', manager_id).execute()
        return jsonify({'success': True, 'message': 'Manager deleted'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# ==================== CLIENTS CRUD ====================
@app.route('/api/clients')
@authenticate
def get_clients():
    try:
        status = request.args.get('status')
        query = supabase.table('clients').select('*', count='exact').order('created_at', desc=True)
        if status: query = query.eq('status', status)
        response = query.execute()
        return jsonify({'success': True, 'data': response.data or [], 'total': response.count or 0})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/clients', methods=['POST'])
@authenticate
def create_client():
    try:
        data = request.get_json()
        name = (data.get('name') or '').strip()
        if not name:
            return jsonify({'success': False, 'message': 'Client name is required'}), 400
        
        new_client = {
            'name': name, 'email': data.get('email', ''), 'password': data.get('password', secrets.token_hex(8)),
            'phone': data.get('phone', ''), 'country': data.get('country', ''),
            'balance': float(data.get('balance', 0) or 0), 'status': 'active',
            'manager_id': request.user['id'],
            'created_at': datetime.now().isoformat(), 'updated_at': datetime.now().isoformat()
        }
        response = supabase.table('clients').insert(new_client).execute()
        return jsonify({'success': True, 'message': 'Client created', 'data': response.data[0] if response.data else {}}), 201
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/clients/<client_id>', methods=['PUT'])
@authenticate
def update_client(client_id):
    try:
        data = request.get_json()
        updates = {'updated_at': datetime.now().isoformat()}
        for field in ['name', 'email', 'phone', 'country', 'balance', 'status']:
            if field in data:
                updates[field] = float(data[field]) if field == 'balance' else data[field]
        response = supabase.table('clients').update(updates).eq('id', client_id).execute()
        return jsonify({'success': True, 'message': 'Client updated', 'data': response.data[0] if response.data else {}})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/clients/<client_id>', methods=['DELETE'])
@authenticate
@require_admin
def delete_client(client_id):
    try:
        supabase.table('clients').delete().eq('id', client_id).execute()
        return jsonify({'success': True, 'message': 'Client deleted'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/clients/<client_id>/status', methods=['PATCH'])
@authenticate
def toggle_client_status(client_id):
    try:
        data = request.get_json()
        status = data.get('status')
        if status not in ['active', 'inactive']:
            return jsonify({'success': False, 'message': 'Valid status required'}), 400
        supabase.table('clients').update({'status': status, 'updated_at': datetime.now().isoformat()}).eq('id', client_id).execute()
        return jsonify({'success': True, 'message': f'Client {status}'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# ==================== SMS RANGES CRUD ====================
@app.route('/api/ranges')
@authenticate
def get_ranges():
    try:
        response = supabase.table('sms_ranges').select('*').order('country').execute()
        ranges = response.data or []
        for r in ranges:
            allocated = supabase.table('number_allocations').select('id', count='exact').eq('range_id', r['id']).eq('status', 'active').execute()
            r['allocated_count'] = allocated.count or 0
            r['available_count'] = (r.get('total_numbers', 0) or 0) - (allocated.count or 0)
        return jsonify({'success': True, 'data': ranges})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/ranges', methods=['POST'])
@authenticate
def create_range():
    try:
        data = request.get_json()
        new_range = {
            'country': data['country'], 'start_number': data['start_number'],
            'end_number': data['end_number'], 'total_numbers': data.get('total_numbers', 0),
            'allocated_numbers': 0, 'status': 'active',
            'manager_id': request.user['id'], 'created_at': datetime.now().isoformat()
        }
        response = supabase.table('sms_ranges').insert(new_range).execute()
        return jsonify({'success': True, 'message': 'Range created', 'data': response.data[0] if response.data else {}}), 201
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/ranges/<range_id>', methods=['PUT'])
@authenticate
def update_range(range_id):
    try:
        data = request.get_json()
        response = supabase.table('sms_ranges').update(data).eq('id', range_id).execute()
        return jsonify({'success': True, 'message': 'Range updated', 'data': response.data[0] if response.data else {}})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/ranges/<range_id>', methods=['DELETE'])
@authenticate
@require_admin
def delete_range(range_id):
    try:
        allocated = supabase.table('number_allocations').select('id', count='exact').eq('range_id', range_id).eq('status', 'active').execute()
        if allocated.count and allocated.count > 0:
            return jsonify({'success': False, 'message': f'Cannot delete range with {allocated.count} active allocations'}), 400
        supabase.table('sms_ranges').delete().eq('id', range_id).execute()
        return jsonify({'success': True, 'message': 'Range deleted'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/ranges/<range_id>/status', methods=['PATCH'])
@authenticate
def toggle_range_status(range_id):
    try:
        data = request.get_json()
        status = data.get('status')
        if status not in ['active', 'inactive']:
            return jsonify({'success': False, 'message': 'Valid status required'}), 400
        supabase.table('sms_ranges').update({'status': status}).eq('id', range_id).execute()
        return jsonify({'success': True, 'message': f'Range {status}'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/sms-ranges')
@authenticate
def get_sms_ranges():
    try:
        response = supabase.table('sms_ranges').select('*').order('created_at', desc=True).execute()
        return jsonify({'success': True, 'data': response.data or []})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# ==================== NUMBER ALLOCATION ====================
@app.route('/api/numbers/available')
@authenticate
def get_available_numbers():
    try:
        range_id = request.args.get('range_id')
        limit = int(request.args.get('limit', 20))
        if not range_id:
            return jsonify({'success': False, 'message': 'range_id required'}), 400
        
        range_resp = supabase.table('sms_ranges').select('*').eq('id', range_id).single().execute()
        sms_range = range_resp.data
        if not sms_range:
            return jsonify({'success': False, 'message': 'Range not found'}), 404
        
        allocated_resp = supabase.table('number_allocations').select('phone_number').eq('range_id', range_id).eq('status', 'active').execute()
        allocated_set = set(a['phone_number'] for a in (allocated_resp.data or []))
        
        start = int(sms_range['start_number']); end = int(sms_range['end_number'])
        available = []
        for num in range(start, end + 1):
            num_str = str(num)
            if num_str not in allocated_set:
                available.append({'phone_number': num_str, 'country': sms_range['country'], 'range_id': sms_range['id']})
                if len(available) >= limit: break
        
        return jsonify({'success': True, 'data': available, 'total_available': (end - start + 1) - len(allocated_set)})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/numbers/allocate', methods=['POST'])
@authenticate
def allocate_number():
    try:
        data = request.get_json()
        phone_number = data.get('phone_number'); range_id = data.get('range_id')
        client_id = data.get('client_id')
        
        if not phone_number or not range_id:
            return jsonify({'success': False, 'message': 'phone_number and range_id required'}), 400
        
        if not client_id and getattr(request, 'user_type', '') == 'client':
            client_id = request.user.get('id')
        
        existing = supabase.table('number_allocations').select('id').eq('phone_number', phone_number).eq('status', 'active').execute()
        if existing.data:
            return jsonify({'success': False, 'message': 'Number already allocated'}), 400
        
        allocation = {
            'phone_number': phone_number, 'range_id': range_id,
            'client_id': client_id, 'manager_id': request.user['id'],
            'status': 'active', 'allocated_at': datetime.now().isoformat()
        }
        response = supabase.table('number_allocations').insert(allocation).execute()
        return jsonify({'success': True, 'message': f'Number {phone_number} allocated', 'data': response.data[0] if response.data else {}}), 201
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/numbers/allocate/bulk', methods=['POST'])
@authenticate
def allocate_numbers_bulk():
    try:
        data = request.get_json()
        range_id = data.get('range_id'); client_id = data.get('client_id')
        count = int(data.get('count', 1))
        
        if not range_id or count < 1:
            return jsonify({'success': False, 'message': 'range_id and count required'}), 400
        
        if not client_id and getattr(request, 'user_type', '') == 'client':
            client_id = request.user.get('id')
        
        range_resp = supabase.table('sms_ranges').select('*').eq('id', range_id).single().execute()
        sms_range = range_resp.data
        if not sms_range:
            return jsonify({'success': False, 'message': 'Range not found'}), 404
        
        allocated_resp = supabase.table('number_allocations').select('phone_number').eq('range_id', range_id).eq('status', 'active').execute()
        allocated_set = set(a['phone_number'] for a in (allocated_resp.data or []))
        
        start = int(sms_range['start_number']); end = int(sms_range['end_number'])
        allocated_list = []
        for num in range(start, end + 1):
            num_str = str(num)
            if num_str not in allocated_set:
                allocated_list.append({
                    'phone_number': num_str, 'range_id': range_id,
                    'client_id': client_id, 'manager_id': request.user['id'],
                    'status': 'active', 'allocated_at': datetime.now().isoformat()
                })
                allocated_set.add(num_str)
                if len(allocated_list) >= count: break
        
        if not allocated_list:
            return jsonify({'success': False, 'message': 'No available numbers in this range'}), 400
        
        response = supabase.table('number_allocations').insert(allocated_list).execute()
        return jsonify({'success': True, 'message': f'{len(allocated_list)} numbers allocated', 'data': response.data or []}), 201
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/numbers/my-numbers')
@authenticate
def get_my_numbers():
    try:
        if getattr(request, 'user_type', '') == 'client':
            response = supabase.table('number_allocations').select('*, sms_ranges(country)').eq('client_id', request.user['id']).order('allocated_at', desc=True).execute()
        else:
            response = supabase.table('number_allocations').select('*, sms_ranges(country)').order('allocated_at', desc=True).execute()
        return jsonify({'success': True, 'data': response.data or [], 'total': len(response.data or [])})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/numbers/release/<phone_number>', methods=['POST'])
@authenticate
def release_number(phone_number):
    try:
        supabase.table('number_allocations').update({'status': 'released', 'notes': 'Released by user'}).eq('phone_number', phone_number).execute()
        return jsonify({'success': True, 'message': f'Number {phone_number} released'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/numbers/release/bulk', methods=['POST'])
@authenticate
def release_numbers_bulk():
    try:
        data = request.get_json()
        phone_numbers = data.get('phone_numbers', [])
        if not phone_numbers:
            return jsonify({'success': False, 'message': 'phone_numbers list required'}), 400
        released = 0
        for number in phone_numbers:
            try:
                supabase.table('number_allocations').update({'status': 'released', 'notes': 'Bulk release'}).eq('phone_number', number).execute()
                released += 1
            except: pass
        return jsonify({'success': True, 'message': f'{released} numbers released', 'released': released})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/numbers/stats')
@authenticate
def number_stats():
    try:
        total = supabase.table('number_allocations').select('id', count='exact').execute()
        active = supabase.table('number_allocations').select('id', count='exact').eq('status', 'active').execute()
        ranges = supabase.table('sms_ranges').select('*').execute()
        stats = []
        for r in (ranges.data or []):
            allocated = supabase.table('number_allocations').select('id', count='exact').eq('range_id', r['id']).eq('status', 'active').execute()
            stats.append({
                'country': r['country'], 'total': r.get('total_numbers', 0),
                'allocated': allocated.count or 0,
                'available': (r.get('total_numbers', 0) or 0) - (allocated.count or 0)
            })
        return jsonify({'success': True, 'data': {'total_allocations': total.count or 0, 'active_allocations': active.count or 0, 'by_country': stats}})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# ==================== RATE CARDS ====================
@app.route('/api/rate-cards')
@authenticate
def get_rate_cards():
    try:
        response = supabase.table('rate_cards').select('*').order('price').execute()
        return jsonify({'success': True, 'data': response.data or []})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# ==================== TRANSACTIONS ====================
@app.route('/api/transactions')
@authenticate
def get_transactions():
    try:
        status = request.args.get('status'); tx_type = request.args.get('type')
        client_id = request.args.get('client_id'); limit = int(request.args.get('limit', 50))
        query = supabase.table('transactions').select('*', count='exact').order('created_at', desc=True).limit(limit)
        if status: query = query.eq('status', status)
        if tx_type: query = query.eq('type', tx_type)
        if client_id: query = query.eq('client_id', client_id)
        response = query.execute()
        return jsonify({'success': True, 'data': response.data or [], 'total': response.count or 0})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/transactions', methods=['POST'])
@authenticate
def create_transaction():
    try:
        data = request.get_json()
        transaction = {
            'type': data.get('type', 'payment'), 'description': data.get('description', ''),
            'amount': float(data.get('amount', 0) or 0), 'currency': data.get('currency', 'USD'),
            'status': data.get('status', 'pending'), 'client_id': data.get('client_id'),
            'manager_id': request.user['id'], 'due_date': data.get('due_date'),
            'created_at': datetime.now().isoformat()
        }
        response = supabase.table('transactions').insert(transaction).execute()
        return jsonify({'success': True, 'message': 'Transaction created', 'data': response.data[0] if response.data else {}}), 201
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/transactions/<transaction_id>', methods=['PATCH'])
@authenticate
def update_transaction(transaction_id):
    try:
        data = request.get_json()
        updates = {}
        if 'status' in data: updates['status'] = data['status']
        if data.get('status') == 'paid': updates['paid_at'] = datetime.now().isoformat()
        response = supabase.table('transactions').update(updates).eq('id', transaction_id).execute()
        return jsonify({'success': True, 'message': 'Transaction updated', 'data': response.data[0] if response.data else {}})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# ==================== SMS LOGS ====================
@app.route('/api/sms-logs')
@authenticate
def get_sms_logs():
    try:
        status = request.args.get('status'); limit = int(request.args.get('limit', 50))
        query = supabase.table('sms_logs').select('*').order('created_at', desc=True).limit(limit)
        if status: query = query.eq('status', status)
        response = query.execute()
        return jsonify({'success': True, 'data': response.data or []})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# ==================== SEARCH ====================
@app.route('/api/search')
@authenticate
def search():
    try:
        query = request.args.get('q', '').strip()
        if not query or len(query) < 2:
            return jsonify({'success': False, 'message': 'Search query too short'}), 400
        results = {'messages': [], 'numbers': [], 'clients': []}
        msg_resp = supabase.table('sms_messages').select('*').or_(f'phone_number.ilike.%{query}%,message.ilike.%{query}%,client_name.ilike.%{query}%').limit(20).execute()
        results['messages'] = msg_resp.data or []
        num_resp = supabase.table('number_allocations').select('*, sms_ranges(country)').ilike('phone_number', f'%{query}%').limit(20).execute()
        results['numbers'] = num_resp.data or []
        client_resp = supabase.table('clients').select('*').or_(f'name.ilike.%{query}%,email.ilike.%{query}%').limit(10).execute()
        results['clients'] = client_resp.data or []
        return jsonify({'success': True, 'data': results})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# ==================== EXPORT ====================
@app.route('/api/export/messages')
@authenticate
def export_messages():
    try:
        response = supabase.table('sms_messages').select('*').order('received_at', desc=True).limit(1000).execute()
        messages = response.data or []
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Phone Number', 'Client', 'Message', 'Payout', 'Status', 'Received At'])
        for msg in messages:
            writer.writerow([msg.get('phone_number', ''), msg.get('client_name', ''), msg.get('message', ''),
                           msg.get('payout', ''), msg.get('status', ''), msg.get('received_at', '')])
        return Response(output.getvalue(), mimetype='text/csv',
                       headers={'Content-Disposition': 'attachment;filename=sms_messages.csv'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# ==================== SYSTEM INFO ====================
@app.route('/api/system/info')
@authenticate
@require_admin
def system_info():
    try:
        info = {
            'version': '4.0.0', 'supabase_connected': supabase is not None,
            'sms_api_url': SMS_API_URL, 'endpoints': [
                '/api/auth/login', '/api/client/login', '/api/client/register',
                '/api/sms/messages', '/api/sms/messages/live', '/api/sms/stats', '/api/sms/sync',
                '/api/dashboard/stats', '/api/managers', '/api/clients',
                '/api/ranges', '/api/sms-ranges', '/api/numbers/available',
                '/api/numbers/allocate', '/api/numbers/allocate/bulk', '/api/numbers/my-numbers',
                '/api/numbers/release', '/api/numbers/release/bulk', '/api/numbers/stats',
                '/api/rate-cards', '/api/transactions', '/api/sms-logs',
                '/api/search', '/api/export/messages', '/api/system/info'
            ]
        }
        return jsonify({'success': True, 'data': info})
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
    print('🚀 ZK SMS Enterprise Backend Server (Flask) v4.0')
    print('=' * 60)
    print(f'📡 Port: {PORT}')
    print(f'🗄️  Supabase: {"✅ Connected" if supabase else "❌ Not configured"}')
    print(f'📨 SMS API: {SMS_API_URL}')
    print(f'🕒 Started at: {datetime.now().isoformat()}')
    print('=' * 60)
    app.run(host='0.0.0.0', port=PORT)
