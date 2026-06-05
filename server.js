// ZK SMS Enterprise Backend - Complete Production Server
const express = require('express');
const cors = require('cors');
const axios = require('axios');
const crypto = require('crypto');
const { createClient } = require('@supabase/supabase-js');

// ==================== CONFIGURATION ====================
const SUPABASE_URL = process.env.SUPABASE_URL || 'https://xwfdjxqrwkimugsxkvnj.supabase.co';
const SUPABASE_KEY = process.env.SUPABASE_KEY || process.env.SUPABASE_SERVICE_KEY || '';
const SMS_API_URL = process.env.SMS_API_URL || 'http://147.135.212.197/crapi/s1t/viewstats';
const SMS_API_TOKEN = process.env.SMS_API_TOKEN || '';
const PORT = process.env.PORT || 5000;

// Validate config
if (!SUPABASE_URL || !SUPABASE_KEY) {
    console.error('❌ SUPABASE_URL and SUPABASE_KEY are required!');
    console.log('Using fallback values...');
}

// ==================== INITIALIZE SERVICES ====================
let supabase;
try {
    supabase = createClient(SUPABASE_URL, SUPABASE_KEY, {
        auth: { persistSession: false }
    });
    console.log('✅ Supabase client initialized');
} catch (error) {
    console.error('❌ Failed to initialize Supabase:', error.message);
    process.exit(1);
}

const app = express();

// ==================== MIDDLEWARE ====================
app.use(cors());
app.use(express.json({ limit: '10mb' }));
app.use(express.urlencoded({ extended: true }));

// Request logging
app.use((req, res, next) => {
    const timestamp = new Date().toISOString();
    console.log(`[${timestamp}] ${req.method} ${req.path}`);
    next();
});

// ==================== AUTH MIDDLEWARE ====================
const authenticate = async (req, res, next) => {
    try {
        const apiKey = req.headers['x-api-key'] || req.query.api_key;

        if (!apiKey) {
            return res.status(401).json({
                success: false,
                message: 'API key required. Use x-api-key header or api_key query parameter.'
            });
        }

        const { data: user, error } = await supabase
            .from('managers')
            .select('*')
            .eq('api_key', apiKey)
            .eq('status', 'active')
            .single();

        if (error || !user) {
            return res.status(401).json({
                success: false,
                message: 'Invalid or inactive API key'
            });
        }

        req.user = user;
        next();
    } catch (error) {
        console.error('Auth error:', error);
        res.status(500).json({
            success: false,
            message: 'Authentication error'
        });
    }
};

// Admin only middleware
const requireAdmin = (req, res, next) => {
    if (req.user && req.user.role === 'admin') {
        return next();
    }
    res.status(403).json({
        success: false,
        message: 'Admin access required'
    });
};

// ==================== ROOT & HEALTH CHECK ====================
app.get('/', (req, res) => {
    res.json({
        success: true,
        name: 'ZK SMS Enterprise API',
        version: '3.0.0',
        timestamp: new Date().toISOString()
    });
});

app.get('/health', async (req, res) => {
    try {
        const startTime = Date.now();
        
        // Check database connection
        let dbStatus = 'unknown';
        try {
            const { data } = await supabase.from('managers').select('id').limit(1);
            dbStatus = data ? 'connected' : 'empty';
        } catch (e) {
            dbStatus = 'error: ' + e.message;
        }
        
        // Check SMS API
        let smsStatus = 'unknown';
        try {
            const response = await axios.get(SMS_API_URL, { 
                params: { token: SMS_API_TOKEN },
                timeout: 5000 
            });
            smsStatus = response.data?.status === 'success' ? 'online' : 'degraded';
        } catch (e) {
            smsStatus = 'offline: ' + e.message;
        }

        res.json({
            success: true,
            status: dbStatus === 'connected' ? 'healthy' : 'degraded',
            services: {
                database: dbStatus,
                sms_api: smsStatus
            },
            uptime: Math.floor(process.uptime()),
            responseTime: `${Date.now() - startTime}ms`,
            timestamp: new Date().toISOString()
        });
    } catch (error) {
        res.status(500).json({
            success: false,
            status: 'unhealthy',
            error: error.message
        });
    }
});

