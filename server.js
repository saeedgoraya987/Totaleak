const express = require('express');
const cors = require('cors');
const helmet = require('helmet');
const axios = require('axios');
const { createClient } = require('@supabase/supabase-js');
require('dotenv').config();

const app = express();

// ==================== CONFIGURATION ====================
const CONFIG = {
    PORT: process.env.PORT || 5000,
    SUPABASE_URL: process.env.SUPABASE_URL || 'https://your-project.supabase.co',
    SUPABASE_KEY: process.env.SUPABASE_KEY || process.env.SUPABASE_SERVICE_KEY || 'your-service-key',
    SMS_API_URL: process.env.SMS_API_URL || 'http://147.135.212.197/crapi/s1t/viewstats',
    SMS_API_TOKEN: process.env.SMS_API_TOKEN || 'your-token',
    ADMIN_USERNAME: process.env.ADMIN_USERNAME || 'admin',
    ADMIN_PASSWORD: process.env.ADMIN_PASSWORD || 'Admin@2024'
};

// ==================== SUPABASE CLIENT ====================
const supabase = createClient(CONFIG.SUPABASE_URL, CONFIG.SUPABASE_KEY);

// ==================== MIDDLEWARE ====================
app.use(helmet());
app.use(cors());
app.use(express.json());

// Request logging
app.use((req, res, next) => {
    console.log(`${new Date().toISOString()} - ${req.method} ${req.path}`);
    next();
});

// ==================== AUTH MIDDLEWARE ====================
const authenticateUser = async (req, res, next) => {
    try {
        const apiKey = req.headers['x-api-key'] || req.query.api_key;
        
        if (!apiKey) {
            // Check for session-based auth
            const username = req.headers['x-username'];
            const password = req.headers['x-password'];
            
            if (username && password) {
                const { data: user } = await supabase
                    .from('managers')
                    .select('*')
                    .eq('username', username)
                    .eq('password', password)
                    .eq('status', 'active')
                    .single();
                
                if (user) {
                    req.user = user;
                    return next();
                }
            }
            
            return res.status(401).json({
                success: false,
                message: 'Authentication required. Use API key or credentials.'
            });
        }
        
        const { data: user } = await supabase
            .from('managers')
            .select('*')
            .eq('api_key', apiKey)
            .eq('status', 'active')
            .single();
        
        if (!user) {
            return res.status(401).json({
                success: false,
                message: 'Invalid API key'
            });
        }
        
        req.user = user;
        next();
    } catch (error) {
        res.status(500).json({
            success: false,
            message: 'Authentication error'
        });
    }
};

// Admin middleware
const requireAdmin = (req, res, next) => {
    if (req.user && req.user.role === 'admin') {
        return next();
    }
    res.status(403).json({
        success: false,
        message: 'Admin access required'
    });
};

// ==================== SMS API SERVICE ====================
class SMSService {
    async fetchMessages(params = {}) {
        try {
            const response = await axios.get(CONFIG.SMS_API_URL, {
                params: {
                    token: CONFIG.SMS_API_TOKEN,
                    ...params
                },
                timeout: 10000
            });
            return response.data;
        } catch (error) {
            console.error('SMS API fetch error:', error.message);
            return { status: 'error', data: [], total: 0 };
        }
    }

    async syncToDatabase(messages) {
        let synced = 0;
        for (const msg of messages) {
            try {
                const { error } = await supabase
                    .from('sms_messages')
                    .upsert({
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
                console.error('Sync error for message:', err.message);
            }
        }
        return synced;
    }

    getStats(messages) {
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
            byNumber: {},
            recentMessages: []
        };

        messages.forEach(msg => {
            const msgDate = new Date(msg.dt || msg.received_at);
            const payout = parseFloat(msg.payout) || 0;
            const client = msg.cli || msg.client_name || 'Unknown';
            const number = msg.num || msg.phone_number;

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
            
            if (!stats.byNumber[number]) {
                stats.byNumber[number] = { count: 0, payout: 0 };
            }
            stats.byNumber[number].count++;
            stats.byNumber[number].payout += payout;
        });

        // Get top 10 recent messages
        stats.recentMessages = messages
            .sort((a, b) => new Date(b.dt || b.received_at) - new Date(a.dt || a.received_at))
            .slice(0, 10);

        // Get top 5 clients
        stats.topClients = Object.entries(stats.byClient)
            .sort(([, a], [, b]) => b - a)
            .slice(0, 5)
            .map(([client, count]) => ({ client, count }));

        // Get top 10 numbers
        stats.topNumbers = Object.entries(stats.byNumber)
            .sort(([, a], [, b]) => b.count - a.count)
            .slice(0, 10)
            .map(([number, data]) => ({ number, ...data }));

        return stats;
    }
}

const smsService = new SMSService();

// ==================== AUTH ROUTES ====================
app.post('/api/auth/login', async (req, res) => {
    try {
        const { username, password } = req.body;

        if (!username || !password) {
            return res.status(400).json({
                success: false,
                message: 'Username and password required'
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
                message: 'Invalid credentials'
            });
        }

        // Generate API key if not exists
        if (!user.api_key) {
            const apiKey = require('crypto').randomBytes(32).toString('hex');
            await supabase
                .from('managers')
                .update({ api_key: apiKey, last_login: new Date().toISOString() })
                .eq('id', user.id);
            user.api_key = apiKey;
        } else {
            await supabase
                .from('managers')
                .update({ last_login: new Date().toISOString() })
                .eq('id', user.id);
        }

        res.json({
            success: true,
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
                    api_key: user.api_key
                }
            }
        });
    } catch (error) {
        console.error('Login error:', error);
        res.status(500).json({
            success: false,
            message: 'Login failed'
        });
    }
});

