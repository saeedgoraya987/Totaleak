// ZK SMS Enterprise Backend - Complete Production Server
const express = require('express');
const cors = require('cors');
const axios = require('axios');
const { createClient } = require('@supabase/supabase-js');

// ==================== CONFIGURATION ====================
const SUPABASE_URL = process.env.SUPABASE_URL;
const SUPABASE_KEY = process.env.SUPABASE_KEY;
const SMS_API_URL = process.env.SMS_API_URL || 'http://147.135.212.197/crapi/s1t/viewstats';
const SMS_API_TOKEN = process.env.SMS_API_TOKEN;
const PORT = process.env.PORT || 5000;

// ==================== INITIALIZE SERVICES ====================
const supabase = createClient(SUPABASE_URL, SUPABASE_KEY);
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

        // Update API usage
        await supabase.from('api_usage').upsert({
            manager_id: user.id,
            endpoint: req.path,
            requests_count: 1,
            last_used: new Date().toISOString()
        }, {
            onConflict: 'manager_id,endpoint',
            count: 'requests_count + 1'
        });

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

// ==================== HEALTH CHECK ====================
app.get('/health', async (req, res) => {
    try {
        const startTime = Date.now();
        
        // Check database connection
        const { data: dbCheck } = await supabase.from('managers').select('id').limit(1);
        
        // Check SMS API
        let smsApiStatus = 'unknown';
        try {
            await axios.get(SMS_API_URL, { 
                params: { token: SMS_API_TOKEN },
                timeout: 5000 
            });
            smsApiStatus = 'online';
        } catch (e) {
            smsApiStatus = 'offline';
        }

        res.json({
            success: true,
            status: 'healthy',
            services: {
                database: dbCheck ? 'connected' : 'error',
                sms_api: smsApiStatus
            },
            uptime: process.uptime(),
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

        // Find user
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

        // Generate API key if not exists
        if (!user.api_key) {
            const crypto = require('crypto');
            const apiKey = crypto.randomBytes(32).toString('hex');
            
            await supabase
                .from('managers')
                .update({ api_key: apiKey })
                .eq('id', user.id);
            
            user.api_key = apiKey;
        }

        // Update last login
        await supabase
            .from('managers')
            .update({ last_login: new Date().toISOString() })
            .eq('id', user.id);

        // Log the login
        console.log(`User logged in: ${user.username} (${user.email})`);

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
            message: 'Login failed'
        });
    }
});

app.get('/api/auth/me', authenticate, async (req, res) => {
    res.json({
        success: true,
        data: {
            user: req.user
        }
    });
});

