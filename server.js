const express = require('express');
const cors = require('cors');
const axios = require('axios');
const crypto = require('crypto');
const { createClient } = require('@supabase/supabase-js');

// HARDCODE the values as fallback
const SUPABASE_URL = process.env.SUPABASE_URL || 'https://xwfdjxqrwkimugsxkvnj.supabase.co';
const SUPABASE_KEY = process.env.SUPABASE_KEY || 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inh3ZmRqeHFyd2tpbXVnc3hrdm5qIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4MDA2MzMxOSwiZXhwIjoyMDk1NjM5MzE5fQ.CGAzuO6x6hhzaQNHz29NBlJJyvtc8laD5cDLRjfr6nM';
const SMS_API_URL = process.env.SMS_API_URL || 'http://147.135.212.197/crapi/s1t/viewstats';
const SMS_API_TOKEN = process.env.SMS_API_TOKEN || 'R1BTQzRSQl93cpl-QW2USn-UUkSCcW9mg4x4WlV0bYBrkHd0clSY';
const PORT = process.env.PORT || 5000;

console.log('Starting with config:');
console.log('SUPABASE_URL:', SUPABASE_URL ? 'SET' : 'MISSING');
console.log('SUPABASE_KEY:', SUPABASE_KEY ? 'SET' : 'MISSING');
console.log('SMS_API_URL:', SMS_API_URL);
console.log('PORT:', PORT);

const supabase = createClient(SUPABASE_URL, SUPABASE_KEY);
const app = express();

app.use(cors());
app.use(express.json());

// Auth middleware
const auth = async (req, res, next) => {
  const key = req.headers['x-api-key'] || req.query.api_key;
  if (!key) return res.status(401).json({ error: 'API key required' });
  
  const { data: user } = await supabase.from('managers').select('*').eq('api_key', key).single();
  if (!user) return res.status(401).json({ error: 'Invalid key' });
  req.user = user;
  next();
};

// Health check
app.get('/', (req, res) => res.json({ status: 'ok', time: new Date() }));
app.get('/health', async (req, res) => {
  try {
    const { data } = await supabase.from('managers').select('id').limit(1);
    res.json({ status: 'healthy', db: data ? 'connected' : 'empty' });
  } catch (e) {
    res.json({ status: 'degraded', error: e.message });
  }
});

// Login
app.post('/api/auth/login', async (req, res) => {
  try {
    const { username, password } = req.body;
    const { data: user } = await supabase.from('managers').select('*').eq('username', username).eq('password', password).single();
    
    if (!user) return res.json({ success: false, message: 'Invalid credentials' });
    
    if (!user.api_key) {
      user.api_key = crypto.randomBytes(32).toString('hex');
      await supabase.from('managers').update({ api_key: user.api_key }).eq('id', user.id);
    }
    
    await supabase.from('managers').update({ last_login: new Date() }).eq('id', user.id);
    res.json({ success: true, data: { user } });
  } catch (e) {
    res.json({ success: false, message: e.message });
  }
});

// Get SMS from external API
app.get('/api/sms/live', auth, async (req, res) => {
  try {
    const response = await axios.get(SMS_API_URL, { params: { token: SMS_API_TOKEN } });
    res.json({ success: true, data: response.data });
  } catch (e) {
    res.json({ success: false, message: e.message });
  }
});

// Get SMS stats
app.get('/api/sms/stats', auth, async (req, res) => {
  try {
    const response = await axios.get(SMS_API_URL, { params: { token: SMS_API_TOKEN } });
    const messages = response.data?.data || [];
    res.json({ success: true, total: messages.length, recent: messages.slice(0, 10) });
  } catch (e) {
    res.json({ success: false, message: e.message });
  }
});

// Dashboard
app.get('/api/dashboard', auth, async (req, res) => {
  try {
    const { data: managers } = await supabase.from('managers').select('id', { count: 'exact', head: true });
    const { data: clients } = await supabase.from('clients').select('id', { count: 'exact', head: true });
    const response = await axios.get(SMS_API_URL, { params: { token: SMS_API_TOKEN } });
    
    res.json({
      success: true,
      data: {
        managers: managers?.count || 0,
        clients: clients?.count || 0,
        smsTotal: response.data?.data?.length || 0
      }
    });
  } catch (e) {
    res.json({ success: false, message: e.message });
  }
});

// Managers
app.get('/api/managers', auth, async (req, res) => {
  const { data } = await supabase.from('managers').select('id,username,email,phone,country,role,status,created_at');
  res.json({ success: true, data });
});

// Clients
app.get('/api/clients', auth, async (req, res) => {
  const { data } = await supabase.from('clients').select('*');
  res.json({ success: true, data });
});

// Sync
app.post('/api/sync', auth, async (req, res) => {
  try {
    const response = await axios.get(SMS_API_URL, { params: { token: SMS_API_TOKEN } });
    const messages = response.data?.data || [];
    let count = 0;
    
    for (const msg of messages) {
      const { error } = await supabase.from('sms_messages').upsert({
        phone_number: msg.num, client_name: msg.cli, message: msg.message,
        payout: parseFloat(msg.payout) || 0, received_at: msg.dt
      }, { onConflict: 'phone_number,received_at', ignoreDuplicates: true });
      if (!error) count++;
    }
    
    res.json({ success: true, synced: count, total: messages.length });
  } catch (e) {
    res.json({ success: false, message: e.message });
  }
});

app.listen(PORT, '0.0.0.0', () => console.log(`✅ Server running on port ${PORT}`));