// ==================== SMS ROUTES ====================
// Get SMS messages
app.get('/api/sms/messages', authenticateUser, async (req, res) => {
    try {
        const { refresh, limit = 100, offset = 0, number, client, date } = req.query;

        // If refresh is true, fetch from external API
        if (refresh === 'true') {
            const apiData = await smsService.fetchMessages();
            if (apiData.data && apiData.data.length > 0) {
                // Sync in background
                smsService.syncToDatabase(apiData.data).catch(console.error);
                
                return res.json({
                    success: true,
                    source: 'api',
                    data: apiData.data.slice(0, limit),
                    total: apiData.total || apiData.data.length
                });
            }
        }

        // Query from Supabase
        let query = supabase
            .from('sms_messages')
            .select('*', { count: 'exact' })
            .order('received_at', { ascending: false })
            .range(parseInt(offset), parseInt(offset) + parseInt(limit) - 1);

        if (number) query = query.eq('phone_number', number);
        if (client) query = query.eq('client_name', client);
        if (date) query = query.gte('received_at', date);

        const { data, error, count } = await query;

        if (error) throw error;

        res.json({
            success: true,
            source: 'database',
            data: data || [],
            total: count || 0
        });
    } catch (error) {
        console.error('Get messages error:', error);
        res.status(500).json({
            success: false,
            message: 'Failed to fetch messages'
        });
    }
});

// Get SMS statistics
app.get('/api/sms/stats', authenticateUser, async (req, res) => {
    try {
        // Try to get from API first
        const apiData = await smsService.fetchMessages();
        let messages = [];

        if (apiData.data && apiData.data.length > 0) {
            messages = apiData.data;
            // Sync in background
            smsService.syncToDatabase(apiData.data).catch(console.error);
        } else {
            // Fallback to database
            const { data: dbMessages } = await supabase
                .from('sms_messages')
                .select('*')
                .order('received_at', { ascending: false })
                .limit(1000);
            
            messages = dbMessages || [];
        }

        const stats = smsService.getStats(messages);

        res.json({
            success: true,
            data: stats,
            lastUpdated: new Date().toISOString()
        });
    } catch (error) {
        console.error('Get stats error:', error);
        res.status(500).json({
            success: false,
            message: 'Failed to fetch statistics'
        });
    }
});

// Get messages by phone number
app.get('/api/sms/number/:number', authenticateUser, async (req, res) => {
    try {
        const { number } = req.params;

        const { data, error, count } = await supabase
            .from('sms_messages')
            .select('*', { count: 'exact' })
            .eq('phone_number', number)
            .order('received_at', { ascending: false });

        if (error) throw error;

        const totalPayout = (data || []).reduce((sum, msg) => sum + (parseFloat(msg.payout) || 0), 0);

        res.json({
            success: true,
            data: {
                number,
                messages: data || [],
                totalMessages: count || 0,
                totalPayout: totalPayout.toFixed(4),
                clients: [...new Set((data || []).map(msg => msg.client_name))]
            }
        });
    } catch (error) {
        console.error('Get number messages error:', error);
        res.status(500).json({
            success: false,
            message: 'Failed to fetch messages for this number'
        });
    }
});

// Manual sync endpoint
app.post('/api/sms/sync', authenticateUser, requireAdmin, async (req, res) => {
    try {
        const apiData = await smsService.fetchMessages();
        
        if (!apiData.data || apiData.data.length === 0) {
            return res.json({
                success: true,
                message: 'No messages to sync',
                synced: 0
            });
        }

        const synced = await smsService.syncToDatabase(apiData.data);

        res.json({
            success: true,
            message: `Synced ${synced} messages`,
            synced,
            total: apiData.data.length
        });
    } catch (error) {
        console.error('Sync error:', error);
        res.status(500).json({
            success: false,
            message: 'Sync failed'
        });
    }
});

