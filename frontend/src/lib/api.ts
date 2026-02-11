import axios from 'axios';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:5000/api';

const api = axios.create({
  baseURL: API_URL,
  headers: {
    'Content-Type': 'application/json',
  },
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
    access_token: string;
    webhook_verify_token?: string;
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
};

export const logs = {
  get: (params?: { product_type?: string; status?: string; limit?: number; offset?: number }) =>
    api.get('/logs', { params }),
  export: (params?: { product_type?: string; status?: string; start_date?: string; end_date?: string }) =>
    api.get('/logs/export', { params, responseType: 'blob' }),
  stats: () => api.get('/logs/stats'),
};

export default api;
