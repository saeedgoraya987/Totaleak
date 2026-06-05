"""
ZK SMS Enterprise Backend - Complete Production Server (Flask)
Single file implementation matching the Node.js/Express version
"""
import os
import secrets
import logging
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, request, jsonify
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

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Initialize Supabase
supabase: Client = None
try:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    logger.info("✅ Supabase client initialized")
except Exception as e:
    logger.error(f"❌ Supabase init failed: {e}")

# ==================== REQUEST LOGGING ====================
@app.before_request
def log_request():
    logger.info(f"[{datetime.now().isoformat()}] {request.method} {request.path}")

# ==================== AUTH MIDDLEWARE ====================
def authenticate(f):
    """Decorator to require API key authentication"""
    @wraps(f)
    def decorated(*args, **kwargs):
        api_key = request.headers.get('x-api-key') or request.args.get('api_key')
        
        if not api_key:
            return jsonify({
                'success': False,
                'message': 'API key required. Use x-api-key header or api_key query parameter.'
            }), 401
        
        try:
            response = supabase.table('managers').select('*').eq('api_key', api_key).eq('status', 'active').single().execute()
            user = response.data
            
            if not user:
                return jsonify({'success': False, 'message': 'Invalid or inactive API key'}), 401
            
            # Update API usage
            try:
                supabase.table('api_usage').upsert({
                    'manager_id': user['id'],
                    'endpoint': request.path,
                    'requests_count': 1,
                    'last_used': datetime.now().isoformat()
                }, on_conflict='manager_id,endpoint').execute()
            except:
                pass
            
            request.user = user
            return f(*args, **kwargs)
        except Exception as e:
            logger.error(f"Auth error: {e}")
            return jsonify({'success': False, 'message': 'Authentication error'}), 500
    
    return decorated

