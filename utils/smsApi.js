const axios = require('axios');

class SMSApiService {
    constructor() {
        this.baseURL = process.env.SMS_API_URL;
        this.token = process.env.SMS_API_TOKEN;
    }

    // Fetch received SMS messages from your API
    async getReceivedMessages(params = {}) {
        try {
            const response = await axios.get(this.baseURL, {
                params: {
                    token: this.token,
                    ...params // Additional filters like date range, number, etc.
                }
            });

            return response.data;
        } catch (error) {
            console.error('SMS API Error:', error.message);
            throw new Error('Failed to fetch SMS messages');
        }
    }

    // Get statistics
    async getStatistics() {
        try {
            const response = await axios.get(this.baseURL, {
                params: {
                    token: this.token
                }
            });

            return this.processStats(response.data);
        } catch (error) {
            console.error('Stats API Error:', error.message);
            throw new Error('Failed to fetch statistics');
        }
    }

    // Process and format stats
    processStats(data) {
        if (!data || !data.data) return null;

        const messages = data.data;
        const stats = {
            total: data.total || messages.length,
            todayMessages: 0,
            todayPayout: 0,
            weekMessages: 0,
            weekPayout: 0,
            monthMessages: 0,
            monthPayout: 0,
            byClient: {},
            byNumber: {},
            recentMessages: messages.slice(0, 50)
        };

        const now = new Date();
        const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        const weekAgo = new Date(today.getTime() - 7 * 24 * 60 * 60 * 1000);
        const monthAgo = new Date(today.getTime() - 30 * 24 * 60 * 60 * 1000);

        messages.forEach(msg => {
            const msgDate = new Date(msg.dt);
            const payout = parseFloat(msg.payout) || 0;

            // Today's stats
            if (msgDate >= today) {
                stats.todayMessages++;
                stats.todayPayout += payout;
            }

            // Week stats
            if (msgDate >= weekAgo) {
                stats.weekMessages++;
                stats.weekPayout += payout;
            }

            // Month stats
            if (msgDate >= monthAgo) {
                stats.monthMessages++;
                stats.monthPayout += payout;
            }

            // By client
            const client = msg.cli || 'Unknown';
            stats.byClient[client] = (stats.byClient[client] || 0) + 1;

            // By number
            const number = msg.num;
            if (!stats.byNumber[number]) {
                stats.byNumber[number] = {
                    count: 0,
                    payout: 0,
                    lastMessage: msgDate
                };
            }
            stats.byNumber[number].count++;
            stats.byNumber[number].payout += payout;
            if (msgDate > stats.byNumber[number].lastMessage) {
                stats.byNumber[number].lastMessage = msgDate;
            }
        });

        return stats;
    }

    // Sync messages to Supabase
    async syncMessagesToSupabase(supabase) {
        try {
            const data = await this.getReceivedMessages();
            
            if (!data || !data.data) return { synced: 0 };

            let syncedCount = 0;

            for (const msg of data.data) {
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
                        onConflict: 'phone_number,received_at'
                    });

                if (!error) syncedCount++;
            }

            return { synced: syncedCount, total: data.data.length };
        } catch (error) {
            console.error('Sync error:', error);
            throw error;
        }
    }
}

module.exports = new SMSApiService();
