/**
 * OpenClaw Bridge Server
 * 
 * Exposes Anthropic-compatible /v1/messages API
 * Routes to OpenClaw gateway for Claude subscription
 * 
 * Security: Requires BRIDGE_SECRET Bearer token for /v1/messages
 */

import express from 'express';
import { spawn } from 'child_process';
import crypto from 'crypto';
import { readFileSync } from 'fs';

const app = express();
app.use(express.json({ limit: '10mb' }));

const PORT = process.env.BRIDGE_PORT || 18802;

// Load bridge secret (required for auth)
let BRIDGE_SECRET = process.env.BRIDGE_SECRET || '';
const SECRET_FILE = process.env.BRIDGE_SECRET_FILE || '/home/node/.openclaw/secrets/bridge-secret.txt';
try {
  if (!BRIDGE_SECRET) {
    BRIDGE_SECRET = readFileSync(SECRET_FILE, 'utf-8').trim();
  }
} catch (e) {
  console.warn(`Warning: Could not load ${SECRET_FILE}, auth disabled`);
}

/**
 * Auth middleware
 */
function requireAuth(req, res, next) {
  // Skip auth if no secret configured (local dev)
  if (!BRIDGE_SECRET) {
    return next();
  }
  
  const authHeader = req.headers.authorization;
  if (!authHeader || !authHeader.startsWith('Bearer ')) {
    return res.status(401).json({
      error: { type: 'authentication_error', message: 'Missing Bearer token' }
    });
  }
  
  const token = authHeader.slice(7);
  if (token !== BRIDGE_SECRET) {
    return res.status(401).json({
      error: { type: 'authentication_error', message: 'Invalid token' }
    });
  }
  
  next();
}

/**
 * Health check
 */
app.get('/health', (req, res) => {
  res.json({ 
    status: 'ok', 
    service: 'openclaw-bridge',
    version: '0.1.0'
  });
});

/**
 * Anthropic-compatible Messages API
 * POST /v1/messages
 */
app.post('/v1/messages', requireAuth, async (req, res) => {
  const startTime = Date.now();
  
  try {
    const { model, messages, system, max_tokens, stream } = req.body;
    
    // Extract the user message (last user message)
    let userMessage = messages
      ?.filter(m => m.role === 'user')
      ?.pop()
      ?.content;
    
    // Handle content array format
    if (Array.isArray(userMessage)) {
      userMessage = userMessage
        .filter(c => c.type === 'text')
        .map(c => c.text)
        .join('\n');
    }
    
    if (!userMessage) {
      return res.status(400).json({
        error: { type: 'invalid_request_error', message: 'No user message found' }
      });
    }
    
    // Build the message with system prompt if provided
    const fullMessage = system 
      ? `[System: ${system}]\n\n${userMessage}`
      : userMessage;
    
    // Generate session ID for this request
    const sessionId = `bridge-${crypto.randomBytes(8).toString('hex')}`;
    
    // Call OpenClaw agent command
    const response = await callOpenClaw(sessionId, fullMessage, model);
    
    const latency = Date.now() - startTime;
    
    // Return Anthropic-format response
    res.json({
      id: `msg_${Date.now()}`,
      type: 'message',
      role: 'assistant',
      content: [{ type: 'text', text: response }],
      model: model || 'claude-sonnet-4-20250514',
      stop_reason: 'end_turn',
      usage: {
        input_tokens: Math.ceil(fullMessage.length / 4),
        output_tokens: Math.ceil(response.length / 4),
      },
      _bridge: {
        latency_ms: latency,
        source: 'openclaw-subscription'
      }
    });
    
  } catch (error) {
    console.error('Bridge error:', error);
    res.status(500).json({
      error: {
        type: 'api_error',
        message: error.message || 'Internal bridge error'
      }
    });
  }
});

/**
 * Call OpenClaw via CLI to use subscription
 */
async function callOpenClaw(sessionId, message, model) {
  return new Promise((resolve, reject) => {
    const args = [
      'agent',
      '--session-id', sessionId,
      '--message', message,
    ];
    
    // Note: openclaw agent doesn't support --model flag
    // It uses the default model from config
    
    const proc = spawn('openclaw', args, {
      timeout: 120000,
      env: { ...process.env, NO_COLOR: '1' }
    });
    
    let output = '';
    
    proc.stdout.on('data', (data) => { output += data; });
    proc.stderr.on('data', (data) => { output += data; });
    
    proc.on('close', (code) => {
      // Filter out all the noise and get the actual response
      // The response is typically the last non-noise line
      const lines = output.split('\n');
      
      const cleanLines = lines.filter(line => {
        const trimmed = line.trim();
        if (!trimmed) return false;
        if (trimmed.includes('Config warnings')) return false;
        if (trimmed.includes('plugin')) return false;
        if (trimmed.includes('Plugin')) return false;
        if (trimmed.includes('towerhq')) return false;
        if (trimmed.includes('manifest')) return false;
        if (trimmed.includes('mismatch')) return false;
        if (trimmed.includes('├')) return false;
        if (trimmed.includes('│')) return false;
        if (trimmed.includes('◇')) return false;
        if (trimmed.includes('╮')) return false;
        if (trimmed.includes('╯')) return false;
        if (trimmed.includes('─')) return false;
        if (trimmed.startsWith('[')) return false;
        if (trimmed.startsWith('\\n')) return false;
        return true;
      });
      
      const response = cleanLines.join('\n').trim();
      
      if (response) {
        resolve(response);
      } else if (code === 0) {
        resolve('Response received');
      } else {
        reject(new Error(`OpenClaw exited with code ${code}`));
      }
    });
    
    proc.on('error', (err) => {
      reject(err);
    });
  });
}

app.listen(PORT, () => {
  console.log(`OpenClaw Bridge listening on port ${PORT}`);
  console.log(`  Anthropic API: http://localhost:${PORT}/v1/messages`);
  console.log(`  Health: http://localhost:${PORT}/health`);
  console.log(`  Auth: ${BRIDGE_SECRET ? 'ENABLED (Bearer token required)' : 'DISABLED (local dev mode)'}`);
});
