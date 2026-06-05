const smsApi = require('../utils/smsApi');
const { supabase } = require('../supabase/client');

// Get received messages
exports.getReceivedMessages = async (req, res) => {
    try {
        const { date, number, client } = req.query;
        
        // Try to get from Supabase first
        let query = supabase
            .from('sms_messages')
            .select('*')
            .order('received_at', { ascending: false })
            .limit(100);

        if (date) {
            query = query.gte('received_at', date);
        }
        if (number) {
            query = query.eq('phone_number', number);
        }
        if (client) {
            query = query.eq('client_name', client);
        }

        const { data: localMessages, error } = await query;

        // If no local data or force refresh, fetch from API
        if (!localMessages || localMessages.length === 0 || req.query.refresh === 'true') {
            const apiData = await smsApi.getReceivedMessages(req.query);
            
            // Sync to Supabase in background
            smsApi.syncMessagesToSupabase(supabase).catch(console.error);

            return res.json({
                success: true,
                data: apiData.data || [],
                total: apiData.total || 0
            });
        }

        res.json({
            success: true,
            data: localMessages,
            total: localMessages.length
        });
    } catch (error) {
        res.status(500).json({
            success: false,
            message: error.message
        });
    }
};

// Get SMS statistics
exports.getSMSStats = async (req, res) => {
    try {
        // Get stats from your API
        const apiStats = await smsApi.getStatistics();

        // Get additional stats from Supabase
        const { data: totalMessages } = await supabase
            .from('sms_messages')
            .select('id', { count: 'exact' });

        const { data: uniqueNumbers } = await supabase
            .from('sms_messages')
            .select('phone_number');

        const uniqueNumberSet = new Set(uniqueNumbers?.map(n => n.phone_number) || []);

        const stats = {
            ...apiStats,
            totalMessagesInDB: totalMessages?.length || 0,
            uniqueNumbers: uniqueNumberSet.size,
            lastSync: new Date().toISOString()
        };

        res.json({
            success: true,
            data: stats
        });
    } catch (error) {
        res.status(500).json({
            success: false,
            message: error.message
        });
    }
};

// Get messages by phone number
exports.getMessagesByNumber = async (req, res) => {
    try {
        const { number } = req.params;

        const { data, error } = await supabase
            .from('sms_messages')
            .select('*')
            .eq('phone_number', number)
            .order('received_at', { ascending: false });

        if (error) throw error;

        // Calculate stats for this number
        const stats = {
            totalMessages: data.length,
            totalPayout: data.reduce((sum, msg) => sum + (parseFloat(msg.payout) || 0), 0),
            clients: [...new Set(data.map(msg => msg.client_name))],
            lastMessage: data[0]?.received_at
        };

        res.json({
            success: true,
            data: {
                messages: data,
                stats
            }
        });
    } catch (error) {
        res.status(500).json({
            success: false,
            message: error.message
        });
    }
};

// Sync messages from your API to Supabase
exports.syncMessages = async (req, res) => {
    try {
        const result = await smsApi.syncMessagesToSupabase(supabase);
        
        res.json({
            success: true,
            message: `Synced ${result.synced} messages`,
            data: result
        });
    } catch (error) {
        res.status(500).json({
            success: false,
            message: error.message
        });
    }
};

// Get messages by client
exports.getMessagesByClient = async (req, res) => {
    try {
        const { client } = req.params;

        const { data, error } = await supabase
            .from('sms_messages')
            .select('*')
            .eq('client_name', client)
            .order('received_at', { ascending: false })
            .limit(100);

        if (error) throw error;

        res.json({
            success: true,
            data,
            total: data.length
        });
    } catch (error) {
        res.status(500).json({
            success: false,
            message: error.message
        });
    }
};
