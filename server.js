// ZK SMS - Complete Server (No External Routes)
const express = require('express');
const cors = require('cors');
const axios = require('axios');
const crypto = require('crypto');
const { createClient } = require('@supabase/supabase-js');

const app = express();
app.use(cors());
app.use(express.json());

// Config from env
const SUPABASE_URL = process.env.SUPABASE_URL;
const SUPABASE_KEY = process.env.SUPABASE_KEY;
const SMS_API_URL = process.env.SMS_API_URL || 'http://147.135.212.197/crapi/s1t/viewstats';
const SMS_API_TOKEN = process.env.SMS_API_TOKEN;
const PORT = process.env.PORT || 5000;

const supabase = createClient(SUPABASE_URL, SUPABASE_KEY);

// Auth check
const checkAuth = async (req, res, next) => {
  const key = req.headers['x-api-key'] || req.query.api_key;
  if (!key) return res.status(401).json({ error: 'API key required' });
  const { data: user } = await supabase.from('managers').select('*').eq('api_key', key).single();
  if (!user) return res.status(401).json({ error: 'Invalid key' });
  req.user = user;
  next();
};

// ============ ROUTES ============

// Health
app.get('/health', (req, res) => {
  res.json({ status: 'ok', time: new Date() });
});

// Login
app.post('/api/auth/login', async (req, res) => {
  const { username, password } = req.body;
  const { data: user } = await supabase.from('managers').select('*').eq('username', username).eq('password', password).single();
  if (!user) return res.json({ success: false, message: 'Invalid credentials' });
  
  if (!user.api_key) {
    user.api_key = crypto.randomBytes(32).toString('hex');
    await supabase.from('managers').update({ api_key: user.api_key }).eq('id', user.id);
  }
  
  await supabase.from('managers').update({ last_login: new Date() }).eq('id', user.id);
  res.json({ success: true, data: { user } });
});

// Get SMS messages
app.get('/api/sms/messages', checkAuth, async (req, res) => {
  const { data } = await supabase.from('sms_messages').select('*').order('received_at', { ascending: false }).limit(100);
  res.json({ success: true, data });
});

// Live SMS from API
app.get('/api/sms/live', checkAuth, async (req, res) => {
  const response = await axios.get(SMS_API_URL, { params: { token: SMS_API_TOKEN } });
  res.json({ success: true, data: response.data });
});

// SMS Stats
app.get('/api/sms/stats', checkAuth, async (req, res) => {
  const response = await axios.get(SMS_API_URL, { params: { token: SMS_API_TOKEN } });
  const messages = response.data?.data || [];
  const stats = { total: messages.length, today: 0, week: 0, month: 0 };
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  
  messages.forEach(m => {
    const d = new Date(m.dt);
    const p = parseFloat(m.payout) || 0;
    if (d >= today) stats.today += p;
    if (d >= new Date(today - 7*86400000)) stats.week += p;
    if (d >= new Date(today - 30*86400000)) stats.month += p;
  });
  
  res.json({ success: true, data: stats, recent: messages.slice(0, 10) });
});

// Dashboard
app.get('/api/dashboard', checkAuth, async (req, res) => {
  const [managers, clients, smsRes] = await Promise.all([
    supabase.from('managers').select('id', { count: 'exact', head: true }),
    supabase.from('clients').select('id', { count: 'exact', head: true }),
    axios.get(SMS_API_URL, { params: { token: SMS_API_TOKEN } }).catch(() => ({ data: { data: [] } }))
  ]);
  
  res.json({
    success: true,
    data: {
      managers: managers.count,
      clients: clients.count,
      smsToday: smsRes.data.data?.length || 0,
      recent: smsRes.data.data?.slice(0, 5) || []
    }
  });
});

// Managers CRUD
app.get('/api/managers', checkAuth, async (req, res) => {
  const { data } = await supabase.from('managers').select('id,username,email,phone,country,role,status,created_at');
  res.json({ success: true, data });
});

app.post('/api/managers', checkAuth, async (req, res) => {
  const { username, password, email, phone, country } = req.body;
  const { data } = await supabase.from('managers').insert([{ username, password, email, phone, country, role:'manager', status:'active' }]).select().single();
  res.json({ success: true, data });
});

// Clients
app.get('/api/clients', checkAuth, async (req, res) => {
  const { data } = await supabase.from('clients').select('*');
  res.json({ success: true, data });
});

// Sync
app.post('/api/sync', checkAuth, async (req, res) => {
  const response = await axios.get(SMS_API_URL, { params: { token: SMS_API_TOKEN } });
  const messages = response.data?.data || [];
  let synced = 0;
  
  for (const msg of messages) {
    const { error } = await supabase.from('sms_messages').upsert({
      phone_number: msg.num, client_name: msg.cli, message: msg.message,
      payout: parseFloat(msg.payout) || 0, received_at: msg.dt
    }, { onConflict: 'phone_number,received_at', ignoreDuplicates: true });
    if (!error) synced++;
  }
  
  res.json({ success: true, synced, total: messages.length });
});

// Start
app.listen(PORT, '0.0.0.0', () => {
  console.log(`✅ ZK SMS running on port ${PORT}`);
  console.log(`📡 SMS API: ${SMS_API_URL}`);
  console.log(`🗄️  Supabase: ${SUPABASE_URL ? 'connected' : 'missing'}`);
});
