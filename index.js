const express = require('express');
const cors = require('cors');
const axios = require('axios');
const crypto = require('crypto');
const { createClient } = require('@supabase/supabase-js');

const app = express();
app.use(cors());
app.use(express.json());

const supabase = createClient(
  process.env.SUPABASE_URL || '',
  process.env.SUPABASE_KEY || ''
);

const SMS_URL = process.env.SMS_API_URL || 'http://147.135.212.197/crapi/s1t/viewstats';
const SMS_TOKEN = process.env.SMS_API_TOKEN || '';

// Auth
app.use('/api', async (req, res, next) => {
  if (req.path === '/auth/login') return next();
  const key = req.headers['x-api-key'] || req.query.api_key;
  if (!key) return res.status(401).json({ error: 'API key required' });
  const { data: user } = await supabase.from('managers').select('*').eq('api_key', key).single();
  if (!user) return res.status(401).json({ error: 'Invalid key' });
  req.user = user;
  next();
});

app.get('/', (req, res) => res.json({ status: 'ok' }));
app.get('/health', (req, res) => res.json({ status: 'ok' }));

app.post('/api/auth/login', async (req, res) => {
  try {
    const { username, password } = req.body;
    const { data: user, error } = await supabase
      .from('managers')
      .select('*')
      .eq('username', username)
      .eq('password', password)
      .single();
    
    if (error || !user) {
      return res.json({ success: false, message: 'Invalid credentials' });
    }
    
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

app.get('/api/sms', async (req, res) => {
  try {
    const response = await axios.get(SMS_URL, { params: { token: SMS_TOKEN } });
    res.json({ success: true, data: response.data });
  } catch (e) {
    res.json({ success: false, message: e.message });
  }
});

app.get('/api/sms/stats', async (req, res) => {
  try {
    const response = await axios.get(SMS_URL, { params: { token: SMS_TOKEN } });
    const messages = response.data?.data || [];
    res.json({ success: true, total: messages.length, messages: messages.slice(0, 20) });
  } catch (e) {
    res.json({ success: false, message: e.message });
  }
});

app.get('/api/dashboard', async (req, res) => {
  try {
    const response = await axios.get(SMS_URL, { params: { token: SMS_TOKEN } });
    res.json({ 
      success: true, 
      data: {
        smsTotal: response.data?.data?.length || 0,
        smsRecent: response.data?.data?.slice(0, 10) || []
      }
    });
  } catch (e) {
    res.json({ success: false, message: e.message });
  }
});

app.get('/api/managers', async (req, res) => {
  const { data } = await supabase.from('managers').select('id,username,email,phone,country,role,status');
  res.json({ success: true, data });
});

app.get('/api/clients', async (req, res) => {
  const { data } = await supabase.from('clients').select('*');
  res.json({ success: true, data });
});

app.post('/api/sync', async (req, res) => {
  try {
    const response = await axios.get(SMS_URL, { params: { token: SMS_TOKEN } });
    const messages = response.data?.data || [];
    let count = 0;
    
    for (const msg of messages) {
      const { error } = await supabase.from('sms_messages').upsert({
        phone_number: msg.num,
        client_name: msg.cli,
        message: msg.message,
        payout: parseFloat(msg.payout) || 0,
        received_at: msg.dt
      }, { onConflict: 'phone_number,received_at', ignoreDuplicates: true });
      if (!error) count++;
    }
    
    res.json({ success: true, synced: count, total: messages.length });
  } catch (e) {
    res.json({ success: false, message: e.message });
  }
});

const PORT = process.env.PORT || 5000;
app.listen(PORT, '0.0.0.0', () => console.log(`Server running on port ${PORT}`));