// ==================== AUTH ROUTES ====================
app.post('/api/auth/login', async (req, res) => {
    try {
        const { username, password } = req.body;

        if (!username || !password) {
            return res.status(400).json({
                success: false,
                message: 'Username and password are required'
            });
        }

        const { data: user, error } = await supabase
            .from('managers')
            .select('*')
            .eq('username', username)
            .eq('password', password)
            .eq('status', 'active')
            .single();

        if (error || !user) {
            return res.status(401).json({
                success: false,
                message: 'Invalid credentials or account inactive'
            });
        }

        if (!user.api_key) {
            const apiKey = crypto.randomBytes(32).toString('hex');
            await supabase
                .from('managers')
                .update({ api_key: apiKey })
                .eq('id', user.id);
            user.api_key = apiKey;
        }

        await supabase
            .from('managers')
            .update({ last_login: new Date().toISOString() })
            .eq('id', user.id);

        console.log(`✅ User logged in: ${user.username}`);

        res.json({
            success: true,
            message: 'Login successful',
            data: {
                user: {
                    id: user.id,
                    username: user.username,
                    email: user.email,
                    phone: user.phone,
                    country: user.country,
                    role: user.role,
                    status: user.status,
                    permissions: user.permissions,
                    api_key: user.api_key,
                    last_login: user.last_login
                }
            }
        });
    } catch (error) {
        console.error('Login error:', error);
        res.status(500).json({
            success: false,
            message: 'Login failed: ' + error.message
        });
    }
});

app.get('/api/auth/me', authenticate, async (req, res) => {
    res.json({
        success: true,
        data: { user: req.user }
    });
});

// ==================== SMS ROUTES ====================
app.get('/api/sms/live', authenticate, async (req, res) => {
    try {
        const response = await axios.get(SMS_API_URL, {
            params: { token: SMS_API_TOKEN },
            timeout: 10000
        });

        res.json({
            success: true,
            source: 'api',
            data: response.data
        });
    } catch (error) {
        console.error('Live SMS fetch error:', error.message);
        res.status(500).json({
            success: false,
            message: 'Failed to fetch live messages: ' + error.message
        });
    }
});

app.get('/api/sms/messages', authenticate, async (req, res) => {
    try {
        const { limit = 100, offset = 0, number, client } = req.query;

        let query = supabase
            .from('sms_messages')
            .select('*', { count: 'exact' })
            .order('received_at', { ascending: false })
            .range(parseInt(offset), parseInt(offset) + parseInt(limit) - 1);

        if (number) query = query.eq('phone_number', number);
        if (client) query = query.eq('client_name', client);

        const { data, error, count } = await query;

        if (error) throw error;

        res.json({
            success: true,
            data: data || [],
            pagination: {
                total: count || 0,
                limit: parseInt(limit),
                offset: parseInt(offset),
                hasMore: (parseInt(offset) + parseInt(limit)) < (count || 0)
            }
        });
    } catch (error) {
        console.error('Get messages error:', error);
        res.status(500).json({
            success: false,
            message: 'Failed to fetch messages'
        });
    }
});

app.get('/api/sms/stats', authenticate, async (req, res) => {
    try {
        // Get live data
        let apiMessages = [];
        try {
            const response = await axios.get(SMS_API_URL, {
                params: { token: SMS_API_TOKEN },
                timeout: 5000
            });
            apiMessages = response.data?.data || [];
        } catch (e) {
            console.log('API fetch failed, using DB only');
        }

        // Get DB data
        const thirtyDaysAgo = new Date();
        thirtyDaysAgo.setDate(thirtyDaysAgo.getDate() - 30);

        const { data: dbMessages } = await supabase
            .from('sms_messages')
            .select('*')
            .gte('received_at', thirtyDaysAgo.toISOString())
            .order('received_at', { ascending: false })
            .limit(500);

        const allMessages = [...(dbMessages || []), ...apiMessages];
        const stats = calculateStats(allMessages);

        res.json({
            success: true,
            data: stats,
            sources: {
                database: dbMessages?.length || 0,
                liveApi: apiMessages.length
            }
        });
    } catch (error) {
        console.error('Stats error:', error);
        res.status(500).json({
            success: false,
            message: 'Failed to calculate statistics'
        });
    }
});

