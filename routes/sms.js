const express = require('express');
const router = express.Router();
const smsController = require('../controllers/smsController');
const { validateApiKey } = require('../controllers/authController');

// Public routes with API key
router.get('/received', validateApiKey, smsController.getReceivedMessages);
router.get('/stats', validateApiKey, smsController.getSMSStats);
router.get('/number/:number', validateApiKey, smsController.getMessagesByNumber);
router.get('/client/:client', validateApiKey, smsController.getMessagesByClient);
router.post('/sync', validateApiKey, smsController.syncMessages);

module.exports = router;