// ==================== SMS MESSAGES ROUTES ====================
// Get SMS messages from database
app.get('/api/sms/messages', authenticate, async (req, res) => {
    try {
        const { 
            limit = 100, 
            offset = 0, 
            number, 
            client, 
            startDate, 
            endDate,
            refresh = false 
        } = req.query;

        // If refresh is true, sync from external API first
        if (refresh === 'true') {
            try {
                const apiResponse = await axios.get(SMS_API_URL, {
                    params: { token: SMS_API_TOKEN }
                });

                if (apiResponse.data?.data) {
                    await syncMessagesToDB(apiResponse.data.data);
                }
            } catch (syncError) {
                console.error('Sync error:', syncError.message);
            }
        }

        // Build query
        let query = supabase
            .from('sms_messages')
            .select('*', { count: 'exact' })
            .order('received_at', { ascending: false })
            .range(parseInt(offset), parseInt(offset) + parseInt(limit) - 1);

        if (number) {
            query = query.eq('phone_number', number);
        }
        if (client) {
            query = query.eq('client_name', client);
        }
        if (startDate) {
            query = query.gte('received_at', startDate);
        }
        if (endDate) {
            query = query.lte('received_at', endDate);
        }

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

// Get messages from external API directly
app.get('/api/sms/messages/live', authenticate, async (req, res) => {
    try {
        const response = await axios.get(SMS_API_URL, {
            params: { token: SMS_API_TOKEN }
        });

        const messages = response.data?.data || [];

        res.json({
            success: true,
            source: 'api',
            data: messages,
            total: messages.length
        });
    } catch (error) {
        console.error('Live fetch error:', error);
        res.status(500).json({
            success: false,
            message: 'Failed to fetch live messages'
        });
    }
});

// Get messages by specific phone number
app.get('/api/sms/messages/number/:number', authenticate, async (req, res) => {
    try {
        const { number } = req.params;
        const { limit = 100 } = req.query;

        const { data, error, count } = await supabase
            .from('sms_messages')
            .select('*', { count: 'exact' })
            .eq('phone_number', number)
            .order('received_at', { ascending: false })
            .limit(parseInt(limit));

        if (error) throw error;

        // Calculate stats for this number
        const totalPayout = (data || []).reduce((sum, msg) => sum + (parseFloat(msg.payout) || 0), 0);
        const uniqueClients = [...new Set((data || []).map(msg => msg.client_name))];

        res.json({
            success: true,
            data: {
                number,
                messages: data || [],
                stats: {
                    totalMessages: count || 0,
                    totalPayout: totalPayout.toFixed(4),
                    uniqueClients,
                    lastMessage: data?.[0]?.received_at || null
                }
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

// Get messages by client
app.get('/api/sms/messages/client/:client', authenticate, async (req, res) => {
    try {
        const { client } = req.params;
        const { limit = 100 } = req.query;

        const { data, error, count } = await supabase
            .from('sms_messages')
            .select('*', { count: 'exact' })
            .eq('client_name', client)
            .order('received_at', { ascending: false })
            .limit(parseInt(limit));

        if (error) throw error;

        // Get unique numbers for this client
        const uniqueNumbers = [...new Set((data || []).map(msg => msg.phone_number))];

        res.json({
            success: true,
            data: {
                client,
                messages: data || [],
                stats: {
                    totalMessages: count || 0,
                    uniqueNumbers: uniqueNumbers.length,
                    numbers: uniqueNumbers.slice(0, 10) // Return first 10 numbers
                }
            }
        });
    } catch (error) {
        console.error('Get client messages error:', error);
        res.status(500).json({
            success: false,
            message: 'Failed to fetch client messages'
        });
    }
});

// ==================== SMS STATISTICS ====================
app.get('/api/sms/stats', authenticate, async (req, res) => {
    try {
        // Get messages from database for the last 30 days
        const thirtyDaysAgo = new Date();
        thirtyDaysAgo.setDate(thirtyDaysAgo.getDate() - 30);

        const { data: dbMessages } = await supabase
            .from('sms_messages')
            .select('*')
            .gte('received_at', thirtyDaysAgo.toISOString())
            .order('received_at', { ascending: false });

        // Try to get live data from external API
        let apiMessages = [];
        try {
            const apiResponse = await axios.get(SMS_API_URL, {
                params: { token: SMS_API_TOKEN },
                timeout: 5000
            });
            apiMessages = apiResponse.data?.data || [];
        } catch (apiError) {
            console.log('Could not fetch live API data:', apiError.message);
        }

        // Process stats
        const allMessages = [...(dbMessages || []), ...apiMessages];
        const stats = calculateSMSStats(allMessages);

        // Get top numbers
        const topNumbers = getTopNumbers(allMessages, 10);

        // Get top clients
        const topClients = getTopClients(allMessages, 5);

        // Get hourly breakdown for today
        const hourlyBreakdown = getHourlyBreakdown(allMessages);

        res.json({
            success: true,
            data: {
                overview: stats,
                topNumbers,
                topClients,
                hourlyBreakdown,
                lastUpdated: new Date().toISOString(),
                sources: {
                    database: dbMessages?.length || 0,
                    liveApi: apiMessages.length
                }
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

// ==================== SMS SYNC ====================
app.post('/api/sms/sync', authenticate, requireAdmin, async (req, res) => {
    try {
        const response = await axios.get(SMS_API_URL, {
            params: { token: SMS_API_TOKEN }
        });

        if (!response.data?.data) {
            return res.json({
                success: true,
                message: 'No messages to sync',
                synced: 0
            });
        }

        const synced = await syncMessagesToDB(response.data.data);

        // Log the sync
        await supabase.from('sms_logs').insert([{
            to_number: 'SYSTEM',
            message: `Synced ${synced} messages from external API`,
            status: 'synced',
            manager_id: req.user.id,
            created_at: new Date().toISOString()
        }]);

        res.json({
            success: true,
            message: `Successfully synced ${synced} messages`,
            data: {
                synced,
                total: response.data.data.length,
                timestamp: new Date().toISOString()
            }
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
app.get('/api/dashboard/stats', authenticate, async (req, res) => {
    try {
        // Fetch all required data in parallel
        const [
            managersResult,
            clientsResult,
            activeClientsResult,
            smsRangesResult,
            rateCardsResult,
            recentMessages,
            transactionsResult,
            smsLogsResult
        ] = await Promise.all([
            supabase.from('managers').select('id', { count: 'exact', head: true }),
            supabase.from('clients').select('id', { count: 'exact', head: true }),
            supabase.from('clients').select('id', { count: 'exact', head: true }).eq('status', 'active'),
            supabase.from('sms_ranges').select('*'),
            supabase.from('rate_cards').select('*').order('price', { ascending: true }),
            supabase.from('sms_messages').select('*').order('received_at', { ascending: false }).limit(50),
            supabase.from('transactions').select('*').order('created_at', { ascending: false }).limit(20),
            supabase.from('sms_logs').select('*').order('created_at', { ascending: false }).limit(20)
        ]);

        // Calculate SMS stats
        const smsStats = calculateSMSStats(recentMessages.data || []);

        // Calculate financial metrics
        const transactions = transactionsResult.data || [];
        const now = new Date();
        const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        const weekAgo = new Date(today.getTime() - 7 * 24 * 60 * 60 * 1000);
        const monthAgo = new Date(today.getTime() - 30 * 24 * 60 * 60 * 1000);

        const financials = {
            revenueToday: 0,
            revenueWeek: 0,
            revenueMonth: 0,
            totalPending: 0,
            totalPaid: 0,
            transactionCount: transactions.length
        };

        transactions.forEach(tx => {
            const amount = parseFloat(tx.amount) || 0;
            const txDate = new Date(tx.created_at);

            if (tx.status === 'paid') {
                if (txDate >= today) financials.revenueToday += amount;
                if (txDate >= weekAgo) financials.revenueWeek += amount;
                if (txDate >= monthAgo) financials.revenueMonth += amount;
                financials.totalPaid += amount;
            } else if (tx.status === 'pending') {
                financials.totalPending += amount;
            }
        });

        // Get system health metrics
        const apiUsage = await supabase
            .from('api_usage')
            .select('*')
            .order('last_used', { ascending: false })
            .limit(10);

        const dashboard = {
            overview: {
                totalManagers: managersResult.count || 0,
                totalClients: clientsResult.count || 0,
                activeClients: activeClientsResult.count || 0,
                activeSMSRanges: (smsRangesResult.data || []).filter(r => r.status === 'active').length,
                totalRateCards: (rateCardsResult.data || []).length
            },
            sms: {
                today: smsStats.today,
                week: smsStats.week,
                month: smsStats.month,
                allTime: smsStats.total,
                topClients: smsStats.topClients || [],
                recentMessages: recentMessages.data?.slice(0, 10) || []
            },
            financial: financials,
            system: {
                apiUsage: apiUsage.data || [],
                recentTransactions: transactions.slice(0, 10),
                recentLogs: smsLogsResult.data?.slice(0, 10) || [],
                uptime: process.uptime()
            },
            lastUpdated: new Date().toISOString()
        };

        res.json({
            success: true,
            data: dashboard
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
app.get('/api/managers', authenticate, async (req, res) => {
    try {
        const { status, role } = req.query;
        
        let query = supabase
            .from('managers')
            .select('*', { count: 'exact' })
            .order('created_at', { ascending: false });

        if (status) query = query.eq('status', status);
        if (role) query = query.eq('role', role);

        const { data, error, count } = await query;

        if (error) throw error;

        // Remove sensitive fields
        const safeData = (data || []).map(({ password, ...manager }) => manager);

        res.json({
            success: true,
            data: safeData,
            total: count || 0
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
        const { username, password, email, phone, country, role, permissions } = req.body;

        if (!username || !password || !email) {
            return res.status(400).json({
                success: false,
                message: 'Username, password, and email are required'
            });
        }

        // Check for existing user
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

        // Create new manager
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
                permissions: permissions || ['view_reports'],
                created_at: new Date().toISOString(),
                updated_at: new Date().toISOString()
            }])
            .select()
            .single();

        if (error) throw error;

        // Remove password from response
        const { password: pwd, ...safeData } = data;

        res.status(201).json({
            success: true,
            message: 'Manager created successfully',
            data: safeData
        });
    } catch (error) {
        console.error('Create manager error:', error);
        res.status(500).json({
            success: false,
            message: 'Failed to create manager'
        });
    }
});

app.put('/api/managers/:id', authenticate, requireAdmin, async (req, res) => {
    try {
        const { id } = req.params;
        const updates = req.body;

        // Don't allow password update through this endpoint
        delete updates.password;

        const { data, error } = await supabase
            .from('managers')
            .update({
                ...updates,
                updated_at: new Date().toISOString()
            })
            .eq('id', id)
            .select()
            .single();

        if (error) throw error;

        res.json({
            success: true,
            message: 'Manager updated successfully',
            data
        });
    } catch (error) {
        console.error('Update manager error:', error);
        res.status(500).json({
            success: false,
            message: 'Failed to update manager'
        });
    }
});

app.patch('/api/managers/:id/status', authenticate, requireAdmin, async (req, res) => {
    try {
        const { id } = req.params;
        const { status } = req.body;

        if (!status || !['active', 'inactive', 'suspended'].includes(status)) {
            return res.status(400).json({
                success: false,
                message: 'Valid status required (active, inactive, suspended)'
            });
        }

        const { data, error } = await supabase
            .from('managers')
            .update({ 
                status, 
                updated_at: new Date().toISOString() 
            })
            .eq('id', id)
            .select()
            .single();

        if (error) throw error;

        res.json({
            success: true,
            message: `Manager ${status === 'active' ? 'activated' : status}`,
            data
        });
    } catch (error) {
        console.error('Status update error:', error);
        res.status(500).json({
            success: false,
            message: 'Failed to update status'
        });
    }
});

app.delete('/api/managers/:id', authenticate, requireAdmin, async (req, res) => {
    try {
        const { id } = req.params;

        const { error } = await supabase
            .from('managers')
            .delete()
            .eq('id', id);

        if (error) throw error;

        res.json({
            success: true,
            message: 'Manager deleted successfully'
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
app.get('/api/clients', authenticate, async (req, res) => {
    try {
        const { status } = req.query;

        let query = supabase
            .from('clients')
            .select('*', { count: 'exact' })
            .order('created_at', { ascending: false });

        if (status) query = query.eq('status', status);

        const { data, error, count } = await query;

        if (error) throw error;

        res.json({
            success: true,
            data: data || [],
            total: count || 0
        });
    } catch (error) {
        console.error('Get clients error:', error);
        res.status(500).json({
            success: false,
            message: 'Failed to fetch clients'
        });
    }
});

app.post('/api/clients', authenticate, async (req, res) => {
    try {
        const { name, email, phone, country, balance } = req.body;

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
                balance: parseFloat(balance) || 0,
                status: 'active',
                manager_id: req.user.id,
                created_at: new Date().toISOString(),
                updated_at: new Date().toISOString()
            }])
            .select()
            .single();

        if (error) throw error;

        res.status(201).json({
            success: true,
            message: 'Client created successfully',
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
app.get('/api/sms-ranges', authenticate, async (req, res) => {
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
            message: 'Failed to fetch SMS ranges'
        });
    }
});

// ==================== RATE CARDS ROUTES ====================
app.get('/api/rate-cards', authenticate, async (req, res) => {
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

// ==================== TRANSACTIONS ROUTES ====================
app.get('/api/transactions', authenticate, async (req, res) => {
    try {
        const { status, type, client_id } = req.query;
        const limit = parseInt(req.query.limit) || 50;

        let query = supabase
            .from('transactions')
            .select('*', { count: 'exact' })
            .order('created_at', { ascending: false })
            .limit(limit);

        if (status) query = query.eq('status', status);
        if (type) query = query.eq('type', type);
        if (client_id) query = query.eq('client_id', client_id);

        const { data, error, count } = await query;

        if (error) throw error;

        res.json({
            success: true,
            data: data || [],
            total: count || 0
        });
    } catch (error) {
        console.error('Get transactions error:', error);
        res.status(500).json({
            success: false,
            message: 'Failed to fetch transactions'
        });
    }
});

// ==================== SMS LOGS ROUTES ====================
app.get('/api/sms-logs', authenticate, async (req, res) => {
    try {
        const { status } = req.query;
        const limit = parseInt(req.query.limit) || 50;

        let query = supabase
            .from('sms_logs')
            .select('*')
            .order('created_at', { ascending: false })
            .limit(limit);

        if (status) query = query.eq('status', status);

        const { data, error } = await query;

        if (error) throw error;

        res.json({
            success: true,
            data: data || []
        });
    } catch (error) {
        console.error('Get logs error:', error);
        res.status(500).json({
            success: false,
            message: 'Failed to fetch SMS logs'
        });
    }
});

// ==================== HELPER FUNCTIONS ====================
async function syncMessagesToDB(messages) {
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

function calculateSMSStats(messages) {
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

    return stats;
}

function getTopNumbers(messages, limit = 10) {
    const numberStats = {};

    messages.forEach(msg => {
        const number = msg.num || msg.phone_number;
        if (!numberStats[number]) {
            numberStats[number] = { count: 0, payout: 0 };
        }
        numberStats[number].count++;
        numberStats[number].payout += parseFloat(msg.payout) || 0;
    });

    return Object.entries(numberStats)
        .sort(([, a], [, b]) => b.count - a.count)
        .slice(0, limit)
        .map(([number, data]) => ({
            number,
            messages: data.count,
            totalPayout: data.payout.toFixed(4)
        }));
}

function getTopClients(messages, limit = 10) {
    const clientStats = {};

    messages.forEach(msg => {
        const client = msg.cli || msg.client_name || 'Unknown';
        clientStats[client] = (clientStats[client] || 0) + 1;
    });

    return Object.entries(clientStats)
        .sort(([, a], [, b]) => b - a)
        .slice(0, limit)
        .map(([client, count]) => ({ client, count }));
}

function getHourlyBreakdown(messages) {
    const hours = {};
    const today = new Date(new Date().setHours(0, 0, 0, 0));

    messages.forEach(msg => {
        const msgDate = new Date(msg.dt || msg.received_at);
        if (msgDate >= today) {
            const hour = msgDate.getHours();
            hours[hour] = (hours[hour] || 0) + 1;
        }
    });

    return Object.entries(hours).map(([hour, count]) => ({
        hour: `${hour}:00`,
        count
    }));
}

// ==================== ERROR HANDLER ====================
app.use((err, req, res, next) => {
    console.error('Unhandled error:', err);
    res.status(500).json({
        success: false,
        message: 'Internal server error',
        error: process.env.NODE_ENV === 'development' ? err.message : undefined
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

// Graceful shutdown
process.on('SIGTERM', () => {
    console.log('SIGTERM received. Shutting down gracefully...');
    process.exit(0);
});

process.on('SIGINT', () => {
    console.log('SIGINT received. Shutting down gracefully...');
    process.exit(0);
});

module.exports = app;