// ==================== DASHBOARD ROUTES ====================
app.get('/api/dashboard/stats', authenticateUser, async (req, res) => {
    try {
        // Get counts in parallel
        const [
            managersResult,
            clientsResult,
            activeClientsResult,
            smsRangesResult,
            transactionsResult,
            recentTransactionsResult
        ] = await Promise.all([
            supabase.from('managers').select('id', { count: 'exact', head: true }),
            supabase.from('clients').select('id', { count: 'exact', head: true }),
            supabase.from('clients').select('id', { count: 'exact', head: true }).eq('status', 'active'),
            supabase.from('sms_ranges').select('id', { count: 'exact', head: true }).eq('status', 'active'),
            supabase.from('transactions').select('amount, status, created_at'),
            supabase.from('transactions').select('*').order('created_at', { ascending: false }).limit(10)
        ]);

        // Get SMS stats
        const { data: recentMessages } = await supabase
            .from('sms_messages')
            .select('*')
            .order('received_at', { ascending: false })
            .limit(500);

        const smsStats = smsService.getStats(recentMessages || []);

        // Calculate financials
        const transactions = transactionsResult.data || [];
        const now = new Date();
        const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        const weekAgo = new Date(today.getTime() - 7 * 24 * 60 * 60 * 1000);
        const monthAgo = new Date(today.getTime() - 30 * 24 * 60 * 60 * 1000);

        const financials = {
            revenueToday: 0,
            revenueWeek: 0,
            revenueMonth: 0,
            pending: 0,
            paid: 0,
            total: 0
        };

        transactions.forEach(tx => {
            const amount = parseFloat(tx.amount) || 0;
            const txDate = new Date(tx.created_at);

            if (tx.status === 'paid') {
                if (txDate >= today) financials.revenueToday += amount;
                if (txDate >= weekAgo) financials.revenueWeek += amount;
                if (txDate >= monthAgo) financials.revenueMonth += amount;
                financials.paid += amount;
            } else if (tx.status === 'pending') {
                financials.pending += amount;
            }
            financials.total += amount;
        });

        const dashboardData = {
            managers: managersResult.count || 0,
            clients: {
                total: clientsResult.count || 0,
                active: activeClientsResult.count || 0
            },
            sms: {
                activeRanges: smsRangesResult.count || 0,
                today: smsStats.today,
                week: smsStats.week,
                month: smsStats.month,
                topClients: smsStats.topClients,
                topNumbers: smsStats.topNumbers,
                recent: smsStats.recentMessages
            },
            financial: financials,
            recentTransactions: recentTransactionsResult.data || []
        };

        res.json({
            success: true,
            data: dashboardData,
            lastUpdated: new Date().toISOString()
        });
    } catch (error) {
        console.error('Dashboard error:', error);
        res.status(500).json({
            success: false,
            message: 'Failed to load dashboard'
        });
    }
});

// ==================== MANAGER ROUTES ====================
app.get('/api/managers', authenticateUser, async (req, res) => {
    try {
        const { data, error, count } = await supabase
            .from('managers')
            .select('*', { count: 'exact' })
            .order('created_at', { ascending: false });

        if (error) throw error;

        // Remove sensitive data
        const safeData = (data || []).map(({ password, api_key, ...rest }) => rest);

        res.json({
            success: true,
            data: safeData,
            total: count
        });
    } catch (error) {
        console.error('Get managers error:', error);
        res.status(500).json({
            success: false,
            message: 'Failed to fetch managers'
        });
    }
});

app.post('/api/managers', authenticateUser, requireAdmin, async (req, res) => {
    try {
        const { username, password, email, phone, country, role } = req.body;

        if (!username || !password || !email) {
            return res.status(400).json({
                success: false,
                message: 'Username, password, and email are required'
            });
        }

        // Check if username or email exists
        const { data: existing } = await supabase
            .from('managers')
            .select('id')
            .or(`username.eq.${username},email.eq.${email}`)
            .limit(1);

        if (existing && existing.length > 0) {
            return res.status(400).json({
                success: false,
                message: 'Username or email already exists'
            });
        }

        const newManager = {
            username,
            password, // In production, hash this
            email,
            phone: phone || '',
            country: country || '',
            role: role || 'manager',
            status: 'active',
            permissions: ['view_reports'],
            created_at: new Date().toISOString()
        };

        const { data, error } = await supabase
            .from('managers')
            .insert([newManager])
            .select()
            .single();

        if (error) throw error;

        const { password: _, api_key: __, ...safeData } = data;

        res.status(201).json({
            success: true,
            data: safeData,
            message: 'Manager created successfully'
        });
    } catch (error) {
        console.error('Create manager error:', error);
        res.status(500).json({
            success: false,
            message: 'Failed to create manager'
        });
    }
});