def require_admin(f):
    """Decorator to require admin role"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not hasattr(request, 'user') or request.user.get('role') != 'admin':
            return jsonify({'success': False, 'message': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated

# ==================== HELPER FUNCTIONS ====================
def calculate_sms_stats(messages):
    """Calculate SMS statistics from messages"""
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
        # Parse date
        date_str = msg.get('dt') or msg.get('received_at')
        if not date_str:
            continue
        
        try:
            msg_date = datetime.fromisoformat(str(date_str).replace('Z', '+00:00'))
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

def get_top_numbers(messages, limit=10):
    """Get top phone numbers by message count"""
    number_stats = {}
    
    for msg in messages:
        number = msg.get('num') or msg.get('phone_number')
        if not number:
            continue
        
        if number not in number_stats:
            number_stats[number] = {'count': 0, 'payout': 0}
        
        number_stats[number]['count'] += 1
        number_stats[number]['payout'] += float(msg.get('payout', 0) or 0)
    
    sorted_numbers = sorted(number_stats.items(), key=lambda x: x[1]['count'], reverse=True)[:limit]
    return [{'number': n, 'messages': d['count'], 'totalPayout': round(d['payout'], 4)} for n, d in sorted_numbers]

def get_top_clients(messages, limit=10):
    """Get top clients by message count"""
    client_stats = {}
    
    for msg in messages:
        client = msg.get('cli') or msg.get('client_name') or 'Unknown'
        client_stats[client] = client_stats.get(client, 0) + 1
    
    sorted_clients = sorted(client_stats.items(), key=lambda x: x[1], reverse=True)[:limit]
    return [{'client': c, 'count': n} for c, n in sorted_clients]

def get_hourly_breakdown(messages):
    """Get hourly breakdown of messages for today"""
    hours = {}
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    
    for msg in messages:
        date_str = msg.get('dt') or msg.get('received_at')
        if not date_str:
            continue
        
        try:
            msg_date = datetime.fromisoformat(str(date_str).replace('Z', '+00:00'))
            if msg_date.tzinfo:
                msg_date = msg_date.replace(tzinfo=None)
        except:
            continue
        
        if msg_date >= today:
            hour = msg_date.hour
            hours[hour] = hours.get(hour, 0) + 1
    
    return [{'hour': f'{h}:00', 'count': c} for h, c in sorted(hours.items())]

def sync_messages_to_db(messages):
    """Sync messages from API to Supabase"""
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
        except Exception as e:
            logger.error(f"Sync error: {e}")
    
    return synced

# ==================== HEALTH CHECK ====================
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
        start_time = datetime.now()
        
        # Check database
        db_status = 'unknown'
        if supabase:
            try:
                response = supabase.table('managers').select('id').limit(1).execute()
                db_status = 'connected' if response.data else 'empty'
            except Exception as e:
                db_status = f'error: {str(e)}'
        
        # Check SMS API
        sms_status = 'unknown'
        try:
            resp = requests.get(SMS_API_URL, params={'token': SMS_API_TOKEN}, timeout=5)
            sms_status = 'online' if resp.status_code == 200 else 'degraded'
        except Exception as e:
            sms_status = f'offline: {str(e)}'
        
        response_time = (datetime.now() - start_time).total_seconds() * 1000
        
        return jsonify({
            'success': True,
            'status': 'healthy' if db_status == 'connected' else 'degraded',
            'services': {
                'database': db_status,
                'sms_api': sms_status
            },
            'responseTime': f'{response_time:.0f}ms',
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ==================== AUTH ROUTES ====================
@app.route('/api/auth/login', methods=['POST'])
def login():
    try:
        data = request.get_json()
        username = (data.get('username') or '').strip()
        password = (data.get('password') or '').strip()
        
        if not username or not password:
            return jsonify({'success': False, 'message': 'Username and password are required'}), 400
        
        # Find user
        response = supabase.table('managers').select('*').eq('username', username).eq('password', password).eq('status', 'active').single().execute()
        user = response.data
        
        if not user:
            return jsonify({'success': False, 'message': 'Invalid credentials or account inactive'}), 401
        
        # Generate API key if not exists
        if not user.get('api_key'):
            api_key = secrets.token_hex(32)
            supabase.table('managers').update({'api_key': api_key}).eq('id', user['id']).execute()
            user['api_key'] = api_key
        
        # Update last login
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
        return jsonify({'success': False, 'message': 'Login failed'}), 500

@app.route('/api/auth/me')
@authenticate
def me():
    return jsonify({'success': True, 'data': {'user': request.user}})

# ==================== SMS MESSAGES ROUTES ====================
@app.route('/api/sms/messages')
@authenticate
def sms_messages():
    try:
        limit = int(request.args.get('limit', 100))
        offset = int(request.args.get('offset', 0))
        number = request.args.get('number')
        client = request.args.get('client')
        start_date = request.args.get('startDate')
        end_date = request.args.get('endDate')
        refresh = request.args.get('refresh', 'false').lower() == 'true'
        
        # Sync from external API if refresh is true
        if refresh:
            try:
                api_response = requests.get(SMS_API_URL, params={'token': SMS_API_TOKEN}, timeout=10)
                if api_response.json().get('data'):
                    sync_messages_to_db(api_response.json()['data'])
            except Exception as e:
                logger.error(f"Sync error: {e}")
        
        # Build query
        query = supabase.table('sms_messages').select('*', count='exact').order('received_at', desc=True).range(offset, offset + limit - 1)
        
        if number:
            query = query.eq('phone_number', number)
        if client:
            query = query.eq('client_name', client)
        if start_date:
            query = query.gte('received_at', start_date)
        if end_date:
            query = query.lte('received_at', end_date)
        
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
        logger.error(f"Get messages error: {e}")
        return jsonify({'success': False, 'message': 'Failed to fetch messages'}), 500

@app.route('/api/sms/messages/live')
@authenticate
def sms_messages_live():
    try:
        response = requests.get(SMS_API_URL, params={'token': SMS_API_TOKEN}, timeout=10)
        messages = response.json().get('data', [])
        
        return jsonify({
            'success': True,
            'source': 'api',
            'data': messages,
            'total': len(messages)
        })
    except Exception as e:
        logger.error(f"Live fetch error: {e}")
        return jsonify({'success': False, 'message': 'Failed to fetch live messages'}), 500

@app.route('/api/sms/messages/number/<number>')
@authenticate
def sms_messages_by_number(number):
    try:
        limit = int(request.args.get('limit', 100))
        
        response = supabase.table('sms_messages').select('*', count='exact').eq('phone_number', number).order('received_at', desc=True).limit(limit).execute()
        data = response.data or []
        
        total_payout = sum(float(msg.get('payout', 0) or 0) for msg in data)
        unique_clients = list(set(msg.get('client_name') for msg in data if msg.get('client_name')))
        
        return jsonify({
            'success': True,
            'data': {
                'number': number,
                'messages': data,
                'stats': {
                    'totalMessages': response.count or 0,
                    'totalPayout': round(total_payout, 4),
                    'uniqueClients': unique_clients,
                    'lastMessage': data[0].get('received_at') if data else None
                }
            }
        })
    except Exception as e:
        logger.error(f"Get number messages error: {e}")
        return jsonify({'success': False, 'message': 'Failed to fetch messages for this number'}), 500

@app.route('/api/sms/messages/client/<client>')
@authenticate
def sms_messages_by_client(client):
    try:
        limit = int(request.args.get('limit', 100))
        
        response = supabase.table('sms_messages').select('*', count='exact').eq('client_name', client).order('received_at', desc=True).limit(limit).execute()
        data = response.data or []
        
        unique_numbers = list(set(msg.get('phone_number') for msg in data if msg.get('phone_number')))
        
        return jsonify({
            'success': True,
            'data': {
                'client': client,
                'messages': data,
                'stats': {
                    'totalMessages': response.count or 0,
                    'uniqueNumbers': len(unique_numbers),
                    'numbers': unique_numbers[:10]
                }
            }
        })
    except Exception as e:
        logger.error(f"Get client messages error: {e}")
        return jsonify({'success': False, 'message': 'Failed to fetch client messages'}), 500

# ==================== SMS STATISTICS ====================
@app.route('/api/sms/stats')
@authenticate
def sms_stats():
    try:
        # Get DB messages from last 30 days
        thirty_days_ago = (datetime.now() - timedelta(days=30)).isoformat()
        
        db_response = supabase.table('sms_messages').select('*').gte('received_at', thirty_days_ago).order('received_at', desc=True).execute()
        db_messages = db_response.data or []
        
        # Get live API messages
        api_messages = []
        try:
            api_response = requests.get(SMS_API_URL, params={'token': SMS_API_TOKEN}, timeout=5)
            api_messages = api_response.json().get('data', [])
        except Exception as e:
            logger.warning(f"Could not fetch live API data: {e}")
        
        all_messages = db_messages + api_messages
        stats = calculate_sms_stats(all_messages)
        top_numbers = get_top_numbers(all_messages, 10)
        top_clients = get_top_clients(all_messages, 5)
        hourly_breakdown = get_hourly_breakdown(all_messages)
        
        return jsonify({
            'success': True,
            'data': {
                'overview': stats,
                'topNumbers': top_numbers,
                'topClients': top_clients,
                'hourlyBreakdown': hourly_breakdown,
                'lastUpdated': datetime.now().isoformat(),
                'sources': {
                    'database': len(db_messages),
                    'liveApi': len(api_messages)
                }
            }
        })
    except Exception as e:
        logger.error(f"Stats error: {e}")
        return jsonify({'success': False, 'message': 'Failed to calculate statistics'}), 500

# ==================== SMS SYNC ====================
@app.route('/api/sms/sync', methods=['POST'])
@authenticate
@require_admin
def sms_sync():
    try:
        response = requests.get(SMS_API_URL, params={'token': SMS_API_TOKEN}, timeout=10)
        messages = response.json().get('data', [])
        
        if not messages:
            return jsonify({'success': True, 'message': 'No messages to sync', 'synced': 0})
        
        synced = sync_messages_to_db(messages)
        
        # Log the sync
        try:
            supabase.table('sms_logs').insert({
                'to_number': 'SYSTEM',
                'message': f'Synced {synced} messages from external API',
                'status': 'synced',
                'manager_id': request.user['id'],
                'created_at': datetime.now().isoformat()
            }).execute()
        except:
            pass
        
        return jsonify({
            'success': True,
            'message': f'Successfully synced {synced} messages',
            'data': {
                'synced': synced,
                'total': len(messages),
                'timestamp': datetime.now().isoformat()
            }
        })
    except Exception as e:
        logger.error(f"Sync error: {e}")
        return jsonify({'success': False, 'message': 'Sync failed'}), 500

# ==================== DASHBOARD ROUTES ====================
@app.route('/api/dashboard/stats')
@authenticate
def dashboard_stats():
    try:
        # Fetch all data
        managers_count = supabase.table('managers').select('id', count='exact').execute().count or 0
        clients_count = supabase.table('clients').select('id', count='exact').execute().count or 0
        active_clients_count = supabase.table('clients').select('id', count='exact').eq('status', 'active').execute().count or 0
        
        sms_ranges_resp = supabase.table('sms_ranges').select('*').execute()
        rate_cards_resp = supabase.table('rate_cards').select('*').order('price').execute()
        recent_messages_resp = supabase.table('sms_messages').select('*').order('received_at', desc=True).limit(50).execute()
        transactions_resp = supabase.table('transactions').select('*').order('created_at', desc=True).limit(20).execute()
        sms_logs_resp = supabase.table('sms_logs').select('*').order('created_at', desc=True).limit(20).execute()
        
        # Calculate SMS stats
        sms_stats = calculate_sms_stats(recent_messages_resp.data or [])
        
        # Calculate financial metrics
        transactions = transactions_resp.data or []
        now = datetime.now()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_ago = today - timedelta(days=7)
        month_ago = today - timedelta(days=30)
        
        financials = {
            'revenueToday': 0,
            'revenueWeek': 0,
            'revenueMonth': 0,
            'totalPending': 0,
            'totalPaid': 0,
            'transactionCount': len(transactions)
        }
        
        for tx in transactions:
            amount = float(tx.get('amount', 0) or 0)
            tx_date_str = tx.get('created_at')
            if not tx_date_str:
                continue
            
            try:
                tx_date = datetime.fromisoformat(str(tx_date_str).replace('Z', '+00:00'))
                if tx_date.tzinfo:
                    tx_date = tx_date.replace(tzinfo=None)
            except:
                continue
            
            if tx.get('status') == 'paid':
                if tx_date >= today:
                    financials['revenueToday'] += amount
                if tx_date >= week_ago:
                    financials['revenueWeek'] += amount
                if tx_date >= month_ago:
                    financials['revenueMonth'] += amount
                financials['totalPaid'] += amount
            elif tx.get('status') == 'pending':
                financials['totalPending'] += amount
        
        # API usage
        api_usage_resp = supabase.table('api_usage').select('*').order('last_used', desc=True).limit(10).execute()
        
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
            'financial': financials,
            'system': {
                'apiUsage': api_usage_resp.data or [],
                'recentTransactions': transactions[:10],
                'recentLogs': (sms_logs_resp.data or [])[:10]
            },
            'lastUpdated': datetime.now().isoformat()
        }
        
        return jsonify({'success': True, 'data': dashboard})
    except Exception as e:
        logger.error(f"Dashboard error: {e}")
        return jsonify({'success': False, 'message': 'Failed to load dashboard'}), 500

# ==================== MANAGER ROUTES ====================
@app.route('/api/managers')
@authenticate
def get_managers():
    try:
        status = request.args.get('status')
        role = request.args.get('role')
        
        query = supabase.table('managers').select('*', count='exact').order('created_at', desc=True)
        
        if status:
            query = query.eq('status', status)
        if role:
            query = query.eq('role', role)
        
        response = query.execute()
        
        # Remove password field
        safe_data = []
        for manager in (response.data or []):
            manager_copy = {k: v for k, v in manager.items() if k != 'password'}
            safe_data.append(manager_copy)
        
        return jsonify({
            'success': True,
            'data': safe_data,
            'total': response.count or 0
        })
    except Exception as e:
        logger.error(f"Get managers error: {e}")
        return jsonify({'success': False, 'message': 'Failed to fetch managers'}), 500

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
        
        # Check for existing user
        existing = supabase.table('managers').select('id').or_(f'username.eq.{username},email.eq.{email}').limit(1).execute()
        if existing.data:
            return jsonify({'success': False, 'message': 'Username or email already exists'}), 400
        
        new_manager = {
            'username': username,
            'password': password,
            'email': email,
            'phone': data.get('phone', ''),
            'country': data.get('country', ''),
            'role': data.get('role', 'manager'),
            'status': 'active',
            'permissions': data.get('permissions', ['view_reports']),
            'created_at': datetime.now().isoformat(),
            'updated_at': datetime.now().isoformat()
        }
        
        response = supabase.table('managers').insert(new_manager).execute()
        created = response.data[0] if response.data else {}
        safe_data = {k: v for k, v in created.items() if k != 'password'}
        
        return jsonify({
            'success': True,
            'message': 'Manager created successfully',
            'data': safe_data
        }), 201
    except Exception as e:
        logger.error(f"Create manager error: {e}")
        return jsonify({'success': False, 'message': 'Failed to create manager'}), 500

@app.route('/api/managers/<manager_id>', methods=['PUT'])
@authenticate
@require_admin
def update_manager(manager_id):
    try:
        updates = request.get_json()
        updates.pop('password', None)  # Don't allow password update through this endpoint
        updates['updated_at'] = datetime.now().isoformat()
        
        response = supabase.table('managers').update(updates).eq('id', manager_id).execute()
        
        return jsonify({
            'success': True,
            'message': 'Manager updated successfully',
            'data': response.data[0] if response.data else {}
        })
    except Exception as e:
        logger.error(f"Update manager error: {e}")
        return jsonify({'success': False, 'message': 'Failed to update manager'}), 500

@app.route('/api/managers/<manager_id>/status', methods=['PATCH'])
@authenticate
@require_admin
def toggle_manager_status(manager_id):
    try:
        data = request.get_json()
        status = data.get('status')
        
        if status not in ['active', 'inactive', 'suspended']:
            return jsonify({'success': False, 'message': 'Valid status required (active, inactive, suspended)'}), 400
        
        response = supabase.table('managers').update({
            'status': status,
            'updated_at': datetime.now().isoformat()
        }).eq('id', manager_id).execute()
        
        return jsonify({
            'success': True,
            'message': f"Manager {'activated' if status == 'active' else status}",
            'data': response.data[0] if response.data else {}
        })
    except Exception as e:
        logger.error(f"Status update error: {e}")
        return jsonify({'success': False, 'message': 'Failed to update status'}), 500

@app.route('/api/managers/<manager_id>', methods=['DELETE'])
@authenticate
@require_admin
def delete_manager(manager_id):
    try:
        supabase.table('managers').delete().eq('id', manager_id).execute()
        return jsonify({'success': True, 'message': 'Manager deleted successfully'})
    except Exception as e:
        logger.error(f"Delete manager error: {e}")
        return jsonify({'success': False, 'message': 'Failed to delete manager'}), 500

# ==================== CLIENTS ROUTES ====================
@app.route('/api/clients')
@authenticate
def get_clients():
    try:
        status = request.args.get('status')
        
        query = supabase.table('clients').select('*', count='exact').order('created_at', desc=True)
        
        if status:
            query = query.eq('status', status)
        
        response = query.execute()
        
        return jsonify({
            'success': True,
            'data': response.data or [],
            'total': response.count or 0
        })
    except Exception as e:
        logger.error(f"Get clients error: {e}")
        return jsonify({'success': False, 'message': 'Failed to fetch clients'}), 500

@app.route('/api/clients', methods=['POST'])
@authenticate
def create_client():
    try:
        data = request.get_json()
        name = (data.get('name') or '').strip()
        
        if not name:
            return jsonify({'success': False, 'message': 'Client name is required'}), 400
        
        new_client = {
            'name': name,
            'email': data.get('email', ''),
            'phone': data.get('phone', ''),
            'country': data.get('country', ''),
            'balance': float(data.get('balance', 0) or 0),
            'status': 'active',
            'manager_id': request.user['id'],
            'created_at': datetime.now().isoformat(),
            'updated_at': datetime.now().isoformat()
        }
        
        response = supabase.table('clients').insert(new_client).execute()
        
        return jsonify({
            'success': True,
            'message': 'Client created successfully',
            'data': response.data[0] if response.data else {}
        }), 201
    except Exception as e:
        logger.error(f"Create client error: {e}")
        return jsonify({'success': False, 'message': 'Failed to create client'}), 500

# ==================== SMS RANGES ROUTES ====================
@app.route('/api/sms-ranges')
@authenticate
def get_sms_ranges():
    try:
        response = supabase.table('sms_ranges').select('*').order('created_at', desc=True).execute()
        return jsonify({'success': True, 'data': response.data or []})
    except Exception as e:
        logger.error(f"Get ranges error: {e}")
        return jsonify({'success': False, 'message': 'Failed to fetch SMS ranges'}), 500

# ==================== RATE CARDS ROUTES ====================
@app.route('/api/rate-cards')
@authenticate
def get_rate_cards():
    try:
        response = supabase.table('rate_cards').select('*').order('price').execute()
        return jsonify({'success': True, 'data': response.data or []})
    except Exception as e:
        logger.error(f"Get rate cards error: {e}")
        return jsonify({'success': False, 'message': 'Failed to fetch rate cards'}), 500

# ==================== TRANSACTIONS ROUTES ====================
@app.route('/api/transactions')
@authenticate
def get_transactions():
    try:
        status = request.args.get('status')
        tx_type = request.args.get('type')
        client_id = request.args.get('client_id')
        limit = int(request.args.get('limit', 50))
        
        query = supabase.table('transactions').select('*', count='exact').order('created_at', desc=True).limit(limit)
        
        if status:
            query = query.eq('status', status)
        if tx_type:
            query = query.eq('type', tx_type)
        if client_id:
            query = query.eq('client_id', client_id)
        
        response = query.execute()
        
        return jsonify({
            'success': True,
            'data': response.data or [],
            'total': response.count or 0
        })
    except Exception as e:
        logger.error(f"Get transactions error: {e}")
        return jsonify({'success': False, 'message': 'Failed to fetch transactions'}), 500

# ==================== SMS LOGS ROUTES ====================
@app.route('/api/sms-logs')
@authenticate
def get_sms_logs():
    try:
        status = request.args.get('status')
        limit = int(request.args.get('limit', 50))
        
        query = supabase.table('sms_logs').select('*').order('created_at', desc=True).limit(limit)
        
        if status:
            query = query.eq('status', status)
        
        response = query.execute()
        
        return jsonify({'success': True, 'data': response.data or []})
    except Exception as e:
        logger.error(f"Get logs error: {e}")
        return jsonify({'success': False, 'message': 'Failed to fetch SMS logs'}), 500

# ==================== ERROR HANDLERS ====================
@app.errorhandler(404)
def not_found(e):
    return jsonify({
        'success': False,
        'message': f'Route {request.method} {request.path} not found'
    }), 404

@app.errorhandler(500)
def server_error(e):
    return jsonify({
        'success': False,
        'message': 'Internal server error'
    }), 500

# ==================== START SERVER ====================
if __name__ == '__main__':
    print('=' * 60)
    print('🚀 ZK SMS Enterprise Backend Server (Flask)')
    print('=' * 60)
    print(f'📡 Port: {PORT}')
    print(f'🗄️  Supabase: {"✅ Connected" if supabase else "❌ Not configured"}')
    print(f'📨 SMS API: {SMS_API_URL}')
    print(f'🕒 Started at: {datetime.now().isoformat()}')
    print('=' * 60)
    
    app.run(host='0.0.0.0', port=PORT)
