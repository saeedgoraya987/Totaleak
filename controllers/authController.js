const { supabase } = require('../supabase/client');

// Simple session-based auth using Supabase
exports.login = async (req, res) => {
    try {
        const { username, password } = req.body;

        if (!username || !password) {
            return res.status(400).json({
                success: false,
                message: 'Username and password required'
            });
        }

        // Check user in Supabase
        const { data: user, error } = await supabase
            .from('managers')
            .select('*')
            .eq('username', username)
            .eq('password', password) // In production, hash passwords
            .eq('status', 'active')
            .single();

        if (error || !user) {
            return res.status(401).json({
                success: false,
                message: 'Invalid credentials'
            });
        }

        // Update last login
        await supabase
            .from('managers')
            .update({ last_login: new Date().toISOString() })
            .eq('id', user.id);

        // Generate simple API key if not exists
        if (!user.api_key) {
            const apiKey = require('crypto').randomBytes(32).toString('hex');
            await supabase
                .from('managers')
                .update({ api_key: apiKey })
                .eq('id', user.id);
            user.api_key = apiKey;
        }

        res.json({
            success: true,
            data: {
                user: {
                    id: user.id,
                    username: user.username,
                    email: user.email,
                    role: user.role,
                    api_key: user.api_key
                }
            }
        });
    } catch (error) {
        res.status(500).json({
            success: false,
            message: 'Login failed'
        });
    }
};

exports.validateApiKey = async (req, res, next) => {
    const apiKey = req.headers['x-api-key'] || req.query.api_key;

    if (!apiKey) {
        return res.status(401).json({
            success: false,
            message: 'API key required'
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
            message: 'Invalid API key'
        });
    }

    req.user = user;
    next();
};
