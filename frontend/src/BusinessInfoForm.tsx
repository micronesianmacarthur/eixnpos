import { useState, useEffect, type ChangeEvent, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiFetch, ApiError } from './api';

interface BusinessInfo {
  company_name: string;
  phone: string;
  email: string;
  address?: string;
  website?: string;
}

interface BusinessInfoFormProps {
  onSaved?: () => void;
}

function BusinessInfoForm({ onSaved }: BusinessInfoFormProps) {
  const [formData, setFormData] = useState({
    company_name: '',
    phone: '',
    email: '',
  });
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState('');
  const navigate = useNavigate();

  useEffect(() => {
    // Fetch business info on component mount
    apiFetch<BusinessInfo>('/api/business/')
      .then((data) => {
        setFormData({
          company_name: data.company_name || '',
          phone: data.phone || '',
          email: data.email || '',
        });
      })
      .catch((err) => {
        setError(err instanceof ApiError ? err.message : 'Failed to load business info');
      });
  }, []);

  const handleChange = (e: ChangeEvent<HTMLInputElement>) => {
    const { name, value } = e.target;
    setFormData((prev) => ({ ...prev, [name]: value }));
  };

  const handleSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setIsSubmitting(true);
    setError('');
    try {
      await apiFetch<BusinessInfo>('/api/business/', {
        method: 'POST',
        body: JSON.stringify(formData),
      });
      if (onSaved) {
        onSaved();
      } else {
        navigate('/');
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Network error');
    } finally {
      setIsSubmitting(false);
    }
  };

  if (error) {
    return <p style={{ color: 'red' }}>{error}</p>;
  }

  return (
    <div className="business-form" style={{ padding: '2rem', maxWidth: '400px', margin: 'auto' }}>
      <h3>Business Information</h3>
      <form onSubmit={handleSubmit}>
        <div style={{ marginBottom: '1rem' }}>
          <label>
            Company Name:
            <input
              type="text"
              name="company_name"
              value={formData.company_name}
              onChange={handleChange}
              required
              style={{ width: '100%', padding: '0.5rem' }}
            />
          </label>
        </div>
        <div style={{ marginBottom: '1rem' }}>
          <label>
            Phone:
            <input
              type="text"
              name="phone"
              value={formData.phone}
              onChange={handleChange}
              required
              style={{ width: '100%', padding: '0.5rem' }}
            />
          </label>
        </div>
        <div style={{ marginBottom: '1rem' }}>
          <label>
            Email:
            <input
              type="email"
              name="email"
              value={formData.email}
              onChange={handleChange}
              required
              style={{ width: '100%', padding: '0.5rem' }}
            />
          </label>
        </div>
        <button
          type="submit"
          disabled={isSubmitting}
          style={{ padding: '0.5rem 1rem' }}
        >
          {isSubmitting ? 'Saving...' : 'Save Business Info'}
        </button>
      </form>
    </div>
  );
}

export default BusinessInfoForm;