app.post('/api/sms/sync', authenticate, requireAdmin, async (req, res) => {
    try {
        const response = await axios.get(SMS_API_URL, {
            params: { token: SMS_API_TOKEN },
            timeout: 10000
        });

        const messages = response.data?.data || [];
        if (!messages.length) {
            return res.json({ success: true, message: 'No messages to sync', synced: 0 });
        }

        let synced = 0;
        for (const msg of messages) {
            try {
                const { error } = await supabase.from('sms_messages').upsert({
                    phone_number: msg.num,
                    client_name: msg.cli,
                    message: msg.message,
                    payout: parseFloat(msg.payout) || 0,
                    status: 'received',
                    received_at: msg.dt
                }, {
                    onConflict: 'phone_number,received_at',
                    ignoreDuplicates: true
                });
                if (!error) synced++;
            } catch (err) {
                // Skip duplicate errors
            }
        }

        res.json({
            success: true,
            message: `Synced ${synced} messages`,
            synced,
            total: messages.length
        });
    } catch (error) {
        console.error('Sync error:', error);
        res.status(500).json({
            success: false,
            message: 'Sync failed: ' + error.message
        });
    }
});

// ==================== DASHBOARD ====================
app.get('/api/dashboard', authenticate, async (req, res) => {
    try {
        const { data: recentMessages } = await supabase
            .from('sms_messages')
            .select('*')
            .order('received_at', { ascending: false })
            .limit(50);

        const stats = calculateStats(recentMessages || []);

        res.json({
            success: true,
            data: {
                sms: stats,
                recentMessages: (recentMessages || []).slice(0, 10),
                lastUpdated: new Date().toISOString()
            }
        });
    } catch (error) {
        console.error('Dashboard error:', error);
        res.status(500).json({
            success: false,
            message: 'Failed to load dashboard'
        });
    }
});

// ==================== MANAGERS ====================
app.get('/api/managers', authenticate, async (req, res) => {
    try {
        const { data, error } = await supabase
            .from('managers')
            .select('id, username, email, phone, country, role, status, permissions, created_at, last_login')
            .order('created_at', { ascending: false });

        if (error) throw error;

        res.json({
            success: true,
            data: data || []
        });
    } catch (error) {
        console.error('Get managers error:', error);
        res.status(500).json({
            success: false,
            message: 'Failed to fetch managers'
        });
    }
});

app.post('/api/managers', authenticate, requireAdmin, async (req, res) => {
    try {
        const { username, password, email, phone, country, role } = req.body;

        if (!username || !password || !email) {
            return res.status(400).json({
                success: false,
                message: 'Username, password, and email are required'
            });
        }

        const { data, error } = await supabase
            .from('managers')
            .insert([{
                username,
                password,
                email,
                phone: phone || '',
                country: country || '',
                role: role || 'manager',
                status: 'active',
                permissions: ['view_reports'],
                created_at: new Date().toISOString()
            }])
            .select('id, username, email, phone, country, role, status, created_at')
            .single();

        if (error) {
            if (error.code === '23505') {
                return res.status(400).json({
                    success: false,
                    message: 'Username or email already exists'
                });
            }
            throw error;
        }

        res.status(201).json({
            success: true,
            message: 'Manager created',
            data
        });
    } catch (error) {
        console.error('Create manager error:', error);
        res.status(500).json({
            success: false,
            message: 'Failed to create manager'
        });
    }
});

app.patch('/api/managers/:id/status', authenticate, requireAdmin, async (req, res) => {
    try {
        const { id } = req.params;
        const { status } = req.body;

        if (!['active', 'inactive'].includes(status)) {
            return res.status(400).json({ success: false, message: 'Invalid status' });
        }

        const { error } = await supabase
            .from('managers')
            .update({ status, updated_at: new Date().toISOString() })
            .eq('id', id);

        if (error) throw error;

        res.json({ success: true, message: `Manager ${status}` });
    } catch (error) {
        res.status(500).json({ success: false, message: error.message });
    }
});