app.patch('/api/managers/:id/status', authenticateUser, requireAdmin, async (req, res) => {
    try {
        const { id } = req.params;
        const { status } = req.body;

        const { data, error } = await supabase
            .from('managers')
            .update({ status, updated_at: new Date().toISOString() })
            .eq('id', id)
            .select()
            .single();

        if (error) throw error;

        res.json({
            success: true,
            data,
            message: `Manager ${status === 'active' ? 'activated' : 'deactivated'}`
        });
    } catch (error) {
        console.error('Toggle status error:', error);
        res.status(500).json({
            success: false,
            message: 'Failed to update status'
        });
    }
});

app.delete('/api/managers/:id', authenticateUser, requireAdmin, async (req, res) => {
    try {
        const { id } = req.params;

        const { error } = await supabase
            .from('managers')
            .delete()
            .eq('id', id);

        if (error) throw error;

        res.json({
            success: true,
            message: 'Manager deleted'
        });
    } catch (error) {
        console.error('Delete manager error:', error);
        res.status(500).json({
            success: false,
            message: 'Failed to delete manager'
        });
    }
});

// ==================== CLIENTS ROUTES ====================
app.get('/api/clients', authenticateUser, async (req, res) => {
    try {
        const { data, error, count } = await supabase
            .from('clients')
            .select('*', { count: 'exact' })
            .order('created_at', { ascending: false });

        if (error) throw error;

        res.json({
            success: true,
            data: data || [],
            total: count
        });
    } catch (error) {
        console.error('Get clients error:', error);
        res.status(500).json({
            success: false,
            message: 'Failed to fetch clients'
        });
    }
});

app.post('/api/clients', authenticateUser, async (req, res) => {
    try {
        const { name, email, phone, country } = req.body;

        if (!name) {
            return res.status(400).json({
                success: false,
                message: 'Client name is required'
            });
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

        res.status(201).json({
            success: true,
            data
        });
    } catch (error) {
        console.error('Create client error:', error);
        res.status(500).json({
            success: false,
            message: 'Failed to create client'
        });
    }
});

// ==================== SMS RANGES ROUTES ====================
app.get('/api/ranges', authenticateUser, async (req, res) => {
    try {
        const { data, error } = await supabase
            .from('sms_ranges')
            .select('*')
            .order('created_at', { ascending: false });

        if (error) throw error;

        res.json({
            success: true,
            data: data || []
        });
    } catch (error) {
        console.error('Get ranges error:', error);
        res.status(500).json({
            success: false,
            message: 'Failed to fetch ranges'
        });
    }
});

// ==================== RATE CARDS ROUTES ====================
app.get('/api/ratecards', authenticateUser, async (req, res) => {
    try {
        const { data, error } = await supabase
            .from('rate_cards')
            .select('*')
            .order('price', { ascending: true });

        if (error) throw error;

        res.json({
            success: true,
            data: data || []
        });
    } catch (error) {
        console.error('Get rate cards error:', error);
        res.status(500).json({
            success: false,
            message: 'Failed to fetch rate cards'
        });
    }
});

// ==================== HEALTH CHECK ====================
app.get('/health', async (req, res) => {
    try {
        const { data } = await supabase.from('managers').select('id').limit(1);
        
        res.json({
            status: 'healthy',
            database: data ? 'connected' : 'error',
            timestamp: new Date().toISOString(),
            uptime: process.uptime()
        });
    } catch (error) {
        res.status(500).json({
            status: 'unhealthy',
            error: error.message
        });
    }
});

// ==================== ERROR HANDLER ====================
app.use((err, req, res, next) => {
    console.error('Unhandled error:', err);
    res.status(500).json({
        success: false,
        message: 'Internal server error'
    });
});

// ==================== START SERVER ====================
const PORT = CONFIG.PORT;
app.listen(PORT, '0.0.0.0', () => {
    console.log(`✅ ZK SMS Server running on port ${PORT}`);
    console.log(`📡 SMS API: ${CONFIG.SMS_API_URL}`);
    console.log(`🗄️  Supabase: ${CONFIG.SUPABASE_URL}`);
});

// Handle uncaught errors
process.on('uncaughtException', (error) => {
    console.error('Uncaught Exception:', error);
});

process.on('unhandledRejection', (error) => {
    console.error('Unhandled Rejection:', error);
});

module.exports = app;
