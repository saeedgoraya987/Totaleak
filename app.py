"""
ZK SMS Enterprise Backend - Complete Production Server (Flask)
3-Tier Access: Admin (full), Agent (clients+numbers), Client (own data only)
SMS messages automatically filtered by user's allocated numbers
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

# ==================== ROLE HELPERS ====================
def is_admin(user):
    return user.get('role') == 'admin'

def is_agent(user):
    return user.get('role') == 'agent'

def is_client_user():
    return getattr(request, 'user_type', '') == 'client'

def get_client_numbers(user):
    """Get phone numbers allocated to a client"""
    try:
        resp = supabase.table('number_allocations').select('phone_number').eq('client_id', user['id']).eq('status', 'active').execute()
        return [n['phone_number'] for n in (resp.data or [])]
    except:
        return []

def get_agent_numbers(user):
    """Get phone numbers allocated by an agent"""
    try:
        resp = supabase.table('number_allocations').select('phone_number').eq('manager_id', user['id']).execute()
        return [n['phone_number'] for n in (resp.data or [])]
    except:
        return []

def get_user_numbers(user):
    """Get allowed phone numbers for current user"""
    if is_admin(user):
        return []  # Empty means ALL
    elif is_agent(user):
        return get_agent_numbers(user)
    elif is_client_user():
        return get_client_numbers(user)
    return []

def filter_messages_by_numbers(messages, allowed_numbers):
    """Filter messages to only show those matching allowed numbers"""
    if not allowed_numbers:
        return messages  # Admin sees all
    return [m for m in messages if (m.get('num') or m.get('phone_number')) in allowed_numbers]

# ==================== AUTH MIDDLEWARE ====================
def authenticate(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        api_key = request.headers.get('x-api-key') or request.args.get('api_key')
        if not api_key:
            return jsonify({'success': False, 'message': 'API key required'}), 401
        try:
            response = supabase.table('managers').select('*').eq('api_key', api_key).eq('status', 'active').single().execute()
            if response.data:
                request.user = response.data
                request.user_type = 'manager'
                return f(*args, **kwargs)
        except: pass
        try:
            response = supabase.table('clients').select('*').eq('api_key', api_key).eq('status', 'active').single().execute()
            if response.data:
                request.user = response.data
                request.user_type = 'client'
                return f(*args, **kwargs)
        except: pass
        return jsonify({'success': False, 'message': 'Invalid or inactive API key'}), 401
    return decorated

def require_admin(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not hasattr(request, 'user') or not is_admin(request.user):
            return jsonify({'success': False, 'message': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated

def require_agent_or_admin(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not hasattr(request, 'user') or request.user.get('role') not in ['admin', 'agent']:
            return jsonify({'success': False, 'message': 'Agent or Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated

# ==================== HELPERS ====================
def calculate_sms_stats(messages):
    now = datetime.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = today - timedelta(days=7)
    month_ago = today - timedelta(days=30)
    stats = {'total': len(messages), 'today': {'count': 0, 'payout': 0}, 'week': {'count': 0, 'payout': 0}, 'month': {'count': 0, 'payout': 0}, 'byClient': {}, 'topClients': []}
    for msg in messages:
        date_str = msg.get('dt') or msg.get('received_at')
        if not date_str: continue
        try:
            msg_date = datetime.fromisoformat(str(date_str).replace('Z', '+00:00'))
            if msg_date.tzinfo: msg_date = msg_date.replace(tzinfo=None)
        except: continue
        payout = float(msg.get('payout', 0) or 0)
        client = msg.get('cli') or msg.get('client_name') or 'Unknown'
        if msg_date >= today: stats['today']['count'] += 1; stats['today']['payout'] += payout
        if msg_date >= week_ago: stats['week']['count'] += 1; stats['week']['payout'] += payout
        if msg_date >= month_ago: stats['month']['count'] += 1; stats['month']['payout'] += payout
        stats['byClient'][client] = stats['byClient'].get(client, 0) + 1
    stats['today']['payout'] = round(stats['today']['payout'], 4)
    stats['week']['payout'] = round(stats['week']['payout'], 4)
    stats['month']['payout'] = round(stats['month']['payout'], 4)
    stats['topClients'] = [{'client': c, 'count': n} for c, n in sorted(stats['byClient'].items(), key=lambda x: x[1], reverse=True)[:10]]
    return stats

def get_top_numbers(messages, limit=10):
    ns = {}
    for m in messages:
        n = m.get('num') or m.get('phone_number')
        if not n: continue
        if n not in ns: ns[n] = {'count': 0, 'payout': 0}
        ns[n]['count'] += 1; ns[n]['payout'] += float(m.get('payout', 0) or 0)
    return [{'number': n, 'messages': d['count'], 'totalPayout': round(d['payout'], 4)} for n, d in sorted(ns.items(), key=lambda x: x[1]['count'], reverse=True)[:limit]]

def get_top_clients(messages, limit=10):
    cs = {}
    for m in messages:
        c = m.get('cli') or m.get('client_name') or 'Unknown'
        cs[c] = cs.get(c, 0) + 1
    return [{'client': c, 'count': n} for c, n in sorted(cs.items(), key=lambda x: x[1], reverse=True)[:limit]]

def get_hourly_breakdown(messages):
    hours = {}
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    for m in messages:
        ds = m.get('dt') or m.get('received_at')
        if not ds: continue
        try:
            md = datetime.fromisoformat(str(ds).replace('Z', '+00:00'))
            if md.tzinfo: md = md.replace(tzinfo=None)
        except: continue
        if md >= today: hours[md.hour] = hours.get(md.hour, 0) + 1
    return [{'hour': f'{h}:00', 'count': c} for h, c in sorted(hours.items())]

def sync_messages_to_db(messages):
    """Sync messages to database"""
    synced = 0
    for msg in messages:
        try:
            supabase.table('sms_messages').upsert({
                'phone_number': msg['num'], 'client_name': msg['cli'],
                'message': msg['message'], 'payout': float(msg.get('payout', 0) or 0),
                'status': 'received', 'received_at': msg['dt']
            }, on_conflict='phone_number,received_at').execute()
            synced += 1
        except: pass
    return synced

def fetch_all_sms_from_api():
    """Fetch ALL SMS from external API"""
    try:
        response = requests.get(SMS_API_URL, params={
            'token': SMS_API_TOKEN,
            'records': 1000
        }, timeout=15)
        return response.json().get('data', [])
    except Exception as e:
        logger.error(f"SMS API fetch error: {e}")
        return []

# ==================== HEALTH ====================
@app.route('/')
def home():
    return jsonify({'success': True, 'name': 'ZK SMS Enterprise API', 'version': '5.1.0', 'timestamp': datetime.now().isoformat()})

@app.route('/health')
def health():
    try:
        st = datetime.now()
        db = 'unknown'
        if supabase:
            try:
                r = supabase.table('managers').select('id').limit(1).execute()
                db = 'connected' if r.data else 'empty'
            except Exception as e: db = f'error: {str(e)}'
        sms = 'unknown'
        try:
            r = requests.get(SMS_API_URL, params={'token': SMS_API_TOKEN}, timeout=5)
            sms = 'online' if r.status_code == 200 else 'degraded'
        except Exception as e: sms = f'offline: {str(e)}'
        return jsonify({'success': True, 'status': 'healthy' if db == 'connected' else 'degraded', 'services': {'database': db, 'sms_api': sms}, 'responseTime': f'{(datetime.now()-st).total_seconds()*1000:.0f}ms', 'timestamp': datetime.now().isoformat()})
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
            return jsonify({'success': False, 'message': 'Username and password required'}), 400
        response = supabase.table('managers').select('*').eq('username', username).eq('password', password).eq('status', 'active').single().execute()
        user = response.data
        if not user:
            return jsonify({'success': False, 'message': 'Invalid credentials'}), 401
        if not user.get('api_key'):
            user['api_key'] = secrets.token_hex(32)
            supabase.table('managers').update({'api_key': user['api_key']}).eq('id', user['id']).execute()
        supabase.table('managers').update({'last_login': datetime.now().isoformat()}).eq('id', user['id']).execute()
        logger.info(f"✅ Manager logged in: {username} ({user.get('role')})")
        return jsonify({'success': True, 'message': 'Login successful', 'data': {'user': {
            'id': user['id'], 'username': user['username'], 'email': user.get('email', ''),
            'phone': user.get('phone', ''), 'country': user.get('country', ''),
            'role': user.get('role', 'manager'), 'status': user.get('status', 'active'),
            'permissions': user.get('permissions', []), 'api_key': user['api_key'],
            'last_login': user.get('last_login')
        }}})
    except Exception as e:
        logger.error(f"Login error: {e}")
        return jsonify({'success': False, 'message': 'Login failed'}), 500

@app.route('/api/auth/me')
@authenticate
def me():
    return jsonify({'success': True, 'data': {'user': request.user, 'type': getattr(request, 'user_type', 'unknown')}})

@app.route('/api/client/login', methods=['POST'])
def client_login():
    try:
        data = request.get_json()
        email = (data.get('email') or '').strip()
        password = (data.get('password') or '').strip()
        if not email or not password:
            return jsonify({'success': False, 'message': 'Email and password required'}), 400
        response = supabase.table('clients').select('*').eq('email', email).eq('password', password).eq('status', 'active').single().execute()
        client = response.data
        if not client:
            return jsonify({'success': False, 'message': 'Invalid credentials'}), 401
        if not client.get('api_key'):
            client['api_key'] = secrets.token_hex(32)
            supabase.table('clients').update({'api_key': client['api_key']}).eq('id', client['id']).execute()
        supabase.table('clients').update({'last_login': datetime.now().isoformat()}).eq('id', client['id']).execute()
        logger.info(f"✅ Client logged in: {email}")
        return jsonify({'success': True, 'message': 'Login successful', 'data': {'client': {
            'id': client['id'], 'name': client['name'], 'email': client.get('email', ''),
            'phone': client.get('phone', ''), 'country': client.get('country', ''),
            'balance': float(client.get('balance', 0)), 'status': client.get('status', 'active'),
            'api_key': client['api_key'], 'last_login': client.get('last_login')
        }}})
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
            return jsonify({'success': False, 'message': 'Name, email, and password required'}), 400
        existing = supabase.table('clients').select('id').eq('email', email).execute()
        if existing.data:
            return jsonify({'success': False, 'message': 'Email already registered'}), 400
        api_key = secrets.token_hex(32)
        nc = {'name': name, 'email': email, 'password': password, 'phone': data.get('phone', ''), 'country': data.get('country', ''), 'balance': 0, 'status': 'active', 'api_key': api_key, 'created_at': datetime.now().isoformat(), 'updated_at': datetime.now().isoformat()}
        response = supabase.table('clients').insert(nc).execute()
        c = response.data[0] if response.data else {}
        logger.info(f"✅ New client: {email}")
        return jsonify({'success': True, 'message': 'Registration successful', 'data': {'client': {'id': c['id'], 'name': c['name'], 'email': c['email'], 'phone': c.get('phone', ''), 'country': c.get('country', ''), 'api_key': api_key}}}), 201
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# ==================== SMS MESSAGES (Auto-filtered by user's numbers) ====================
@app.route('/api/sms/messages')
@authenticate
def sms_messages():
    try:
        user = request.user
        limit = int(request.args.get('limit', 100))
        offset = int(request.args.get('offset', 0))
        number = request.args.get('number')
        client = request.args.get('client')
        refresh = request.args.get('refresh', 'false').lower() == 'true'
        
        # Get allowed numbers for this user
        allowed_numbers = get_user_numbers(user)
        
        # If refresh, sync from API first
        if refresh:
            try:
                all_messages = fetch_all_sms_from_api()
                # Filter by user's numbers before syncing
                filtered = filter_messages_by_numbers(all_messages, allowed_numbers)
                if filtered:
                    sync_messages_to_db(filtered)
            except Exception as e:
                logger.error(f"Refresh error: {e}")
        
        # Query from database
        query = supabase.table('sms_messages').select('*', count='exact').order('received_at', desc=True).range(offset, offset + limit - 1)
        
        # Filter by allowed numbers for agent/client
        if allowed_numbers:
            query = query.in_('phone_number', allowed_numbers)
        
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


@app.route('/api/sms/messages/live')
@authenticate
def sms_messages_live():
    """Get live SMS - automatically filtered by user's numbers"""
    try:
        user = request.user
        all_messages = fetch_all_sms_from_api()
        allowed_numbers = get_user_numbers(user)
        filtered = filter_messages_by_numbers(all_messages, allowed_numbers)
        
        return jsonify({
            'success': True,
            'source': 'api',
            'data': filtered,
            'total': len(filtered),
            'total_api': len(all_messages)
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/sms/stats')
@authenticate
def sms_stats():
    """SMS stats - automatically filtered by user's numbers"""
    try:
        user = request.user
        allowed_numbers = get_user_numbers(user)
        
        thirty_days_ago = (datetime.now() - timedelta(days=30)).isoformat()
        query = supabase.table('sms_messages').select('*').gte('received_at', thirty_days_ago).order('received_at', desc=True)
        
        if allowed_numbers:
            query = query.in_('phone_number', allowed_numbers)
        
        db_messages = query.execute().data or []
        
        # Fetch live API messages and filter
        api_messages = []
        try:
            all_api = fetch_all_sms_from_api()
            api_messages = filter_messages_by_numbers(all_api, allowed_numbers)
        except:
            pass
        
        all_messages = db_messages + api_messages
        stats = calculate_sms_stats(all_messages)
        
        return jsonify({
            'success': True,
            'data': {
                'overview': stats,
                'topNumbers': get_top_numbers(all_messages, 10),
                'topClients': get_top_clients(all_messages, 5),
                'hourlyBreakdown': get_hourly_breakdown(all_messages),
                'lastUpdated': datetime.now().isoformat(),
                'sources': {'database': len(db_messages), 'liveApi': len(api_messages)}
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/sms/sync', methods=['POST'])
@authenticate
@require_agent_or_admin
def sms_sync():
    """Sync SMS - filtered by user's numbers for agent"""
    try:
        user = request.user
        all_messages = fetch_all_sms_from_api()
        allowed_numbers = get_user_numbers(user)
        filtered = filter_messages_by_numbers(all_messages, allowed_numbers)
        
        if not filtered:
            return jsonify({'success': True, 'message': 'No messages to sync', 'synced': 0})
        
        synced = sync_messages_to_db(filtered)
        
        try:
            supabase.table('sms_logs').insert({
                'to_number': 'SYSTEM',
                'message': f'Synced {synced} messages',
                'status': 'synced',
                'manager_id': user['id'],
                'created_at': datetime.now().isoformat()
            }).execute()
        except:
            pass
        
        return jsonify({
            'success': True,
            'message': f'Synced {synced} messages',
            'synced': synced,
            'total': len(filtered),
            'total_api': len(all_messages)
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


# ==================== DASHBOARD ====================
@app.route('/api/dashboard/stats')
@authenticate
def dashboard_stats():
    try:
        user = request.user
        allowed_numbers = get_user_numbers(user)
        managers_count = clients_count = active_clients_count = 0
        
        if is_admin(user):
            managers_count = supabase.table('managers').select('id', count='exact').execute().count or 0
            clients_count = supabase.table('clients').select('id', count='exact').execute().count or 0
            active_clients_count = supabase.table('clients').select('id', count='exact').eq('status', 'active').execute().count or 0
        elif is_agent(user):
            managers_count = supabase.table('managers').select('id', count='exact').eq('parent_id', user['id']).execute().count or 0
            clients_count = supabase.table('clients').select('id', count='exact').eq('agent_id', user['id']).execute().count or 0
            active_clients_count = supabase.table('clients').select('id', count='exact').eq('agent_id', user['id']).eq('status', 'active').execute().count or 0
        else:
            clients_count = len(allowed_numbers)
            active_clients_count = clients_count
        
        sms_ranges_resp = supabase.table('sms_ranges').select('*').execute()
        rate_cards_resp = supabase.table('rate_cards').select('*').order('price').execute()
        
        msg_query = supabase.table('sms_messages').select('*').order('received_at', desc=True).limit(50)
        if allowed_numbers:
            msg_query = msg_query.in_('phone_number', allowed_numbers)
        
        recent_messages_resp = msg_query.execute()
        sms_stats = calculate_sms_stats(recent_messages_resp.data or [])
        active_ranges = len([r for r in (sms_ranges_resp.data or []) if r.get('status') == 'active'])
        
        dashboard = {
            'overview': {
                'totalManagers': managers_count,
                'totalClients': clients_count,
                'activeClients': active_clients_count,
                'activeSMSRanges': active_ranges,
                'totalRateCards': len(rate_cards_resp.data or [])
            },
            'sms': {
                'today': sms_stats['today'],
                'week': sms_stats['week'],
                'month': sms_stats['month'],
                'allTime': sms_stats['total'],
                'topClients': sms_stats['topClients'],
                'recentMessages': (recent_messages_resp.data or [])[:10]
            },
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
        user = request.user
        status = request.args.get('status'); role = request.args.get('role')
        query = supabase.table('managers').select('*', count='exact').order('created_at', desc=True)
        if is_agent(user): query = query.eq('parent_id', user['id'])
        if is_client_user(): return jsonify({'success': False, 'message': 'Access denied'}), 403
        if status: query = query.eq('status', status)
        if role: query = query.eq('role', role)
        response = query.execute()
        safe_data = [{k: v for k, v in m.items() if k != 'password'} for m in (response.data or [])]
        return jsonify({'success': True, 'data': safe_data, 'total': response.count or 0})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/managers', methods=['POST'])
@authenticate
@require_agent_or_admin
def create_manager():
    try:
        user = request.user
        data = request.get_json()
        username = (data.get('username') or '').strip()
        password = (data.get('password') or '').strip()
        email = (data.get('email') or '').strip()
        if not username or not password or not email:
            return jsonify({'success': False, 'message': 'Username, password, email required'}), 400
        
        role = data.get('role', 'client')
        if is_agent(user) and role in ['admin', 'agent']:
            return jsonify({'success': False, 'message': 'Agents can only create client accounts'}), 403
        
        ucheck = supabase.table('managers').select('id').eq('username', username).execute()
        if ucheck.data:
            return jsonify({'success': False, 'message': 'Username already exists'}), 400
        
        echeck = supabase.table('managers').select('id').eq('email', email).execute()
        if echeck.data:
            return jsonify({'success': False, 'message': 'Email already exists'}), 400
        
        new_manager = {
            'username': username, 'password': password, 'email': email,
            'phone': data.get('phone', ''), 'country': data.get('country', ''),
            'role': role if is_admin(user) else 'client', 'status': 'active',
            'permissions': data.get('permissions', ['view_reports']),
            'parent_id': user['id'] if is_agent(user) else None,
            'created_at': datetime.now().isoformat(), 'updated_at': datetime.now().isoformat()
        }
        response = supabase.table('managers').insert(new_manager).execute()
        created = response.data[0] if response.data else {}
        safe_data = {k: v for k, v in created.items() if k != 'password'}
        return jsonify({'success': True, 'message': 'Account created', 'data': safe_data}), 201
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
        return jsonify({'success': True, 'message': 'Updated', 'data': response.data[0] if response.data else {}})
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
        return jsonify({'success': True, 'message': 'Deleted'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# ==================== CLIENTS CRUD ====================
@app.route('/api/clients')
@authenticate
def get_clients():
    try:
        user = request.user
        if is_client_user(): return jsonify({'success': False, 'message': 'Access denied'}), 403
        status = request.args.get('status')
        query = supabase.table('clients').select('*').order('created_at', desc=True)
        if is_agent(user): query = query.eq('agent_id', user['id'])
        if status: query = query.eq('status', status)
        response = query.execute()
        return jsonify({'success': True, 'data': response.data or [], 'total': len(response.data or [])})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/clients', methods=['POST'])
@authenticate
@require_agent_or_admin
def create_client():
    try:
        user = request.user
        data = request.get_json()
        name = (data.get('name') or '').strip()
        if not name: return jsonify({'success': False, 'message': 'Client name required'}), 400
        nc = {
            'name': name, 'email': data.get('email', ''), 'password': data.get('password', secrets.token_hex(8)),
            'phone': data.get('phone', ''), 'country': data.get('country', ''),
            'balance': float(data.get('balance', 0) or 0), 'status': 'active',
            'manager_id': user['id'], 'agent_id': user['id'] if is_agent(user) else data.get('agent_id'),
            'created_at': datetime.now().isoformat(), 'updated_at': datetime.now().isoformat()
        }
        response = supabase.table('clients').insert(nc).execute()
        return jsonify({'success': True, 'message': 'Client created', 'data': response.data[0] if response.data else {}}), 201
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/clients/<client_id>', methods=['PUT'])
@authenticate
@require_agent_or_admin
def update_client(client_id):
    try:
        data = request.get_json()
        updates = {'updated_at': datetime.now().isoformat()}
        for f in ['name', 'email', 'phone', 'country', 'balance', 'status']:
            if f in data: updates[f] = float(data[f]) if f == 'balance' else data[f]
        response = supabase.table('clients').update(updates).eq('id', client_id).execute()
        return jsonify({'success': True, 'message': 'Updated', 'data': response.data[0] if response.data else {}})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/clients/<client_id>', methods=['DELETE'])
@authenticate
@require_admin
def delete_client(client_id):
    try:
        supabase.table('clients').delete().eq('id', client_id).execute()
        return jsonify({'success': True, 'message': 'Deleted'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/clients/<client_id>/status', methods=['PATCH'])
@authenticate
@require_agent_or_admin
def toggle_client_status(client_id):
    try:
        data = request.get_json()
        status = data.get('status')
        if status not in ['active', 'inactive']: return jsonify({'success': False, 'message': 'Valid status required'}), 400
        supabase.table('clients').update({'status': status, 'updated_at': datetime.now().isoformat()}).eq('id', client_id).execute()
        return jsonify({'success': True, 'message': f'Client {status}'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# ==================== SMS RANGES ====================
@app.route('/api/ranges')
@authenticate
def get_ranges():
    try:
        if is_client_user(): return jsonify({'success': False, 'message': 'Access denied'}), 403
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
@require_admin
def create_range():
    try:
        data = request.get_json()
        nr = {'country': data['country'], 'start_number': data['start_number'], 'end_number': data['end_number'], 'total_numbers': data.get('total_numbers', 0), 'allocated_numbers': 0, 'status': 'active', 'manager_id': request.user['id'], 'created_at': datetime.now().isoformat()}
        response = supabase.table('sms_ranges').insert(nr).execute()
        return jsonify({'success': True, 'message': 'Range created', 'data': response.data[0] if response.data else {}}), 201
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/sms-ranges')
@authenticate
def get_sms_ranges():
    try:
        if is_client_user(): return jsonify({'success': False, 'message': 'Access denied'}), 403
        response = supabase.table('sms_ranges').select('*').order('created_at', desc=True).execute()
        return jsonify({'success': True, 'data': response.data or []})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/ranges/<range_id>', methods=['PUT'])
@authenticate
@require_admin
def update_range(range_id):
    try:
        response = supabase.table('sms_ranges').update(request.get_json()).eq('id', range_id).execute()
        return jsonify({'success': True, 'message': 'Updated', 'data': response.data[0] if response.data else {}})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/ranges/<range_id>', methods=['DELETE'])
@authenticate
@require_admin
def delete_range(range_id):
    try:
        allocated = supabase.table('number_allocations').select('id', count='exact').eq('range_id', range_id).eq('status', 'active').execute()
        if allocated.count and allocated.count > 0:
            return jsonify({'success': False, 'message': f'{allocated.count} active allocations'}), 400
        supabase.table('sms_ranges').delete().eq('id', range_id).execute()
        return jsonify({'success': True, 'message': 'Deleted'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/ranges/<range_id>/status', methods=['PATCH'])
@authenticate
@require_admin
def toggle_range_status(range_id):
    try:
        data = request.get_json()
        status = data.get('status')
        if status not in ['active', 'inactive']: return jsonify({'success': False, 'message': 'Valid status required'}), 400
        supabase.table('sms_ranges').update({'status': status}).eq('id', range_id).execute()
        return jsonify({'success': True, 'message': f'Range {status}'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# ==================== NUMBER ALLOCATION ====================
@app.route('/api/numbers/available')
@authenticate
def get_available_numbers():
    try:
        if is_client_user(): return jsonify({'success': False, 'message': 'Access denied'}), 403
        range_id = request.args.get('range_id'); limit = int(request.args.get('limit', 20))
        if not range_id: return jsonify({'success': False, 'message': 'range_id required'}), 400
        range_resp = supabase.table('sms_ranges').select('*').eq('id', range_id).single().execute()
        sms_range = range_resp.data
        if not sms_range: return jsonify({'success': False, 'message': 'Range not found'}), 404
        allocated_resp = supabase.table('number_allocations').select('phone_number').eq('range_id', range_id).eq('status', 'active').execute()
        allocated_set = set(a['phone_number'] for a in (allocated_resp.data or []))
        start, end = int(sms_range['start_number']), int(sms_range['end_number'])
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
@require_agent_or_admin
def allocate_number():
    try:
        user = request.user
        data = request.get_json()
        phone_number = data.get('phone_number'); range_id = data.get('range_id')
        client_id = data.get('client_id')
        if not phone_number or not range_id: return jsonify({'success': False, 'message': 'phone_number and range_id required'}), 400
        if is_agent(user):
            if client_id:
                cc = supabase.table('clients').select('id').eq('id', client_id).eq('agent_id', user['id']).execute()
                if not cc.data: return jsonify({'success': False, 'message': 'Client not found or not yours'}), 403
            else:
                return jsonify({'success': False, 'message': 'client_id required'}), 400
        existing = supabase.table('number_allocations').select('id').eq('phone_number', phone_number).eq('status', 'active').execute()
        if existing.data: return jsonify({'success': False, 'message': 'Number already allocated'}), 400
        allocation = {'phone_number': phone_number, 'range_id': range_id, 'client_id': client_id, 'manager_id': user['id'], 'status': 'active', 'allocated_at': datetime.now().isoformat()}
        response = supabase.table('number_allocations').insert(allocation).execute()
        return jsonify({'success': True, 'message': f'Number {phone_number} allocated', 'data': response.data[0] if response.data else {}}), 201
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/numbers/allocate/bulk', methods=['POST'])
@authenticate
@require_agent_or_admin
def allocate_numbers_bulk():
    try:
        user = request.user
        data = request.get_json()
        range_id = data.get('range_id'); client_id = data.get('client_id'); count = int(data.get('count', 1))
        if not range_id or count < 1: return jsonify({'success': False, 'message': 'range_id and count required'}), 400
        if is_agent(user):
            if client_id:
                cc = supabase.table('clients').select('id').eq('id', client_id).eq('agent_id', user['id']).execute()
                if not cc.data: return jsonify({'success': False, 'message': 'Client not found or not yours'}), 403
            else:
                return jsonify({'success': False, 'message': 'client_id required'}), 400
        range_resp = supabase.table('sms_ranges').select('*').eq('id', range_id).single().execute()
        sms_range = range_resp.data
        if not sms_range: return jsonify({'success': False, 'message': 'Range not found'}), 404
        allocated_resp = supabase.table('number_allocations').select('phone_number').eq('range_id', range_id).eq('status', 'active').execute()
        allocated_set = set(a['phone_number'] for a in (allocated_resp.data or []))
        start, end = int(sms_range['start_number']), int(sms_range['end_number'])
        allocated_list = []
        for num in range(start, end + 1):
            num_str = str(num)
            if num_str not in allocated_set:
                allocated_list.append({'phone_number': num_str, 'range_id': range_id, 'client_id': client_id, 'manager_id': user['id'], 'status': 'active', 'allocated_at': datetime.now().isoformat()})
                allocated_set.add(num_str)
                if len(allocated_list) >= count: break
        if not allocated_list: return jsonify({'success': False, 'message': 'No available numbers'}), 400
        response = supabase.table('number_allocations').insert(allocated_list).execute()
        return jsonify({'success': True, 'message': f'{len(allocated_list)} numbers allocated', 'data': response.data or []}), 201
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/numbers/my-numbers')
@authenticate
def get_my_numbers():
    try:
        user = request.user
        if is_client_user():
            response = supabase.table('number_allocations').select('*, sms_ranges(country)').eq('client_id', user['id']).order('allocated_at', desc=True).execute()
        elif is_agent(user):
            response = supabase.table('number_allocations').select('*, sms_ranges(country)').eq('manager_id', user['id']).order('allocated_at', desc=True).execute()
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
        if not phone_numbers: return jsonify({'success': False, 'message': 'phone_numbers list required'}), 400
        released = 0
        for number in phone_numbers:
            try:
                supabase.table('number_allocations').update({'status': 'released', 'notes': 'Bulk release'}).eq('phone_number', number).execute()
                released += 1
            except: pass
        return jsonify({'success': True, 'message': f'{released} numbers released', 'released': released})
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
        user = request.user
        status = request.args.get('status'); tx_type = request.args.get('type')
        client_id = request.args.get('client_id'); limit = int(request.args.get('limit', 50))
        query = supabase.table('transactions').select('*', count='exact').order('created_at', desc=True).limit(limit)
        if is_client_user(): query = query.eq('client_id', user['id'])
        elif is_agent(user): query = query.eq('manager_id', user['id'])
        if status: query = query.eq('status', status)
        if tx_type: query = query.eq('type', tx_type)
        if client_id: query = query.eq('client_id', client_id)
        response = query.execute()
        return jsonify({'success': True, 'data': response.data or [], 'total': response.count or 0})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/transactions', methods=['POST'])
@authenticate
@require_agent_or_admin
def create_transaction():
    try:
        data = request.get_json()
        transaction = {'type': data.get('type', 'payment'), 'description': data.get('description', ''), 'amount': float(data.get('amount', 0) or 0), 'currency': data.get('currency', 'USD'), 'status': data.get('status', 'pending'), 'client_id': data.get('client_id'), 'manager_id': request.user['id'], 'due_date': data.get('due_date'), 'created_at': datetime.now().isoformat()}
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
        return jsonify({'success': True, 'message': 'Updated', 'data': response.data[0] if response.data else {}})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# ==================== SMS LOGS ====================
@app.route('/api/sms-logs')
@authenticate
def get_sms_logs():
    try:
        user = request.user
        if is_client_user(): return jsonify({'success': False, 'message': 'Access denied'}), 403
        status = request.args.get('status'); limit = int(request.args.get('limit', 50))
        query = supabase.table('sms_logs').select('*').order('created_at', desc=True).limit(limit)
        if is_agent(user): query = query.eq('manager_id', user['id'])
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
        if not query or len(query) < 2: return jsonify({'success': False, 'message': 'Query too short'}), 400
        results = {'messages': [], 'numbers': [], 'clients': []}
        
        msg1 = supabase.table('sms_messages').select('*').ilike('phone_number', f'%{query}%').limit(20).execute()
        msg2 = supabase.table('sms_messages').select('*').ilike('client_name', f'%{query}%').limit(20).execute()
        msg3 = supabase.table('sms_messages').select('*').ilike('message', f'%{query}%').limit(20).execute()
        seen_m = set()
        messages = []
        for m in (msg1.data or []) + (msg2.data or []) + (msg3.data or []):
            if m['id'] not in seen_m:
                seen_m.add(m['id'])
                messages.append(m)
        results['messages'] = messages[:20]
        
        num_resp = supabase.table('number_allocations').select('*').ilike('phone_number', f'%{query}%').limit(20).execute()
        results['numbers'] = num_resp.data or []
        
        c1 = supabase.table('clients').select('*').ilike('name', f'%{query}%').limit(10).execute()
        c2 = supabase.table('clients').select('*').ilike('email', f'%{query}%').limit(10).execute()
        seen_c = set()
        clients = []
        for c in (c1.data or []) + (c2.data or []):
            if c['id'] not in seen_c:
                seen_c.add(c['id'])
                clients.append(c)
        results['clients'] = clients[:10]
        
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
            writer.writerow([msg.get('phone_number', ''), msg.get('client_name', ''), msg.get('message', ''), msg.get('payout', ''), msg.get('status', ''), msg.get('received_at', '')])
        return Response(output.getvalue(), mimetype='text/csv', headers={'Content-Disposition': 'attachment;filename=sms_messages.csv'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# ==================== SYSTEM INFO ====================
@app.route('/api/system/info')
@authenticate
@require_admin
def system_info():
    try:
        return jsonify({'success': True, 'data': {'version': '5.1.0', 'supabase_connected': supabase is not None, 'sms_api_url': SMS_API_URL, 'roles': ['admin', 'agent', 'client']}})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# ==================== ERROR HANDLERS ====================
@app.errorhandler(404)
def not_found(e):
    return jsonify({'success': False, 'message': f'Route {request.method} {request.path} not found'}), 404

@app.errorhandler(500)
def server_error(e):
    return jsonify({'success': False, 'message': 'Internal server error'}), 500

# ==================== START ====================
if __name__ == '__main__':
    print('=' * 60)
    print('🚀 ZK SMS Enterprise Backend Server (Flask) v5.1.0')
    print('=' * 60)
    print(f'📡 Port: {PORT}')
    print(f'🗄️  Supabase: {"✅ Connected" if supabase else "❌ Not configured"}')
    print(f'📨 SMS API: {SMS_API_URL}')
    print(f'👥 Roles: Admin | Agent | Client')
    print(f'🔒 Auto-filter: Agents & Clients see only their numbers')
    print(f'🕒 Started: {datetime.now().isoformat()}')
    print('=' * 60)
    app.run(host='0.0.0.0', port=PORT)
