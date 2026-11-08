import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';

function BusinessInfoForm() {
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
    fetch('/api/business/')
      .then((res) => res.json())
      .then((data) => {
        setFormData({
          company_name: data.company_name || '',
          phone: data.phone || '',
          email: data.email || '',
        });
      })
      .catch((err) => {
        setError('Failed to load business info');
      });
  }, []);

  const handleChange = (e) => {
    const { name, value } = e.target;
    setFormData((prev) => ({ ...prev, [name]: value }));
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setIsSubmitting(true);
    setError('');
    try {
      const response = await fetch('/api/business/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(formData),
      });

      if (response.ok) {
        navigate('/');
      } else {
        const errorData = await response.json();
        setError(errorData.error || 'Failed to save business info');
      }
    } catch (err) {
      setError('Network error');
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