app.delete('/api/managers/:id', authenticate, requireAdmin, async (req, res) => {
    try {
        const { error } = await supabase.from('managers').delete().eq('id', req.params.id);
        if (error) throw error;
        res.json({ success: true, message: 'Manager deleted' });
    } catch (error) {
        res.status(500).json({ success: false, message: error.message });
    }
});

// ==================== CLIENTS ====================
app.get('/api/clients', authenticate, async (req, res) => {
    try {
        const { data, error } = await supabase
            .from('clients')
            .select('*')
            .order('created_at', { ascending: false });

        if (error) throw error;

        res.json({ success: true, data: data || [] });
    } catch (error) {
        res.status(500).json({ success: false, message: 'Failed to fetch clients' });
    }
});

app.post('/api/clients', authenticate, async (req, res) => {
    try {
        const { name, email, phone, country } = req.body;

        if (!name) {
            return res.status(400).json({ success: false, message: 'Client name required' });
        }

        const { data, error } = await supabase
            .from('clients')
            .insert([{
                name,
                email: email || '',
                phone: phone || '',
                country: country || '',
                balance: 0,
                status: 'active',
                manager_id: req.user.id,
                created_at: new Date().toISOString()
            }])
            .select()
            .single();

        if (error) throw error;

        res.status(201).json({ success: true, message: 'Client created', data });
    } catch (error) {
        res.status(500).json({ success: false, message: error.message });
    }
});

// ==================== HELPER FUNCTIONS ====================
function calculateStats(messages) {
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const weekAgo = new Date(today.getTime() - 7 * 24 * 60 * 60 * 1000);
    const monthAgo = new Date(today.getTime() - 30 * 24 * 60 * 60 * 1000);

    const stats = {
        total: messages.length,
        today: { count: 0, payout: 0 },
        week: { count: 0, payout: 0 },
        month: { count: 0, payout: 0 },
        byClient: {},
        topClients: []
    };

    messages.forEach(msg => {
        const msgDate = new Date(msg.dt || msg.received_at);
        if (isNaN(msgDate.getTime())) return;

        const payout = parseFloat(msg.payout) || 0;
        const client = msg.cli || msg.client_name || 'Unknown';

        if (msgDate >= today) {
            stats.today.count++;
            stats.today.payout += payout;
        }
        if (msgDate >= weekAgo) {
            stats.week.count++;
            stats.week.payout += payout;
        }
        if (msgDate >= monthAgo) {
            stats.month.count++;
            stats.month.payout += payout;
        }

        stats.byClient[client] = (stats.byClient[client] || 0) + 1;
    });

    stats.topClients = Object.entries(stats.byClient)
        .sort(([, a], [, b]) => b - a)
        .slice(0, 10)
        .map(([client, count]) => ({ client, count }));

    stats.today.payout = stats.today.payout.toFixed(4);
    stats.week.payout = stats.week.payout.toFixed(4);
    stats.month.payout = stats.month.payout.toFixed(4);

    return stats;
}

// ==================== ERROR HANDLER ====================
app.use((err, req, res, next) => {
    console.error('Unhandled error:', err);
    res.status(500).json({
        success: false,
        message: 'Internal server error'
    });
});

// 404 handler
app.use((req, res) => {
    res.status(404).json({
        success: false,
        message: `Route ${req.method} ${req.path} not found`
    });
});

// ==================== START SERVER ====================
app.listen(PORT, '0.0.0.0', () => {
    console.log('='.repeat(60));
    console.log('🚀 ZK SMS Enterprise Backend Server');
    console.log('='.repeat(60));
    console.log(`📡 Port: ${PORT}`);
    console.log(`🗄️  Supabase: ${SUPABASE_URL ? '✅ Connected' : '❌ Not configured'}`);
    console.log(`📨 SMS API: ${SMS_API_URL}`);
    console.log(`🕒 Started at: ${new Date().toISOString()}`);
    console.log('='.repeat(60));
});

process.on('SIGTERM', () => process.exit(0));
process.on('SIGINT', () => process.exit(0));

module.exports = app;
