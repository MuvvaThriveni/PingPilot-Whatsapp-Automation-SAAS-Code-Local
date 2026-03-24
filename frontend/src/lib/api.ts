import axios from 'axios';
import { auth } from '@/lib/firebase';

export const API = process.env.NEXT_PUBLIC_API_BASE_URL || '';

const normalizeApiBaseUrl = (baseUrl: string) => {
  let url = (baseUrl || '').trim();
  if (!url) {
    return '/api';
  }

  url = url.replace(/\/+$/, '');
  if (url.endsWith('/docs')) {
    url = url.slice(0, -'/docs'.length);
    url = url.replace(/\/+$/, '');
  }

  if (url.endsWith('/api')) {
    return url;
  }

  return `${url}/api`;
};

const API_URL = normalizeApiBaseUrl(API);

const api = axios.create({
  baseURL: API_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Attach Firebase Auth ID token to every request
api.interceptors.request.use(async (config) => {
  try {
    const user = auth.currentUser;
    if (user) {
      const token = await user.getIdToken();
      config.headers.Authorization = `Bearer ${token}`;
    }
  } catch (e) {
    console.warn('Failed to get Firebase ID token:', e);
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  (error) => {
    // Normalize FastAPI error responses: {"detail": "..."} -> {"error": "..."}
    if (error.response?.data?.detail && !error.response?.data?.error) {
      error.response.data.error = error.response.data.detail;
    }
    console.error('API Error:', error.response?.data || error.message);
    return Promise.reject(error);
  }
);

export const settings = {
  getWhatsApp: () => api.get('/settings/whatsapp'),
  saveWhatsApp: (data: {
    business_account_id: string;
    phone_number_id: string;
    access_token?: string;
    webhook_verify_token?: string;
    meta_app_secret?: string;
  }) => api.post('/settings/whatsapp', data),
  testConnection: () => api.post('/settings/whatsapp/test'),
  getUsage: () => api.get('/settings/usage'),
};

export const fileForward = {
  send: (formData: FormData) =>
    api.post('/file-forward/send', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }),
  parseContacts: (formData: FormData) =>
    api.post('/file-forward/parse-contacts', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }),
  sendBulk: (formData: FormData) =>
    api.post('/file-forward/send-bulk', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }),
};

export const bulkMessage = {
  templates: () => api.get('/bulk-message/templates'),
  parse: (formData: FormData) =>
    api.post('/bulk-message/parse', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }),
  start: (formData: FormData) =>
    api.post('/bulk-message/start', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }),
  stop: (campaignId: string) => api.post(`/bulk-message/stop/${campaignId}`),
  status: (campaignId: string) => api.get(`/bulk-message/status/${campaignId}`),
  deleteCampaign: (campaignId: string) => api.delete(`/bulk-message/campaigns/${campaignId}`),
  campaigns: () => api.get('/bulk-message/campaigns'),
  details: (campaignId: string) => api.get(`/bulk-message/campaigns/${campaignId}/details`),
  resendFailed: (campaignId: string) => api.post(`/bulk-message/campaigns/${campaignId}/resend-failed`),
};

export const chatbot = {
  getSettings: () => api.get('/chatbot/settings'),
  updateSettings: (data: { 
    is_enabled: boolean; 
    fallback_message: string;
    use_ai?: boolean;
    ai_system_prompt?: string;
    openai_api_key?: string;
  }) => api.put('/chatbot/settings', data),
  getRules: () => api.get('/chatbot/rules'),
  createRule: (data: { keyword: string; response: string; priority?: number }) =>
    api.post('/chatbot/rules', data),
  updateRule: (id: number, data: { keyword?: string; response?: string; is_active?: boolean; priority?: number }) =>
    api.put(`/chatbot/rules/${id}`, data),
  deleteRule: (id: number) => api.delete(`/chatbot/rules/${id}`),
  getConversations: () => api.get('/chatbot/conversations'),
  getUsers: () => api.get('/chatbot/users'),
  getUserConversations: (phone: string) => api.get(`/chatbot/conversations/${phone}`),
};

export const logs = {
  get: (params?: { product_type?: string; status?: string; limit?: number; offset?: number }) =>
    api.get('/logs', { params }),
  export: (params?: { product_type?: string; status?: string; start_date?: string; end_date?: string }) =>
    api.get('/logs/export', { params, responseType: 'blob' }),
  stats: () => api.get('/logs/stats'),
};

export default api;
