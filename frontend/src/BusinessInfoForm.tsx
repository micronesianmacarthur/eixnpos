import { useState, useEffect, type ChangeEvent, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiFetch, ApiError } from './api';
import './AuthPages.css';

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

  const header = (
    <header className="auth-header">
      <span className="auth-eyebrow">Store setup</span>
      <h2 className="auth-title">Business Information</h2>
      <p className="auth-subtitle">
        These details appear on receipts, reports, and customer documents.
      </p>
    </header>
  );

  if (error) {
    return (
      <section className="mm-card auth-card business-form">
        {header}
        <div className="mm-alert mm-alert--danger" role="alert">
          {error}
        </div>
      </section>
    );
  }

  return (
    <section className="mm-card auth-card business-form">
      {header}

      <form className="auth-form" onSubmit={handleSubmit}>
        <div className="mm-field">
          <label className="mm-label" htmlFor="business-company-name">
            Company Name:
          </label>
          <input
            id="business-company-name"
            className="mm-input"
            type="text"
            name="company_name"
            value={formData.company_name}
            onChange={handleChange}
            required
            autoComplete="organization"
          />
        </div>

        <div className="mm-field">
          <label className="mm-label" htmlFor="business-phone">
            Phone:
          </label>
          <input
            id="business-phone"
            className="mm-input"
            type="text"
            name="phone"
            value={formData.phone}
            onChange={handleChange}
            required
            autoComplete="tel"
          />
        </div>

        <div className="mm-field">
          <label className="mm-label" htmlFor="business-email">
            Email:
          </label>
          <input
            id="business-email"
            className="mm-input"
            type="email"
            name="email"
            value={formData.email}
            onChange={handleChange}
            required
            autoComplete="email"
          />
        </div>

        <button
          type="submit"
          className="mm-btn mm-btn--primary auth-submit"
          disabled={isSubmitting}
        >
          {isSubmitting ? 'Saving...' : 'Save Business Info'}
        </button>
      </form>
    </section>
  );
}

export default BusinessInfoForm;
